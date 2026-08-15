"""Stage 2: deterministic signals. No LLM.

Two flags, deliberately separate:

    is_automated   mechanism -- was this machine-sent?
    is_noise       routing   -- is there anything here worth tracking?

Only is_noise gates extraction. Gating on is_automated would silently drop
application status changes, receipts, ticket updates, bounces and renewal
notices: all machine-sent from no-reply@, all carrying real state that opens or
closes a work item.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta

from . import config
from .db import connect, j, now_iso, run_record

# --------------------------------------------------------------------------- #
# Automation markers (mechanism)
# --------------------------------------------------------------------------- #

_NOREPLY = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|notifications?|mailer|bounce|"
    r"postmaster|automated|auto|alerts?|system)\b",
    re.I,
)


def automation(headers: dict, from_addr: str) -> tuple[bool, str | None]:
    precedence = (headers.get("Precedence") or "").lower()
    if headers.get("Auto-Submitted"):
        return True, "auto_submitted"
    if headers.get("List-Unsubscribe"):
        return True, "list_unsubscribe"
    if precedence in {"bulk", "list", "junk"}:
        return True, "precedence_bulk"
    if headers.get("Feedback-ID"):
        return True, "esp_return_path"
    local = from_addr.split("@")[0] if from_addr else ""
    if _NOREPLY.match(local):
        return True, "noreply_local"
    return False, None


# --------------------------------------------------------------------------- #
# Noise gate (routing) -- deliberately narrow.
#
# Deliberately excluded from this gate:
#   SPAM folder    -- phishing and lookalike-domain invoice fraud land there,
#                     and those are exactly what the owner needs told about.
#                     Machine-classified spam is not evidence of irrelevance.
#   Auto-Submitted -- out-of-office replies close/defer real obligations.
#   noreply_local  -- transactional mail carries state.
#
# WHY NARROW. Measured against meta.email_class over the whole corpus:
#
#   gate rule                          real work lost   noise gated   extract $
#   category OR bulk headers                        6           202       1.42
#   List-ID OR (promo/social AND auto)              2           134       1.50
#   List-ID only                                    0            35       1.61
#   no gate at all                                  0             0       1.65
#
# The aggressive gate saves $0.23 and loses six real obligations -- among them
# a speaking-slot confirmation and a "your plan renews in 5 days, update your
# payment method" notice that Gmail happened to file under Promotions. At Luna
# prices the gate's cost justification does not survive contact with the
# numbers, so it is tuned for ledger hygiene instead: gate only unambiguous
# bulk, and let the reducer ignore whatever junk gets through. Losing a real
# obligation is a visible failure; extracting a newsletter costs $0.001.
# --------------------------------------------------------------------------- #


# Transactional language that overrides a promotional category. This is the
# "machine-sent but carrying real state" case: renewals, payment failures,
# invoices and expiries are routinely filed under Promotions by the provider,
# and every one of them can open a work item.
_TRANSACTIONAL = re.compile(
    r"\b(renew(s|al|ing)?|expir(es?|ing|ation)|past due|overdue|invoice|receipt|"
    r"payment (method|failed|declined)|update your (payment|card|billing)|"
    r"action required|final notice|suspend(ed|ing)?|cancel(led|lation) )\b",
    re.I,
)


def noise(
    headers: dict,
    category: str | None,
    folder: str | None,
    is_automated: bool,
    subject: str | None = None,
) -> tuple[bool, str | None]:
    # A true mailing list: List-ID means list traffic, not a person writing.
    if headers.get("List-ID"):
        return True, "list_archive"
    # Transactional subject beats the provider's category label.
    if subject and _TRANSACTIONAL.search(subject):
        return False, None
    # Promotional/social AND machine-sent. The category alone is not enough --
    # Gmail files real speaking-slot confirmations under Promotions.
    if category == "CATEGORY_PROMOTIONS" and is_automated:
        return True, "promotion"
    if category == "CATEGORY_SOCIAL" and is_automated:
        return True, "advert"
    return False, None


# --------------------------------------------------------------------------- #
# Date mentions
# --------------------------------------------------------------------------- #

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_DATE_PATTERNS = [
    (re.compile(r"\b(today|tonight)\b", re.I), "today"),
    (re.compile(r"\btomorrow\b", re.I), "tomorrow"),
    (re.compile(r"\byesterday\b", re.I), "yesterday"),
    (re.compile(r"\b(?:next|this|by|on|before)\s+(" + "|".join(_WEEKDAYS) + r")\b", re.I), "weekday"),
    (re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b", re.I), "weekday"),
    (re.compile(r"\b(eod|eow|cob|asap|end of (?:the )?(?:day|week|month|quarter))\b", re.I), "fuzzy"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "iso"),
    (
        re.compile(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b",
            re.I,
        ),
        "monthday",
    ),
]


def date_mentions(text: str, anchor_iso: str) -> list[dict]:
    """Find date surface forms and resolve the resolvable ones.

    Relative forms are resolved against the message's own timestamp, which is
    why the anchor is stored alongside: re-resolving later against a different
    "now" would silently change what "Friday" meant.
    """
    if not text:
        return []
    try:
        anchor = datetime.fromisoformat(anchor_iso)
    except ValueError:
        return []

    out: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for pattern, kind in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            span = (m.start(), m.end())
            if any(s <= span[0] < e for s, e in seen):
                continue
            seen.add(span)
            resolved = None
            if kind == "today":
                resolved = anchor.date().isoformat()
            elif kind == "tomorrow":
                resolved = (anchor + timedelta(days=1)).date().isoformat()
            elif kind == "yesterday":
                resolved = (anchor - timedelta(days=1)).date().isoformat()
            elif kind == "weekday":
                target = _WEEKDAYS.index(m.group(1).lower())
                delta = (target - anchor.weekday()) % 7
                resolved = (anchor + timedelta(days=delta or 7)).date().isoformat()
            elif kind == "iso":
                resolved = m.group(0)
            out.append(
                {
                    "surface": m.group(0),
                    "kind": kind,
                    "resolved": resolved,
                    "anchor_date": anchor.date().isoformat(),
                    "char_offset": m.start(),
                }
            )
    return sorted(out, key=lambda d: d["char_offset"])[:20]


_QUESTION = re.compile(r"\?|\b(could you|can you|would you|please (?:send|confirm|review|approve)|let me know|any update|when can|do you)\b", re.I)


# --------------------------------------------------------------------------- #

def compute(conn: sqlite3.Connection, stats: dict) -> None:
    ts = now_iso()
    rows = conn.execute(
        "SELECT e.id, e.user_id, e.thread_id, e.body_text_novel, e.category, e.folder, "
        "       e.direction, e.provider_ts, e.subject "
        "FROM emails e"
    ).fetchall()
    stats["items_in"] = len(rows)

    # Raw headers are not on the emails table; re-read them from the corpus.
    headers_by_msg: dict[str, dict] = {}
    for profile_id in config.PROFILE_IDS:
        path = config.PROFILES / f"{profile_id}.canonical.jsonl"
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            headers_by_msg[rec["id"]] = rec.get("extra_headers") or {}

    pm_ids = {
        r["id"]: r["provider_message_id"]
        for r in conn.execute("SELECT id, provider_message_id FROM emails")
    }

    # Per-thread ordering, for position and reply latency.
    thread_seq: dict[str, list[tuple[str, str]]] = {}
    for r in conn.execute("SELECT id, thread_id, provider_ts FROM emails ORDER BY thread_id, provider_ts"):
        thread_seq.setdefault(r["thread_id"], []).append((r["id"], r["provider_ts"]))
    position_of: dict[str, int] = {}
    prev_ts_of: dict[str, str] = {}
    for seq in thread_seq.values():
        for idx, (eid, ets) in enumerate(seq):
            position_of[eid] = idx + 1
            if idx:
                prev_ts_of[eid] = seq[idx - 1][1]

    parts: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute(
        "SELECT email_id, role, raw_address, is_user FROM email_participants"
    ):
        parts.setdefault(r["email_id"], []).append(r)

    for row in rows:
        eid = row["id"]
        headers = headers_by_msg.get(pm_ids[eid], {})
        prow = parts.get(eid, [])
        from_addr = next((p["raw_address"] for p in prow if p["role"] == "from"), "")

        is_auto, auto_reason = automation(headers, from_addr)
        is_noise, noise_reason = noise(
            headers, row["category"], row["folder"], is_auto, row["subject"]
        )

        user_in_to = any(p["role"] == "to" and p["is_user"] for p in prow)
        user_in_cc = any(p["role"] == "cc" and p["is_user"] for p in prow)
        user_is_sender = row["direction"] == "outbound"
        recipient_count = sum(1 for p in prow if p["role"] in ("to", "cc", "bcc"))

        latency = None
        if eid in prev_ts_of:
            latency = int(
                (
                    datetime.fromisoformat(row["provider_ts"])
                    - datetime.fromisoformat(prev_ts_of[eid])
                ).total_seconds()
            )

        body = row["body_text_novel"] or ""
        haystack = f"{row['subject'] or ''}\n{body}"

        conn.execute(
            "INSERT OR REPLACE INTO email_signals (email_id, user_id, thread_id, "
            "is_automated, automation_reason, is_noise, noise_reason, user_in_to, "
            "user_in_cc, user_is_sender, recipient_count, thread_position, "
            "reply_latency_sec, novel_char_count, contains_question, date_mentions, "
            "signal_version, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid, row["user_id"], row["thread_id"],
                int(is_auto), auto_reason, int(is_noise), noise_reason,
                int(user_in_to), int(user_in_cc), int(user_is_sender), recipient_count,
                position_of.get(eid), latency, len(body),
                int(bool(_QUESTION.search(haystack))),
                j(date_mentions(haystack, row["provider_ts"])),
                config.SIGNAL_VERSION, ts,
            ),
        )
        stats["items_out"] += 1


def main() -> None:
    conn = connect()
    with run_record(conn, "signals") as stats:
        compute(conn, stats)
        conn.commit()
    total = conn.execute("SELECT count(*) FROM email_signals").fetchone()[0]
    noisy = conn.execute("SELECT count(*) FROM email_signals WHERE is_noise=1").fetchone()[0]
    auto = conn.execute("SELECT count(*) FROM email_signals WHERE is_automated=1").fetchone()[0]
    conn.close()
    print(
        f"signals: {total} emails, {auto} automated, {noisy} gated as noise, "
        f"{total - noisy} to extract"
    )


if __name__ == "__main__":
    main()
