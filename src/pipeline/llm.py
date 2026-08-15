"""Gemini client wrapper: retries, model fallback, cost accounting, budget cap.

The extraction pass makes ~1,400 calls. Three things have to be true for that
to be safe to leave running unattended:

  1. Transient failures retry rather than aborting the run.
  2. A model outage or a bad model id falls back rather than failing every
     remaining email.
  3. A prompt bug cannot spend unbounded money -- the run aborts at a ceiling.
"""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types as gt
from google.genai import errors as gerrors

from . import config


class BudgetExceeded(RuntimeError):
    """Raised when a run would push spend past ALFRED_BUDGET_USD."""


@dataclass
class Usage:
    """Thread-safe running total for one pipeline run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    retries: int = 0
    fallbacks: int = 0
    by_model: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, model: str, in_tok: int, out_tok: int) -> float:
        tier = config.tier_for_model(model)
        cost = tier.cost(in_tok, out_tok)
        with self._lock:
            self.input_tokens += in_tok
            self.output_tokens += out_tok
            self.cost_usd += cost
            self.calls += 1
            self.by_model[model] = self.by_model.get(model, 0) + 1
            total = self.cost_usd
        if total > config.BUDGET_USD:
            raise BudgetExceeded(
                f"spend ${total:.2f} exceeded ALFRED_BUDGET_USD=${config.BUDGET_USD:.2f} "
                f"after {self.calls} calls. Raise the cap in .env to continue."
            )
        return cost

    def summary(self) -> str:
        return (
            f"{self.calls} calls, {self.input_tokens:,} in / {self.output_tokens:,} out "
            f"tokens, ${self.cost_usd:.3f}, {self.retries} retries, "
            f"{self.fallbacks} fallbacks, models={self.by_model}"
        )


# HTTP statuses worth retrying: transient by nature. A 400 is a bug in our
# request and retrying it just burns time, so it is not in this set.
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}


def _status_of(exc: Exception) -> int | None:
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


class LLM:
    def __init__(self, model: str | None = None, fallback: str | None = None):
        self.client = genai.Client(api_key=config.require_api_key())
        self.model = model or config.PRIMARY_MODEL
        self.fallback = fallback or config.FALLBACK_MODEL
        self.usage = Usage()

    # ------------------------------------------------------------------ #

    def list_models(self) -> list[str]:
        out = []
        for m in self.client.models.list():
            name = (m.name or "").removeprefix("models/")
            actions = getattr(m, "supported_actions", None) or []
            if not actions or "generateContent" in actions:
                out.append(name)
        return sorted(set(out))

    # ------------------------------------------------------------------ #

    def structured(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str = "result",
        max_output_tokens: int = 4096,
    ) -> dict[str, Any]:
        """One structured-output call, with retries and model fallback.

        Returns the parsed object. Raises the last error if every attempt on
        both models fails.
        """
        models = [self.model, self.fallback] if self.fallback != self.model else [self.model]
        last_error: Exception | None = None

        cfg = gt.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=max_output_tokens,
            temperature=0,
        )

        for model_idx, model in enumerate(models):
            if model_idx:
                self.usage.fallbacks += 1
            for attempt in range(config.MAX_RETRIES):
                try:
                    resp = self.client.models.generate_content(
                        model=model, contents=user, config=cfg
                    )
                    meta = getattr(resp, "usage_metadata", None)
                    self.usage.add(
                        model,
                        getattr(meta, "prompt_token_count", 0) or 0,
                        (getattr(meta, "candidates_token_count", 0) or 0)
                        + (getattr(meta, "thoughts_token_count", 0) or 0),
                    )
                    text = (resp.text or "").strip()
                    if not text:
                        raise ValueError("empty response body")
                    return json.loads(text)

                except BudgetExceeded:
                    raise  # never retry past the ceiling

                except (gerrors.APIError, gerrors.ServerError) as exc:
                    last_error = exc
                    status = _status_of(exc)
                    if status in _RETRY_STATUS or status is None:
                        self.usage.retries += 1
                        self._sleep(attempt)
                    else:
                        # 4xx other than rate limit: our request is wrong, or the
                        # model id does not exist. Go to the fallback model
                        # rather than hammering the same bad request.
                        break

                except (json.JSONDecodeError, ValueError) as exc:
                    # Structured output should make this impossible, but a
                    # truncated or safety-filtered response still lands here.
                    last_error = exc
                    self.usage.retries += 1
                    self._sleep(attempt)

                except Exception as exc:  # noqa: BLE001 - transport-level
                    last_error = exc
                    self.usage.retries += 1
                    self._sleep(attempt)

        raise RuntimeError(f"all attempts failed on {models}: {last_error}") from last_error

    @staticmethod
    def _sleep(attempt: int) -> None:
        delay = min(config.RETRY_BASE_DELAY * (2**attempt), config.RETRY_MAX_DELAY)
        time.sleep(delay * (0.5 + random.random()))  # full jitter


def resolve_models(verbose: bool = True) -> tuple[str, str]:
    """Confirm the configured model ids exist on this account.

    Checks the configured ids against the live model list and picks the closest
    available match rather than failing a 1,400-call run on the first request.
    """
    llm = LLM()
    available = set(llm.list_models())

    def pick(preferred: str) -> str:
        if preferred in available:
            return preferred
        # Same family, e.g. gemini-3.5-flash-lite -> gemini-3.5-flash-lite-001
        prefix = preferred.rsplit("-", 1)[0] if preferred[-1].isdigit() else preferred
        candidates = sorted(m for m in available if m.startswith(prefix))
        if not candidates:
            candidates = sorted(m for m in available if m.startswith(preferred))
        if candidates:
            if verbose:
                print(f"  {preferred!r} not found; using {candidates[0]!r}")
            return candidates[0]
        raise SystemExit(
            f"Model {preferred!r} is not available on this account.\n"
            "Set GEMINI_MODEL in .env to one of:\n  "
            + "\n  ".join(sorted(m for m in available if "gemini" in m)[:40])
        )

    return pick(config.PRIMARY_MODEL), pick(config.FALLBACK_MODEL)
