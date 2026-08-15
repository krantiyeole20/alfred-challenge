"""Stage 1: canonical JSONL -> relational base tables.

Reads data/profiles/<profile>.canonical.jsonl and populates users, accounts,
user_identities, people, person_identities, threads, emails, email_participants.

No LLM. Everything here is mechanical.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from . import config
from .db import connect, init_schema, j, new_id, now_iso, run_record
from .identity import may_merge

# Persona metadata. Timezones come from docs/personas.md; "due tomorrow" needs
# an owner's midnight, so this is load-bearing, not decoration.
PROFILES = {
    "founder": {
        "display_name": "Maya Rodriguez",
        "address": "maya@kettlehq.com",
        "timezone": "America/Los_Angeles",
        "role_profile": "founder_ceo",
    },
    "marketing": {
        "display_name": "Jordan Feld",
        "address": "jordan.feld@habitatgoods.com",
        "timezone": "America/Chicago",
        "role_profile": "vp_marketing",
    },
    "finance": {
        "display_name": "Aditi Sharma",
        "address": "aditi.sharma@northwindrobotics.com",
        "timezone": "America/New_York",
        "role_profile": "vp_finance",
    },
    "hr": {
        "display_name": "Marcus Bell",
        "address": "marcus.bell@vireohealth.com",
        "timezone": "America/Denver",
        "role_profile": "head_of_people",
    },
    "consulting": {
        "display_name": "Evelyn Thorne",
        "address": "evelyn.thorne@thornecadwell.com",
        "timezone": "America/Chicago",
        "role_profile": "consultancy_president",
    },
}

_SUBJECT_PREFIX = re.compile(r"^\s*((re|fw|fwd|aw|sv)\s*(\[\d+\])?\s*:\s*)+", re.I)
_WS = re.compile(r"\s+")

# Quoted-reply detection. Deliberately conservative: body_text_full is kept
# intact because quoted_text_only_task plants a real obligation inside quoted
# text, and stripping it destructively would make that case unanswerable.
_QUOTE_HEADER = re.compile(
    r"^\s*(On .{0,120}\bwrote:\s*$"
    r"|-{2,}\s*Original Message\s*-{2,}"
    r"|_{5,}"
    r"|From:\s*.+\bSent:\s*)",
    re.I | re.M,
)


def normalize_subject(subject: str | None) -> str:
    if not subject:
        return ""
    return _WS.sub(" ", _SUBJECT_PREFIX.sub("", subject)).strip().lower()


def split_novel(body: str | None) -> tuple[str, str]:
    """Return (novel_text, full_text).

    Novel text is what the extractor reads by default: this message's own
    words, with the quoted tail removed. Full text is retained on the row so a
    task that exists only inside quoted material is still recoverable.
    """
    if not body:
        return "", ""
    full = body
    match = _QUOTE_HEADER.search(body)
    if match:
        novel = body[: match.start()]
    else:
        novel = body
    # Drop residual '>' quote lines.
    novel = "\n".join(ln for ln in novel.splitlines() if not ln.lstrip().startswith(">"))
    return novel.strip(), full


def _addr(entry) -> tuple[str, str | None]:
    """(email, name) from a corpus address object."""
    if not entry:
        return "", None
    return (entry.get("email") or "").lower(), entry.get("name")


def load_profile(conn: sqlite3.Connection, profile_id: str, stats: dict) -> None:
    meta = PROFILES[profile_id]
    path = config.PROFILES / f"{profile_id}.canonical.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    stats["items_in"] += len(records)

    ts = now_iso()
    user_id, account_id = new_id(), new_id()

    conn.execute(
        "INSERT INTO users (id, display_name, timezone, role_profile, "
        "extraction_window_days, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, meta["display_name"], meta["timezone"], meta["role_profile"], 30, ts, ts),
    )
    conn.execute(
        "INSERT INTO accounts (id, user_id, provider, provider_account_id, "
        "email_address, is_primary, connected_at, created_at, updated_at) "
        "VALUES (?,?,'gmail',?,?,1,?,?,?)",
        (account_id, user_id, profile_id, meta["address"], ts, ts, ts),
    )
    conn.execute(
        "INSERT INTO user_identities (id, user_id, account_id, address, source, created_at) "
        "VALUES (?,?,?,?,'account',?)",
        (new_id(), user_id, account_id, meta["address"], ts),
    )

    owner_address = meta["address"]

    # ---- people: exact-address resolution, then a name-equality merge pass.
    # The design defers fuzzy matching, but same_person_two_addresses is a
    # planted case, and merging on an identical observed name catches it
    # without opening the door to speculative fuzzy joins.
    observed: dict[str, dict] = {}
    for rec in records:
        seen = []
        for role in ("from", "sender", "reply_to"):
            if rec.get(role):
                seen.append(_addr(rec[role]))
        for role in ("to", "cc", "bcc"):
            for entry in rec.get(role) or []:
                seen.append(_addr(entry))
        for email, name in seen:
            if not email:
                continue
            slot = observed.setdefault(
                email,
                {"name": name, "first": rec["date"], "last": rec["date"], "count": 0},
            )
            slot["count"] += 1
            slot["name"] = slot["name"] or name
            slot["first"] = min(slot["first"], rec["date"])
            slot["last"] = max(slot["last"], rec["date"])

    # Resolve most-seen addresses first so the established address anchors the
    # person and a lookalike is judged against it, not the other way round.
    by_name: dict[str, str] = {}
    name_anchor: dict[str, str] = {}
    person_of_address: dict[str, str] = {}
    ordered = sorted(observed.items(), key=lambda kv: (-kv[1]["count"], kv[0]))

    for email, info in ordered:
        is_self = email == owner_address
        name_key = (info["name"] or "").strip().lower()
        method = "exact"
        if is_self:
            person_id = by_name.setdefault("\x00self", new_id())
        elif name_key and name_key in by_name:
            anchor = name_anchor[name_key]
            allowed, relation = may_merge(anchor, email)
            if allowed:
                person_id = by_name[name_key]
                method = "name_match"
            else:
                # Same display name on a confusingly similar domain. Keep them
                # apart and record it — this is the lookalike_domain signal.
                person_id = new_id()
                conn.execute(
                    "INSERT INTO identity_conflicts (id, user_id, observed_name, "
                    "address_a, address_b, relation, message_count_a, message_count_b, "
                    "detected_at, resolver_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_id(), user_id, info["name"], anchor, email, relation,
                        observed[anchor]["count"], info["count"], ts,
                        config.RESOLVER_VERSION,
                    ),
                )
                method = "lookalike_split"
        else:
            person_id = new_id()
            if name_key:
                by_name[name_key] = person_id
                name_anchor[name_key] = email

        conn.execute(
            "INSERT OR IGNORE INTO people (id, user_id, canonical_name, is_self, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (person_id, user_id, info["name"], 1 if is_self else 0, ts, ts),
        )
        conn.execute(
            "INSERT INTO person_identities (id, person_id, user_id, address, observed_name, "
            "first_seen_at, last_seen_at, message_count, confidence, resolution_method, "
            "resolver_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id(), person_id, user_id, email, info["name"],
                info["first"], info["last"], info["count"],
                0.85 if method == "name_match" else 1.0,
                method, config.RESOLVER_VERSION,
            ),
        )
        person_of_address[email] = person_id

    # ---- threads
    threads: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        threads[rec["thread_id"]].append(rec)

    thread_ids: dict[str, str] = {}
    for provider_thread_id, msgs in threads.items():
        msgs.sort(key=lambda r: r["date"])
        thread_id = new_id()
        thread_ids[provider_thread_id] = thread_id
        gaps = [
            (
                _epoch(msgs[i]["date"]) - _epoch(msgs[i - 1]["date"])
            )
            for i in range(1, len(msgs))
        ]
        gaps.sort()
        median_gap = gaps[len(gaps) // 2] if gaps else None
        participants = {
            e for m in msgs for e, _ in
            [_addr(m.get("from"))] + [_addr(x) for x in (m.get("to") or [])]
            if e
        }
        conn.execute(
            "INSERT INTO threads (id, user_id, root_rfc_message_id, normalized_subject, "
            "first_message_at, last_message_at, message_count, participant_count, "
            "median_reply_gap_sec, clustering_version, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                thread_id, user_id, msgs[0].get("message_id_header"),
                normalize_subject(msgs[0].get("subject")),
                msgs[0]["date"], msgs[-1]["date"], len(msgs), len(participants),
                median_gap, config.CLUSTERING_VERSION, ts, ts,
            ),
        )

    # ---- emails + participants
    for rec in records:
        email_id = new_id()
        from_email, _ = _addr(rec.get("from"))
        novel, full = split_novel(rec.get("body_text"))
        direction = "outbound" if (rec.get("folder") == "SENT" or from_email == owner_address) else "inbound"
        attachments = rec.get("attachments") or []

        conn.execute(
            "INSERT INTO emails (id, account_id, user_id, thread_id, provider_message_id, "
            "provider_thread_id, rfc_message_id, in_reply_to, references_chain, subject, "
            "normalized_subject, snippet, body_text_novel, body_text_full, body_char_count, "
            "normalizer_version, direction, provider_ts, header_date, has_attachments, "
            "attachments, raw_storage_uri, folder, category, is_unread, is_starred, "
            "is_important, ingested_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                email_id, account_id, user_id, thread_ids[rec["thread_id"]],
                rec["id"], rec["thread_id"], rec.get("message_id_header"),
                rec.get("in_reply_to"), j(rec.get("references") or []),
                rec.get("subject"), normalize_subject(rec.get("subject")),
                rec.get("snippet"), novel, full, len(full),
                config.NORMALIZER_VERSION, direction, rec["date"], rec["date"],
                1 if attachments else 0, j(attachments),
                f"corpus://{profile_id}/{rec['id']}",
                rec.get("folder"), rec.get("category"),
                int(bool(rec.get("is_unread"))), int(bool(rec.get("is_starred"))),
                int(bool(rec.get("is_important"))), ts,
            ),
        )

        rows = []
        for role in ("from", "sender", "reply_to"):
            if rec.get(role):
                email, name = _addr(rec[role])
                if email:
                    rows.append((role, 0, email, name))
        for role in ("to", "cc", "bcc"):
            for pos, entry in enumerate(rec.get(role) or []):
                email, name = _addr(entry)
                if email:
                    rows.append((role, pos, email, name))

        for role, pos, email, name in rows:
            conn.execute(
                "INSERT OR REPLACE INTO email_participants (email_id, role, position, "
                "raw_address, raw_name, person_id, is_user) VALUES (?,?,?,?,?,?,?)",
                (
                    email_id, role, pos, email, name,
                    person_of_address.get(email),
                    1 if email == owner_address else 0,
                ),
            )
        stats["items_out"] += 1


def _epoch(iso: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(iso).timestamp())


def main(reset_db: bool = True) -> None:
    from .db import reset as reset_file

    if reset_db:
        reset_file()
    conn = connect()
    init_schema(conn)
    with run_record(conn, "load") as stats:
        for profile_id in config.PROFILE_IDS:
            load_profile(conn, profile_id, stats)
            conn.commit()
    counts = {
        t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("users", "people", "person_identities", "threads", "emails", "email_participants")
    }
    conn.close()
    print("loaded:", ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
