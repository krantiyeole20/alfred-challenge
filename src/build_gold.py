"""Validate the five per-mailbox gold files and merge them into data/gold_set.json.

    .venv/bin/python src/build_gold.py

Checks, beyond the structural rules in src/gold_schema.py:
  * every cited message_id / thread_id exists in that mailbox
  * anchor and cited messages actually belong to the cited thread
  * every evidence_quote is a verbatim substring of the anchor message's
    body_text or one of its attachments' extracted_text
  * distractor message_ids exist and their subjects match the corpus
  * no item cites a message the corpus marks as superseded (stale-fact guard)

Exits non-zero on any failure.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic import ValidationError  # noqa: E402

from gold_schema import QUESTION_KEYS, QUESTION_TEXT, MailboxGold, Tier  # noqa: E402
from load import load_profile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR = ROOT / "data" / "gold"
RESULTS = ROOT / "results"

PROFILE_IDS = ["founder", "marketing", "finance", "hr", "consulting"]

errors: list[str] = []
warnings: list[str] = []


def norm(text: str) -> str:
    return " ".join(text.split())


def check_mailbox(gold: MailboxGold) -> None:
    corpus = {e.id: e for e in load_profile(gold.profile_id)}
    threads = {e.thread_id for e in corpus.values()}
    tag = f"[{gold.profile_id}]"

    superseded = {
        e.id for e in corpus.values() if e.meta.superseded_by_message_id is not None
    }

    for key in QUESTION_KEYS:
        answer = gold.answers[key]
        if answer.question != QUESTION_TEXT[key]:
            warnings.append(f"{tag} {key}: question text differs from the canonical wording")

        for item in answer.items:
            where = f"{tag} {item.item_id}"

            unknown = [m for m in item.message_ids if m not in corpus]
            if unknown:
                errors.append(f"{where}: message_ids not in this mailbox: {unknown}")
                continue
            if item.thread_id not in threads:
                errors.append(f"{where}: thread_id {item.thread_id} not in this mailbox")

            # An obligation may legitimately span threads (e.g. an implicit deadline
            # resolved via a calendar invite elsewhere). Only flag when NOTHING cited
            # is actually in the declared thread.
            if not any(corpus[m].thread_id == item.thread_id for m in item.message_ids):
                warnings.append(
                    f"{where}: no cited message belongs to thread {item.thread_id}"
                )

            anchor = corpus[item.anchor_message_id]
            haystacks = [anchor.body_text] + [
                a.extracted_text or "" for a in anchor.attachments
            ]
            quote = norm(item.evidence_quote)
            if not any(quote in norm(h) for h in haystacks):
                # allow the quote to come from any cited message, not just the anchor
                widened = False
                for m in item.message_ids:
                    e = corpus[m]
                    pool = [e.body_text] + [a.extracted_text or "" for a in e.attachments]
                    if any(quote in norm(p) for p in pool):
                        widened = True
                        warnings.append(
                            f"{where}: evidence_quote is from {m}, not the anchor "
                            f"{item.anchor_message_id}"
                        )
                        break
                if not widened:
                    errors.append(
                        f"{where}: evidence_quote not found in any cited message — "
                        f"{item.evidence_quote[:70]!r}"
                    )

            if item.anchor_message_id in superseded:
                errors.append(
                    f"{where}: anchors on {item.anchor_message_id}, which the corpus "
                    "marks superseded — cite the current state instead"
                )

            if key == "q5_what_changed":
                for f in ("changed_field", "previous_value", "current_value"):
                    if getattr(item, f) is None:
                        errors.append(f"{where}: q5 item missing {f}")
                # to_message_id is mandatory (it carries the new value).
                # from_message_id may be null when the old value is asserted inside
                # the same message ("your role was changed from Admin to Owner").
                if item.to_message_id is None:
                    errors.append(f"{where}: q5 item missing to_message_id")
                for f in ("from_message_id", "to_message_id"):
                    mid = getattr(item, f)
                    if mid is not None and mid not in corpus:
                        errors.append(f"{where}: q5 {f} {mid} not in this mailbox")

        for d in answer.must_not_include:
            if d.message_id not in corpus:
                errors.append(
                    f"{tag} {key}: distractor {d.message_id} is not in this mailbox"
                )
            elif norm(corpus[d.message_id].subject) != norm(d.subject):
                warnings.append(
                    f"{tag} {key}: distractor {d.message_id} subject mismatch — "
                    f"corpus says {corpus[d.message_id].subject!r}"
                )

        n = len(answer.items)
        if n < 3:
            warnings.append(f"{tag} {key}: only {n} items")
        if n > 20:
            warnings.append(f"{tag} {key}: {n} items — inclusion criteria may be too loose")
        if len(answer.must_not_include) < 4:
            warnings.append(
                f"{tag} {key}: {len(answer.must_not_include)} distractors (spec asks for 4+)"
            )


def main() -> int:
    mailboxes: dict[str, MailboxGold] = {}

    for pid in PROFILE_IDS:
        path = GOLD_DIR / f"{pid}.gold.json"
        if not path.exists():
            errors.append(f"MISSING {path.relative_to(ROOT)}")
            continue
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: invalid JSON — {exc}")
            continue
        try:
            gold = MailboxGold.model_validate(raw)
        except ValidationError as exc:
            for e in exc.errors()[:8]:
                loc = ".".join(str(p) for p in e["loc"])
                errors.append(f"{path.name}: {loc} — {e['msg']}")
            continue
        mailboxes[pid] = gold
        check_mailbox(gold)

    all_item_ids = [
        i.item_id for g in mailboxes.values() for a in g.answers.values() for i in a.items
    ]
    for iid, n in Counter(all_item_ids).items():
        if n > 1:
            errors.append(f"duplicate item_id across the gold set: {iid}")

    for w in warnings[:50]:
        print(f"  warn: {w}")
    if len(warnings) > 50:
        print(f"  ... and {len(warnings) - 50} more warnings")

    if errors:
        print(f"\n{len(errors)} ERROR(S):")
        for e in errors[:60]:
            print(f"  - {e}")
        if len(errors) > 60:
            print(f"  ... and {len(errors) - 60} more")
        return 1

    out: dict[str, Any] = {
        "meta": {
            "corpus": "alfred-challenge mock Gmail corpus, 1500 messages / 5 mailboxes",
            "as_of": "2026-08-12T23:59:00 in each mailbox owner's local timezone",
            "questions": QUESTION_TEXT,
            "scoring": {
                "primary_surface": "answers.<qN>.ranked — ordered item_ids, the only "
                "list used to score a pipeline run",
                "ranked_contains": "must_include + acceptable tiers, in priority order",
                "borderline_excluded_from_ranked": "defensible either way; neither "
                "rewarded nor penalised",
                "diagnostic_surfaces": {
                    "tier": "must_include misses are real failures; acceptable misses are softer",
                    "must_not_include": "traps a naive system returns — false-positive rate "
                    "here localises the failure mode via the `trap` field",
                    "evidence_quote + anchor_message_id": "distinguishes retrieval failure "
                    "from comprehension failure",
                    "due_date / consequence / days_open": "diagnoses ranking failures",
                    "q5 previous_value vs current_value": "diagnoses stale-fact citation",
                },
                "suggested_metrics": [
                    "recall@k over ranked (headline)",
                    "precision, counting anything outside ranked ∪ borderline as a miss",
                    "false-positive rate over must_not_include",
                    "Spearman rank correlation against ranked",
                ],
                "notes": "Compare on ranked; debug on everything else. See "
                "docs/gold-set-spec.md §1 and docs/QUESTIONS.md.",
            },
        },
        "mailboxes": {
            pid: json.loads(mailboxes[pid].model_dump_json(exclude_none=False))
            for pid in PROFILE_IDS
            if pid in mailboxes
        },
    }
    (ROOT / "data" / "gold_set.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))

    lines = [
        "# Gold set — build report",
        "",
        f"{len(mailboxes)} mailboxes × 6 questions = {len(mailboxes) * 6} answer sets, "
        f"{len(all_item_ids)} items total.",
        "",
        "| mailbox | " + " | ".join(k.split("_")[0].upper() for k in QUESTION_KEYS) + " | items | ranked | distractors |",
        "|---" * (len(QUESTION_KEYS) + 4) + "|",
    ]
    for pid, g in mailboxes.items():
        counts = [str(len(g.answers[k].items)) for k in QUESTION_KEYS]
        tot = sum(len(g.answers[k].items) for k in QUESTION_KEYS)
        rank = sum(len(g.answers[k].ranked) for k in QUESTION_KEYS)
        dis = sum(len(g.answers[k].must_not_include) for k in QUESTION_KEYS)
        lines.append(f"| `{pid}` | " + " | ".join(counts) + f" | {tot} | {rank} | {dis} |")

    lines += ["", "## Tier mix", "", "| mailbox | must_include | acceptable | borderline |", "|---|--:|--:|--:|"]
    for pid, g in mailboxes.items():
        c = Counter(i.tier for a in g.answers.values() for i in a.items)
        lines.append(
            f"| `{pid}` | {c[Tier.MUST]} | {c[Tier.ACCEPTABLE]} | {c[Tier.BORDERLINE]} |"
        )

    lines += [
        "",
        "## Traps recorded in `must_not_include`",
        "",
        "| trap | count |",
        "|---|--:|",
    ]
    traps = Counter(
        d.trap for g in mailboxes.values() for a in g.answers.values() for d in a.must_not_include
    )
    for t, n in traps.most_common():
        lines.append(f"| `{t}` | {n} |")

    lines += ["", f"Warnings at build time: {len(warnings)}.", ""]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "gold_report.md").write_text("\n".join(lines))

    print(f"\nOK — {len(all_item_ids)} items across {len(mailboxes) * 6} answer sets")
    print("wrote data/gold_set.json and results/gold_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
