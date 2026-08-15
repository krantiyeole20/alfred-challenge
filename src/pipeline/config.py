"""Configuration: paths, model tiers, pricing, and versions.

Everything tunable lives here so no other module reads os.environ directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
PROFILES = DATA / "profiles"
GOLD = DATA / "gold"
RESULTS = ROOT / "results"
BUILD = ROOT / "build"

DB_PATH = Path(os.getenv("ALFRED_DB", BUILD / "alfred.db"))
SCHEMA_SQL = Path(__file__).resolve().parent / "schema_sqlite.sql"

PROFILE_IDS = ["founder", "marketing", "finance", "hr", "consulting"]

# The corpus is frozen at this moment; "overdue" and "stale" are relative to it.
# Every mailbox's window ends 2026-08-12, per docs/QUESTIONS.md.
AS_OF = os.getenv("ALFRED_AS_OF", "2026-08-12T23:59:59")

# --------------------------------------------------------------------------- #
# Versions -- bumped when the corresponding stage's logic changes, so rows
# written by different logic stay distinguishable in the ledger.
# --------------------------------------------------------------------------- #

NORMALIZER_VERSION = "norm-0.1"
RESOLVER_VERSION = "resolve-0.1"
CLUSTERING_VERSION = "cluster-0.1"
SIGNAL_VERSION = "signal-0.1"
EXTRACTOR_VERSION = "extract-0.1"
REDUCER_VERSION = "reduce-0.1"
ATTENTION_VERSION = "attn-0.1"

# --------------------------------------------------------------------------- #
# Models (Google Gemini)
#
# Prices are USD per 1M tokens, current as of 2026-08. Batch mode is 50% off
# across the board. Model ids are confirmed against the live account with
# `python -m src.pipeline.run list-models` before any paid run rather than
# trusted from the pricing pages.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Tier:
    name: str
    model_id: str
    input_per_mtok: float
    output_per_mtok: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000 * self.input_per_mtok
            + output_tokens / 1_000_000 * self.output_per_mtok
        )


TIERS = {
    # retires 2026-10-16; fine for a one-off batch, not for the live demo
    "lite-2.5": Tier("lite-2.5", "gemini-2.5-flash-lite", 0.10, 0.40),
    "lite-3.1": Tier("lite-3.1", "gemini-3.1-flash-lite", 0.25, 1.50),
    "lite-3.5": Tier("lite-3.5", "gemini-3.5-flash-lite", 0.30, 2.50),
    "flash-3.6": Tier("flash-3.6", "gemini-3.6-flash", 1.50, 7.50),
    "pro-3.1": Tier("pro-3.1", "gemini-3.1-pro", 2.00, 12.00),
}

# 3.5-flash-lite for the bulk pass (~$3 over the full corpus), 3.6-flash as the
# fallback: genuinely stronger, so a retry after repeated failure also buys a
# better shot at the adversarial cases rather than just being a spare.
PRIMARY_TIER = os.getenv("ALFRED_TIER", "lite-3.5")
FALLBACK_TIER = os.getenv("ALFRED_FALLBACK_TIER", "flash-3.6")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

# Model id overrides win over the tier defaults above.
PRIMARY_MODEL = os.getenv("GEMINI_MODEL") or TIERS[PRIMARY_TIER].model_id
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL") or TIERS[FALLBACK_TIER].model_id

# --------------------------------------------------------------------------- #
# Retry / concurrency / budget
# --------------------------------------------------------------------------- #

MAX_RETRIES = int(os.getenv("ALFRED_MAX_RETRIES", "5"))
RETRY_BASE_DELAY = float(os.getenv("ALFRED_RETRY_BASE_DELAY", "1.0"))
RETRY_MAX_DELAY = float(os.getenv("ALFRED_RETRY_MAX_DELAY", "60.0"))
REQUEST_TIMEOUT = float(os.getenv("ALFRED_REQUEST_TIMEOUT", "120.0"))
CONCURRENCY = int(os.getenv("ALFRED_CONCURRENCY", "8"))

# Hard ceiling on a single run. The pipeline aborts rather than silently
# spending past this, so a prompt bug cannot run up a bill unattended.
BUDGET_USD = float(os.getenv("ALFRED_BUDGET_USD", "10.00"))


def tier_for_model(model_id: str) -> Tier:
    """Find the pricing tier for a model id, for cost accounting."""
    for tier in TIERS.values():
        if tier.model_id == model_id:
            return tier
    # An overridden/unknown model still needs a cost basis; assume the most
    # expensive tier so estimates are conservative rather than optimistic.
    return Tier(f"unknown:{model_id}", model_id, 5.00, 30.00)


def require_api_key() -> str:
    if not GEMINI_API_KEY:
        raise SystemExit(
            "GEMINI_API_KEY is not set.\n"
            f"Create {ROOT / '.env'} containing:\n\n"
            "    GEMINI_API_KEY=AIza...\n"
        )
    return GEMINI_API_KEY
