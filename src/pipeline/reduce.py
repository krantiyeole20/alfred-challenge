"""Stage 4: evidence -> work_items -> attention. Deterministic, no LLM.

The model proposed claims. This decides what is currently true. Every rule here
is plain code so it is testable and so "what changed" is a range scan over
work_item_changes rather than a generation task.

Fold order is by observed time: evidence is replayed oldest-first, so a later
claim can close or supersede an earlier one but never the reverse.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta

from . import config
from .db import connect, j, new_id, now_iso, run_record

# Attributes that OPEN a work item, and the speech act each produces.
OPENING = {
    "request.action": "request",
    "commitment.action": "commitment",
    "commitment.attachment": "commitment",
    "decision.value": "decision",
}

# Attributes that CLOSE an existing work item.
CLOSING = {
    "completion.action": "RESOLVED",
    "delivery.attachment": "RESOLVED",
    "cancellation": "CANCELLED",
}

_STOPWORDS = {
    # grammar
    "the", "a", "an", "to", "for", "of", "on", "in", "and", "or", "is", "be",
    "please", "can", "could", "would", "you", "we", "i", "me", "my", "your",
    "this", "that", "it", "will", "with", "at", "by", "from", "as", "so",
    # Action verbs are dropped on purpose. The speech act already lives in
    # `attribute` (request.action vs completion.action), so the verb carries no
    # matching signal -- and keeping it actively breaks the fold, because
    # "send the Q3 deck" and "Q3 deck sent" are the same obligation described
    # from opposite ends. Irregular forms are why a stopword list beats
    # suffix-stripping here: send/sent share no stem.
    "send", "sends", "sending", "sent", "get", "gets", "getting", "got",
    "provide", "provides", "provided", "share", "shared", "sharing",
    "return", "returned", "returning", "confirm", "confirmed", "confirming",
    "review", "reviewed", "reviewing", "complete", "completed", "completing",
    "finish", "finished", "deliver", "delivered", "delivering",
    "submit", "submitted", "submitting", "approve", "approved", "approving",
}
_WORD = re.compile(r"[a-z0-9]+")


def normalize_title(text: str) -> str:
    """Content key for matching claims about the same underlying obligation.

    Deliberately lossy: drops stopwords and ordering so "send the Q3 deck" and
    "Q3 deck sent" collapse to the same key. This is what lets a completion
    close the request it answers without an LLM in the loop.
    """
    words = [w for w in _WORD.findall((text or "").lower()) if w not in _STOPWORDS]
    return " ".join(sorted(set(words))[:8])


def match_key(speech_act: str, owner_person_id: str | None, title: str) -> str:
    return f"{speech_act}|{owner_person_id or 'unassigned'}|{normalize_title(title)}"


def _overlap(a: str, b: str) -> float:
    """Jaccard overlap between two normalized titles."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def resolve_due(surface: str | None, anchor_iso: str) -> tuple[str | None, float]:
    """Resolve a written deadline against the message that stated it.

    Returns (iso_date, confidence). Relative forms resolve against the anchor,
    which is why the anchor is stored on the evidence row: re-resolving later
    against a different "now" would silently change what "Friday" meant.
    """
    from .signals import date_mentions

    if not surface:
        return None, 0.0
    hits = date_mentions(surface, anchor_iso)
    for hit in hits:
        if hit["resolved"]:
            return hit["resolved"], 0.9 if hit["kind"] in ("iso", "monthday") else 0.7
    # Fuzzy forms carry an obligation but not a date we can defend.
    if re.search(r"\b(eod|cob|asap|end of (the )?day)\b", surface, re.I):
        return anchor_iso[:10], 0.5
    if re.search(r"\b(eow|end of (the )?week)\b", surface, re.I):
        anchor = datetime.fromisoformat(anchor_iso)
        return (anchor + timedelta(days=(4 - anchor.weekday()) % 7)).date().isoformat(), 0.5
    return None, 0.0


