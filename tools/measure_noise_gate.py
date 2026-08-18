#!/usr/bin/env python3
"""Measure what each candidate noise gate would cost in lost work.

The gate decides what never reaches the extractor, so a rule that is too
eager silently deletes real obligations and no downstream stage can recover
them. This re-runs every candidate rule over the whole corpus and reports
what each would have thrown away, so the choice in signals.py is a measured
one rather than a guess.

"Real work lost" is counted against the gold sets: a gold item whose source
email the rule would gate is an obligation the pipeline could never surface.

    python tools/measure_noise_gate.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pipeline import config  # noqa: E402
from src.pipeline.db import connect  # noqa: E402
from src.pipeline.signals import _TRANSACTIONAL  # noqa: E402

# Cost per extracted email, taken from the recorded run rather than a list
# price, so the comparison reflects what this corpus actually cost.
COST_PER_EMAIL = 0.716 / 1367


def variants(sig: dict) -> dict[str, bool]:
    """Would each candidate rule gate this email? True = dropped."""
    cat = sig["category"]
    auto = bool(sig["is_automated"])
    listid = bool(sig["list_id"])
    bulk = bool(sig["bulk"])
    subject = sig["subject"] or ""
    transactional = bool(_TRANSACTIONAL.search(subject))

    return {
        "category OR bulk headers": (
            cat in ("CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES")
            or bulk
            or listid
        ),
        "List-ID OR (promo AND automated)": (
            listid or (cat in ("CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL") and auto)
        ),
        "shipped: + transactional override": (
            listid
            or (
                not transactional
                and cat in ("CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL")
                and auto
            )
        ),
        "transactional checked FIRST": (
            not transactional
            and (listid or (cat in ("CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL") and auto))
        ),
        "List-ID only": listid,
        "no gate": False,
    }


def main() -> None:
    conn = connect()

    rows = conn.execute(
        """
        SELECT e.id, e.subject, e.provider_message_id, e.category,
               s.is_automated
        FROM emails e JOIN email_signals s ON s.email_id = e.id
        """
    ).fetchall()

    # List-ID and bulk headers live in the corpus, not the database: the
    # loader consumes them to compute is_automated and does not keep them.
    # Read them back from source so this measurement stands on the same
    # evidence the gate would have seen.
    headers: dict[str, dict] = {}
    for path in sorted((ROOT / "data" / "profiles").glob("*.canonical.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            extra = {k.lower(): v for k, v in (rec.get("extra_headers") or {}).items()}
            headers[rec["id"]] = {
                "list_id": bool(extra.get("list-id") or extra.get("list-unsubscribe")),
                "bulk": (extra.get("precedence", "").lower() in {"bulk", "list", "junk"})
                or bool(extra.get("x-campaign-id") or extra.get("x-mailer-type")),
            }

    # Gold items point at source messages; a gated source is an unrecoverable
    # miss, so that is what "real work lost" has to mean.
    gold_msg_ids: set[str] = set()
    gold_dir = ROOT / "data" / "gold"
    for path in sorted(gold_dir.glob("*.gold.json")):
        blob = json.loads(path.read_text())
        for answer in blob.get("answers", {}).values():
            for item in answer.get("items", []) or []:
                if item.get("anchor_message_id"):
                    gold_msg_ids.add(item["anchor_message_id"])
                for mid in item.get("message_ids") or []:
                    gold_msg_ids.add(mid)

    gated = defaultdict(int)
    lost = defaultdict(int)
    lost_examples = defaultdict(list)

    for r in rows:
        sig = dict(r)
        sig.update(headers.get(r["provider_message_id"], {"list_id": False, "bulk": False}))
        for name, drops in variants(sig).items():
            if not drops:
                continue
            gated[name] += 1
            if r["provider_message_id"] in gold_msg_ids:
                lost[name] += 1
                if len(lost_examples[name]) < 3:
                    lost_examples[name].append((r["subject"] or "")[:64])

    total = len(rows)
    order = [
        "category OR bulk headers",
        "List-ID OR (promo AND automated)",
        "shipped: + transactional override",
        "transactional checked FIRST",
        "List-ID only",
        "no gate",
    ]

    print(f"corpus: {total} emails · {len(gold_msg_ids)} distinct gold source messages")
    print(f"cost basis: ${COST_PER_EMAIL:.6f} per extracted email (from the recorded run)\n")
    print(f"{'gate rule':38} {'lost':>5} {'gated':>6} {'cost':>8}")
    print("-" * 60)
    for name in order:
        cost = (total - gated[name]) * COST_PER_EMAIL
        print(f"{name:38} {lost[name]:>5} {gated[name]:>6} {cost:>8.2f}")
    print("-" * 60)

    for name in order:
        if lost_examples[name]:
            print(f"\nreal work {name!r} would have dropped:")
            for s in lost_examples[name]:
                print(f"  · {s}")

    conn.close()


if __name__ == "__main__":
    main()
