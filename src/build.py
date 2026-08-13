"""Validate the generated batches, then assemble the shipping corpus.

    .venv/bin/python src/build.py

Reads   data/raw/<profile>_b<N>.json      (authored by the generation subagents)
Writes  data/profiles/<profile>.canonical.jsonl
        data/profiles/<profile>.gmail.jsonl
        data/profiles/<profile>.threads.json
        data/ground_truth.jsonl
        results/stats.json
        results/corpus_report.md

Exits non-zero if any integrity check fails.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic import ValidationError  # noqa: E402

from schema import (  # noqa: E402
    Email,
    Folder,
    TrickyTag,
    compute_size_estimate,
    to_gmail_api,
    to_gmail_thread,
)

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROFILES = ROOT / "data" / "profiles"
RESULTS = ROOT / "results"

PROFILE_IDS = ["founder", "marketing", "finance", "hr", "consulting"]
BATCHES = [1, 2, 3, 4, 5, 6]

WINDOW_START = datetime(2026, 6, 13, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 13, tzinfo=timezone.utc)
RECENT_CUTOFF = datetime(2026, 7, 13, tzinfo=timezone.utc)

REQUIRED_TAGS = [
    TrickyTag.FORWARDED_TASK_NOT_USERS,
    TrickyTag.SAME_PERSON_TWO_ADDRESSES,
    TrickyTag.CONDITIONAL_PROMISE,
    TrickyTag.SHIFTING_NUMBER,
    TrickyTag.DIFFUSED_GROUP_ASK,
    TrickyTag.NEWSLETTER_UNSUBSCRIBE,
    TrickyTag.OUT_OF_OFFICE,
    TrickyTag.LONG_THREAD,
    TrickyTag.TOPIC_DRIFT,
    TrickyTag.PROMISED_ATTACHMENT,
    TrickyTag.DELIVERED_ATTACHMENT,
]

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


# --------------------------------------------------------------------------- #
# Load + per-record validation
# --------------------------------------------------------------------------- #


def load() -> list[Email]:
    emails: list[Email] = []
    for pid in PROFILE_IDS:
        for b in BATCHES:
            path = RAW / f"{pid}_b{b}.json"
            if not path.exists():
                err(f"MISSING FILE {path.relative_to(ROOT)}")
                continue
            try:
                records = json.loads(path.read_text())
            except json.JSONDecodeError as exc:
                err(f"{path.name}: invalid JSON — {exc}")
                continue
            if not isinstance(records, list):
                err(f"{path.name}: top level must be a JSON array")
                continue
            if len(records) != 50:
                warn(f"{path.name}: {len(records)} records (expected 50)")
            for i, rec in enumerate(records):
                try:
                    email = Email.model_validate(rec)
                except ValidationError as exc:
                    first = exc.errors()[0]
                    loc = ".".join(str(p) for p in first["loc"])
                    err(f"{path.name}[{i}] {rec.get('id', '?')}: {loc} — {first['msg']}")
                    continue
                if email.profile_id != pid:
                    err(f"{path.name}[{i}]: profile_id {email.profile_id!r} != {pid!r}")
                emails.append(email)
    return emails


# --------------------------------------------------------------------------- #
# Corpus-wide integrity
# --------------------------------------------------------------------------- #


def check_corpus(emails: list[Email]) -> None:
    by_id: dict[str, Email] = {}
    for e in emails:
        if e.id in by_id:
            err(f"duplicate id {e.id} ({e.profile_id} / {by_id[e.id].profile_id})")
        by_id[e.id] = e

    mids: dict[str, str] = {}
    for e in emails:
        if e.message_id_header in mids:
            err(f"duplicate Message-ID {e.message_id_header} on {e.id} and {mids[e.message_id_header]}")
        mids[e.message_id_header] = e.id

    # --- threads --------------------------------------------------------- #
    threads: dict[str, list[Email]] = defaultdict(list)
    for e in emails:
        threads[e.thread_id].append(e)

    for tid, msgs in threads.items():
        msgs.sort(key=lambda m: m.thread_position)
        if len({m.profile_id for m in msgs}) > 1:
            err(f"thread {tid} spans multiple profiles")
        declared = {m.thread_length for m in msgs}
        if len(declared) > 1:
            err(f"thread {tid}: inconsistent thread_length {sorted(declared)}")
        elif msgs[0].thread_length != len(msgs):
            err(f"thread {tid}: thread_length {msgs[0].thread_length} but {len(msgs)} messages present")
        positions = [m.thread_position for m in msgs]
        if positions != list(range(1, len(msgs) + 1)):
            err(f"thread {tid}: thread_position sequence is {positions}")
        if tid not in by_id:
            err(f"thread {tid}: no message carries that id as its own id")
        elif by_id[tid].thread_position != 1:
            err(f"thread {tid}: root message is not at thread_position 1")

        local_mids = {m.message_id_header: m for m in msgs}
        for m in msgs:
            if m.thread_position == 1:
                continue
            parent = local_mids.get(m.in_reply_to or "")
            if parent is None:
                err(f"{m.id}: In-Reply-To {m.in_reply_to} not found inside thread {tid}")
                continue
            if parent.thread_position >= m.thread_position:
                err(f"{m.id}: replies to a message at a later position")
            if m.date <= parent.date:
                err(f"{m.id}: dated at or before its parent {parent.id}")
            expected = parent.references + [parent.message_id_header]
            if m.references != expected:
                err(
                    f"{m.id}: References chain wrong — has {len(m.references)} entries, "
                    f"expected {len(expected)} (parent's References + parent's Message-ID)"
                )

    # --- dates ------------------------------------------------------------ #
    for e in emails:
        d = e.date.astimezone(timezone.utc)
        if not (WINDOW_START <= d < WINDOW_END):
            err(f"{e.id}: date {e.date.isoformat()} outside the 2026-06-13..2026-08-12 window")
        drift = abs(e.internal_date_ms / 1000 - d.timestamp())
        if drift > 120:
            err(f"{e.id}: internal_date_ms drifts {drift:.0f}s from the Date header")

    for pid in PROFILE_IDS:
        pe = [e for e in emails if e.profile_id == pid]
        if not pe:
            continue
        recent = sum(1 for e in pe if e.date.astimezone(timezone.utc) >= RECENT_CUTOFF)
        pct = recent / len(pe)
        if pct < 0.70:
            warn(f"{pid}: only {pct:.0%} of mail is in the recent 30 days (target 75%)")

    # --- required tricky coverage, per profile ----------------------------- #
    for pid in PROFILE_IDS:
        tags = Counter()
        for e in emails:
            if e.profile_id == pid:
                tags.update(e.meta.tricky_tags)
        for tag in REQUIRED_TAGS:
            if tags[tag] == 0:
                err(f"{pid}: required tricky case {tag.value!r} is missing")
        if tags[TrickyTag.SHIFTING_NUMBER] < 3:
            err(f"{pid}: shifting_number needs 3+ messages, found {tags[TrickyTag.SHIFTING_NUMBER]}")
        if tags[TrickyTag.LONG_THREAD] < 8:
            err(f"{pid}: long_thread needs 8+ messages, found {tags[TrickyTag.LONG_THREAD]}")

    # --- semantic spot checks ---------------------------------------------- #
    for e in emails:
        if TrickyTag.DELIVERED_ATTACHMENT in e.meta.tricky_tags and not e.attachments:
            err(f"{e.id}: tagged delivered_attachment but carries no attachment")
        if TrickyTag.PROMISED_ATTACHMENT in e.meta.tricky_tags and e.attachments:
            err(f"{e.id}: tagged promised_attachment but already has the attachment")
        if TrickyTag.NEWSLETTER_UNSUBSCRIBE in e.meta.tricky_tags:
            if "List-Unsubscribe" not in e.extra_headers:
                err(f"{e.id}: newsletter_unsubscribe without a List-Unsubscribe header")
        if TrickyTag.OUT_OF_OFFICE in e.meta.tricky_tags and e.folder is not Folder.SENT:
            if e.extra_headers.get("Auto-Submitted") != "auto-replied":
                err(f"{e.id}: out_of_office without Auto-Submitted: auto-replied")
        if e.meta.commitment_is_conditional and not e.meta.commitment_condition:
            err(f"{e.id}: conditional commitment with no commitment_condition")
        if e.meta.superseded_by_message_id and e.meta.superseded_by_message_id not in mids:
            err(f"{e.id}: superseded_by_message_id points at an unknown Message-ID")
        if e.snippet and e.snippet[:20].strip() not in " ".join(e.body_text.split())[:80]:
            warn(f"{e.id}: snippet does not appear to be drawn from body_text")


# --------------------------------------------------------------------------- #
# Normalisation + emit
# --------------------------------------------------------------------------- #


def normalise(emails: list[Email]) -> None:
    """Make history_id monotonic per profile and backfill size_estimate."""
    for pid in PROFILE_IDS:
        pe = sorted(
            (e for e in emails if e.profile_id == pid), key=lambda e: e.internal_date_ms
        )
        for n, e in enumerate(pe, start=1):
            e.history_id = str(1_000_000 + n * 7)
    for e in emails:
        if not e.size_estimate:
            e.size_estimate = compute_size_estimate(e)


def emit(emails: list[Email]) -> dict[str, Any]:
    PROFILES.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)

    stats: dict[str, Any] = {"generated_at": "2026-08-12", "total": len(emails), "profiles": {}}
    gt_lines: list[str] = []

    for pid in PROFILE_IDS:
        pe = sorted(
            (e for e in emails if e.profile_id == pid), key=lambda e: e.internal_date_ms
        )
        if not pe:
            continue

        (PROFILES / f"{pid}.canonical.jsonl").write_text(
            "\n".join(e.model_dump_json(by_alias=True) for e in pe) + "\n"
        )
        (PROFILES / f"{pid}.gmail.jsonl").write_text(
            "\n".join(json.dumps(to_gmail_api(e), ensure_ascii=False) for e in pe) + "\n"
        )

        threads: dict[str, list[Email]] = defaultdict(list)
        for e in pe:
            threads[e.thread_id].append(e)
        (PROFILES / f"{pid}.threads.json").write_text(
            json.dumps(
                [to_gmail_thread(v) for v in threads.values()], indent=2, ensure_ascii=False
            )
        )

        for e in pe:
            if e.meta.tricky_tags:
                gt_lines.append(
                    json.dumps(
                        {
                            "profile_id": pid,
                            "id": e.id,
                            "thread_id": e.thread_id,
                            "date": e.date.isoformat(),
                            "subject": e.subject,
                            "tricky_tags": [t.value for t in e.meta.tricky_tags],
                            "email_class": e.meta.email_class.value,
                            "is_actionable_for_user": e.meta.is_actionable_for_user,
                            "action_owner": e.meta.action_owner,
                            "action_summary": e.meta.action_summary,
                            "due_date": e.meta.due_date,
                            "commitment_made_by_user": e.meta.commitment_made_by_user,
                            "commitment_is_conditional": e.meta.commitment_is_conditional,
                            "commitment_condition": e.meta.commitment_condition,
                            "superseded_by_message_id": e.meta.superseded_by_message_id,
                            "notes": e.meta.notes,
                        },
                        ensure_ascii=False,
                    )
                )

        recent = sum(1 for e in pe if e.date.astimezone(timezone.utc) >= RECENT_CUTOFF)
        stats["profiles"][pid] = {
            "messages": len(pe),
            "threads": len(threads),
            "max_thread_length": max(len(v) for v in threads.values()),
            "folders": dict(Counter(e.folder.value for e in pe)),
            "categories": dict(Counter(e.category.value for e in pe if e.category)),
            "email_classes": dict(Counter(e.meta.email_class.value for e in pe)),
            "tricky_tags": dict(
                Counter(t.value for e in pe for t in e.meta.tricky_tags)
            ),
            "with_attachments": sum(1 for e in pe if e.attachments),
            "attachment_count": sum(len(e.attachments) for e in pe),
            "with_calendar_event": sum(1 for e in pe if e.calendar_event),
            "with_html": sum(1 for e in pe if e.body_html),
            "unread": sum(1 for e in pe if e.is_unread),
            "actionable": sum(1 for e in pe if e.meta.is_actionable_for_user),
            "recent_30d_pct": round(100 * recent / len(pe), 1),
            "date_range": [pe[0].date.date().isoformat(), pe[-1].date.date().isoformat()],
            "median_body_words": sorted(len(e.body_text.split()) for e in pe)[len(pe) // 2],
        }

    (ROOT / "data" / "ground_truth.jsonl").write_text("\n".join(gt_lines) + "\n")
    (RESULTS / "stats.json").write_text(json.dumps(stats, indent=2))
    return stats


def report(stats: dict[str, Any]) -> None:
    lines = [
        "# Mock Gmail corpus — build report",
        "",
        f"**{stats['total']} messages** across {len(stats['profiles'])} mailboxes, "
        "window 2026-06-13 → 2026-08-12.",
        "",
        "| profile | msgs | threads | longest | attach | cal | unread | actionable | recent 30d |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for pid, s in stats["profiles"].items():
        lines.append(
            f"| `{pid}` | {s['messages']} | {s['threads']} | {s['max_thread_length']} | "
            f"{s['with_attachments']} | {s['with_calendar_event']} | {s['unread']} | "
            f"{s['actionable']} | {s['recent_30d_pct']}% |"
        )

    lines += ["", "## Folder mix", "", "| profile | " + " | ".join(
        f.value for f in Folder) + " |", "|---" * (len(Folder) + 1) + "|"]
    for pid, s in stats["profiles"].items():
        lines.append(
            f"| `{pid}` | " + " | ".join(str(s["folders"].get(f.value, 0)) for f in Folder) + " |"
        )

    lines += ["", "## Tricky-case coverage", "", "| tag | " + " | ".join(
        stats["profiles"]) + " |", "|---" * (len(stats["profiles"]) + 1) + "|"]
    all_tags = sorted({t for s in stats["profiles"].values() for t in s["tricky_tags"]})
    for tag in all_tags:
        row = " | ".join(str(s["tricky_tags"].get(tag, 0)) for s in stats["profiles"].values())
        lines.append(f"| `{tag}` | {row} |")

    lines += [
        "",
        "## Files",
        "",
        "- `data/profiles/<profile>.canonical.jsonl` — one `Email` per line",
        "- `data/profiles/<profile>.gmail.jsonl` — `users.messages.get` shape",
        "- `data/profiles/<profile>.threads.json` — `users.threads.get` shape",
        "- `data/ground_truth.jsonl` — every message carrying a tricky tag",
        "- `results/stats.json` — the numbers above, machine-readable",
        "",
    ]
    (RESULTS / "corpus_report.md").write_text("\n".join(lines))


def main() -> int:
    emails = load()
    print(f"loaded {len(emails)} records from {len(list(RAW.glob('*.json')))} batch files")
    check_corpus(emails)

    for w in warnings[:40]:
        print(f"  warn: {w}")
    if len(warnings) > 40:
        print(f"  ... and {len(warnings) - 40} more warnings")

    if errors:
        print(f"\n{len(errors)} ERROR(S):")
        for e in errors[:60]:
            print(f"  - {e}")
        if len(errors) > 60:
            print(f"  ... and {len(errors) - 60} more")
        return 1

    normalise(emails)
    stats = emit(emails)
    report(stats)
    print(f"\nOK — {stats['total']} messages written to data/profiles/")
    print(f"see results/corpus_report.md ({len(warnings)} warnings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