# --------------------------------------------------------------------------- #

def fold(conn: sqlite3.Connection, stats: dict) -> None:
    ts = now_iso()
    conn.execute("DELETE FROM attention_candidates")
    conn.execute("DELETE FROM work_item_changes")
    conn.execute("DELETE FROM work_item_threads")
    conn.execute("DELETE FROM work_items")
    conn.execute("UPDATE evidence SET work_item_id = NULL, superseded_by = NULL")

    rows = conn.execute(
        "SELECT e.*, em.provider_ts, em.subject "
        "FROM evidence e JOIN emails em ON em.id = e.source_email_id "
        "WHERE e.quote_verified = 1 AND e.invalidated_at IS NULL "
        "ORDER BY em.provider_ts, e.id"
    ).fetchall()
    stats["items_in"] = len(rows)

    # match_key -> work_item_id, per user
    index: dict[tuple[str, str], str] = {}
    # (user, owner, speech_act) -> [(normalized_title, work_item_id)]
    fuzzy: dict[tuple[str, str, str], list[tuple[str, str]]] = {}

    for ev in rows:
        value = json.loads(ev["value"])
        title = (value.get("summary") or "").strip()
        if not title:
            continue

        user_id = ev["user_id"]
        attribute = ev["attribute"]
        norm = normalize_title(title)

        if attribute in CLOSING:
            wid = _find_open(conn, index, fuzzy, user_id, ev, norm)
            if wid:
                new_status = CLOSING[attribute]
                conn.execute(
                    "UPDATE work_items SET status=?, resolved_at=?, last_activity_at=?, "
                    "updated_at=? WHERE id=? AND status='OPEN'",
                    (new_status, ev["provider_ts"], ev["provider_ts"], ts, wid),
                )
                _log_change(
                    conn, wid, user_id,
                    "resolved" if new_status == "RESOLVED" else "cancelled",
                    None, {"status": new_status}, ev["id"], ev["provider_ts"],
                )
                conn.execute("UPDATE evidence SET work_item_id=? WHERE id=?", (wid, ev["id"]))
            continue

        if attribute not in OPENING:
            continue

        speech_act = OPENING[attribute]
        key = (user_id, match_key(speech_act, ev["owner_person_id"], title))
        due_at, due_conf = resolve_due(ev["date_surface_form"], ev["provider_ts"])

        existing = index.get(key)
        if existing is None:
            existing = _fuzzy_lookup(fuzzy, user_id, ev["owner_person_id"], speech_act, norm)

        if existing:
            _update(conn, existing, ev, due_at, due_conf, ts, attribute)
            conn.execute("UPDATE evidence SET work_item_id=? WHERE id=?", (existing, ev["id"]))
            continue

        wid = new_id()
        conn.execute(
            "INSERT INTO work_items (id, user_id, origin_thread_id, speech_act, title, "
            "owner_person_id, requester_person_id, owner_is_self, owner_resolved, status, "
            "is_unconfirmed, due_at, due_confidence, first_seen_email_id, last_activity_at, "
            "opened_at, match_key, reducer_version, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?)",
            (
                wid, user_id, ev["thread_id"], speech_act, title,
                ev["owner_person_id"], ev["requester_person_id"], ev["owner_is_self"],
                ev["owner_resolved"],
                1 if not ev["owner_resolved"] else 0,
                due_at, due_conf, ev["source_email_id"], ev["provider_ts"],
                ev["provider_ts"], key[1], config.REDUCER_VERSION, ts, ts,
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO work_item_threads (work_item_id, thread_id) VALUES (?,?)",
            (wid, ev["thread_id"]),
        )
        conn.execute("UPDATE evidence SET work_item_id=? WHERE id=?", (wid, ev["id"]))
        _log_change(conn, wid, user_id, "opened", None,
                    {"title": title, "due_at": due_at}, ev["id"], ev["provider_ts"])

        index[key] = wid
        fuzzy.setdefault((user_id, ev["owner_person_id"] or "", speech_act), []).append((norm, wid))
        stats["items_out"] += 1

    conn.commit()


def _fuzzy_lookup(fuzzy, user_id, owner, speech_act, norm) -> str | None:
    """Near-match on title, for claims that reword the same obligation."""
    for cand_norm, wid in fuzzy.get((user_id, owner or "", speech_act), []):
        if _overlap(cand_norm, norm) >= 0.6:
            return wid
    return None


def _find_open(conn, index, fuzzy, user_id, ev, norm) -> str | None:
    """Find the open work item a closing claim refers to.

    Tries the same thread first -- a completion almost always lands in the
    thread that requested it -- then falls back to title overlap for any owner.
    """
    row = conn.execute(
        "SELECT wi.id, wi.title FROM work_items wi "
        "JOIN work_item_threads wt ON wt.work_item_id = wi.id "
        "WHERE wi.user_id=? AND wt.thread_id=? AND wi.status='OPEN' "
        "ORDER BY wi.opened_at DESC",
        (user_id, ev["thread_id"]),
    ).fetchall()
    best, best_score = None, 0.0
    for cand in row:
        score = _overlap(normalize_title(cand["title"]), norm)
        if score > best_score:
            best, best_score = cand["id"], score
    if best and best_score >= 0.25:
        return best
    if row:
        return row[0]["id"]

    for speech_act in ("request", "commitment"):
        wid = _fuzzy_lookup(fuzzy, user_id, ev["owner_person_id"], speech_act, norm)
        if wid:
            return wid
    return None


def _update(conn, wid, ev, due_at, due_conf, ts, attribute) -> None:
    """Fold a repeat claim into an existing item, recording what moved."""
    cur = conn.execute("SELECT * FROM work_items WHERE id=?", (wid,)).fetchone()
    if cur is None:
        return

    if due_at and due_at != cur["due_at"] and due_conf >= (cur["due_confidence"] or 0):
        _log_change(conn, wid, ev["user_id"], "due_moved",
                    {"due_at": cur["due_at"]}, {"due_at": due_at},
                    ev["id"], ev["provider_ts"])
        conn.execute(
            "UPDATE work_items SET due_at=?, due_confidence=? WHERE id=?",
            (due_at, due_conf, wid),
        )

    # A later decision.value on the same item supersedes the earlier one --
    # this is the shifting_number / deadline_moved case.
    if attribute == "decision.value":
        prior = conn.execute(
            "SELECT id FROM evidence WHERE work_item_id=? AND attribute='decision.value' "
            "AND id<>? AND superseded_by IS NULL ORDER BY observed_at",
            (wid, ev["id"]),
        ).fetchall()
        for p in prior:
            conn.execute("UPDATE evidence SET superseded_by=? WHERE id=?", (ev["id"], p["id"]))
        if prior:
            _log_change(conn, wid, ev["user_id"], "value_superseded", None,
                        {"evidence_id": ev["id"]}, ev["id"], ev["provider_ts"])

    if ev["owner_resolved"] and not cur["owner_resolved"]:
        _log_change(conn, wid, ev["user_id"], "owner_changed",
                    {"owner_person_id": cur["owner_person_id"]},
                    {"owner_person_id": ev["owner_person_id"]},
                    ev["id"], ev["provider_ts"])
        conn.execute(
            "UPDATE work_items SET owner_person_id=?, owner_is_self=?, owner_resolved=1, "
            "is_unconfirmed=0 WHERE id=?",
            (ev["owner_person_id"], ev["owner_is_self"], wid),
        )

    conn.execute(
        "UPDATE work_items SET last_activity_at=?, updated_at=? WHERE id=?",
        (max(cur["last_activity_at"], ev["provider_ts"]), ts, wid),
    )
    conn.execute(
        "INSERT OR IGNORE INTO work_item_threads (work_item_id, thread_id) VALUES (?,?)",
        (wid, ev["thread_id"]),
    )


def _log_change(conn, wid, user_id, change_type, old, new, evidence_id, when) -> None:
    conn.execute(
        "INSERT INTO work_item_changes (id, work_item_id, user_id, change_type, "
        "old_value, new_value, evidence_id, changed_at) VALUES (?,?,?,?,?,?,?,?)",
        (new_id(), wid, user_id, change_type,
         j(old) if old else None, j(new) if new else None, evidence_id, when),
    )


# --------------------------------------------------------------------------- #
# Attention ranking
# --------------------------------------------------------------------------- #

WEIGHTS = {"urgency": 0.40, "importance": 0.25, "staleness": 0.20, "commitment": 0.15}


def rank(conn: sqlite3.Connection, stats: dict) -> None:
    ts = now_iso()
    as_of = datetime.fromisoformat(config.AS_OF)

    rows = conn.execute(
        "SELECT wi.*, "
        "  (SELECT count(*) FROM evidence e WHERE e.work_item_id = wi.id) AS ev_count, "
        "  (SELECT count(*) FROM work_item_threads wt WHERE wt.work_item_id = wi.id) AS thread_count "
        "FROM work_items wi WHERE wi.status = 'OPEN'"
    ).fetchall()

    for wi in rows:
        # urgency: how close (or past) the deadline is
        if wi["due_at"]:
            days = (datetime.fromisoformat(wi["due_at"]).date() - as_of.date()).days
            urgency = 1.0 if days < 0 else max(0.0, 1.0 - days / 14.0)
            reason = "overdue" if days < 0 else ("due_soon" if days <= 3 else "unanswered_request")
        else:
            urgency, reason = 0.25, "unanswered_request"

        # importance: repeated mentions across threads are the strongest
        # available proxy without a hand-maintained VIP list
        importance = min(1.0, 0.3 + 0.2 * (wi["ev_count"] or 1) + 0.2 * ((wi["thread_count"] or 1) - 1))

        # staleness: silence since the last activity on the item
        quiet_days = (as_of - datetime.fromisoformat(wi["last_activity_at"]).replace(tzinfo=None)).days
        staleness = min(1.0, max(0.0, quiet_days / 21.0))
        if quiet_days >= 14 and reason == "unanswered_request":
            reason = "stale"

        # commitment: the owner's own promises outrank other people's asks
        if wi["speech_act"] == "commitment" and wi["owner_is_self"]:
            commitment, reason = 1.0, "unfulfilled_commitment"
        elif wi["owner_is_self"]:
            commitment = 0.6
        else:
            commitment = 0.2

        breakdown = {
            "urgency": round(urgency, 3),
            "importance": round(importance, 3),
            "staleness": round(staleness, 3),
            "commitment": round(commitment, 3),
        }
        score = sum(WEIGHTS[k] * v for k, v in breakdown.items())

        conn.execute(
            "INSERT INTO attention_candidates (id, user_id, work_item_id, reason, "
            "urgency_norm, importance_norm, staleness_norm, commitment_norm, score, "
            "score_breakdown, generator_version, generated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id(), wi["user_id"], wi["id"], reason,
                urgency, importance, staleness, commitment, score,
                j({**breakdown, "weights": WEIGHTS}), config.ATTENTION_VERSION, ts,
            ),
        )
    conn.commit()


def main() -> None:
    conn = connect()
    with run_record(conn, "reduce") as stats:
        fold(conn, stats)
        rank(conn, stats)
    counts = {
        "work_items": conn.execute("SELECT count(*) FROM work_items").fetchone()[0],
        "open": conn.execute("SELECT count(*) FROM work_items WHERE status='OPEN'").fetchone()[0],
        "changes": conn.execute("SELECT count(*) FROM work_item_changes").fetchone()[0],
        "attention": conn.execute("SELECT count(*) FROM attention_candidates").fetchone()[0],
    }
    conn.close()
    print("reduced:", ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
