"""Stage 3: emails -> evidence claims. The only stage that costs money.

The model proposes claims with verbatim quotes. It never decides current state,
never updates, never deletes. Every claim is checked against the source text
before it enters the ledger; a quote that cannot be found is quarantined rather
than trusted. That check is what makes "every answer carries a citation" true
by construction instead of by prompting.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import sqlite3
from typing import Any

from . import config
from .db import connect, j, new_id, now_iso, run_record
from .llm import LLM, BudgetExceeded, resolve_models

# --------------------------------------------------------------------------- #
# Attribute vocabulary. Append-only: the fold groups on `attribute`, so renaming
# one leaves an immortal ghost value. Deprecate and supersede instead.
# --------------------------------------------------------------------------- #

VOCAB: list[tuple[str, str, str]] = [
    ("request.action", "request", "Someone asks a named party to do something."),
    ("commitment.action", "commitment", "Someone promises to do something."),
    ("commitment.attachment", "commitment", "Someone promises to send a file or document."),
    ("decision.value", "decision", "A fact, number, date or choice is stated as settled."),
    ("completion.action", "decision", "Something previously asked or promised is reported done."),
    ("delivery.attachment", "decision", "A previously promised file is actually attached or linked."),
    ("cancellation", "decision", "A previously stated request, commitment or decision is retracted."),
]

VALUE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "detail": {"type": ["string", "null"]},
    },
    "required": ["summary"],
}


def seed_vocab(conn: sqlite3.Connection) -> None:
    for attribute, speech_act, description in VOCAB:
        conn.execute(
            "INSERT OR IGNORE INTO attribute_vocab (attribute, speech_act, value_schema, "
            "introduced_in) VALUES (?,?,?,?)",
            (attribute, speech_act, j({**VALUE_SCHEMA, "description": description}),
             config.EXTRACTOR_VERSION),
        )
    conn.commit()


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #

SYSTEM = """\
You extract structured claims from a single work email. You are one stage in a \
pipeline: a deterministic reducer downstream decides what is currently true. \
Your job is only to report what THIS email says, with evidence.

Rules that matter more than completeness:

1. EVERY claim needs `evidence_quote`: a span copied CHARACTER-FOR-CHARACTER \
from the email text you were given. Do not paraphrase, fix typos, normalise \
whitespace, or join separated sentences. If you cannot copy an exact span, do \
not make the claim.
2. Report only what the text states. Never infer an obligation from tone, and \
never invent a due date that is not written down.
3. Ownership is who must ACT, not who sent the mail. A forwarded task usually \
belongs to a third party. "Can someone look at this?" sent to six people is \
owned by nobody until a person claims it — set `owner_address` to null.
4. A conditional promise ("if legal approves, Friday") is a commitment with \
`is_conditional` true and the condition recorded. It is not a settled fact.
5. If the email retracts or supersedes something stated earlier, use \
`cancellation` or `decision.value` — do not silently drop the old claim.
6. Tasks can live in quoted text or in an attachment's extracted text. Both are \
provided and both count. Quote from whichever section you used.
7. Prefer no claim over a speculative one. An email with nothing actionable \
should return an empty list. Most email is like this.

`attribute` must be exactly one of:
  request.action        someone asks a named party to do something
  commitment.action     someone promises to do something
  commitment.attachment someone promises to send a file
  decision.value        a fact/number/date/choice stated as settled
  completion.action     something asked or promised is reported done
  delivery.attachment   a promised file is actually attached here
  cancellation          a previous request/commitment/decision is retracted

Addresses must be copied exactly from the participant list you were given, or \
be null. Never invent an address.\
"""

# Deliberately avoids union types (["string", "null"]) and additionalProperties:
# the empty string stands for "absent" instead. Nullable unions are the part of
# JSON Schema that providers implement least consistently, and this schema has
# to survive both the primary and the fallback model without a shape change.
CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "description": "Empty if the email states nothing actionable. Most email does.",
            "items": {
                "type": "object",
                "properties": {
                    "attribute": {
                        "type": "string",
                        "enum": [a for a, _, _ in VOCAB],
                    },
                    "summary": {
                        "type": "string",
                        "description": "One short line naming the action or fact.",
                    },
                    "evidence_quote": {
                        "type": "string",
                        "description": (
                            "A span copied character-for-character from the email "
                            "text above. Not a paraphrase."
                        ),
                    },
                    "owner_address": {
                        "type": "string",
                        "description": (
                            "Email address of whoever must act, copied exactly from "
                            "the participant list. Empty string if genuinely unassigned."
                        ),
                    },
                    "requester_address": {
                        "type": "string",
                        "description": (
                            "Email address of whoever wants it. Empty string if not stated."
                        ),
                    },
                    "due_surface_form": {
                        "type": "string",
                        "description": (
                            "Deadline exactly as written, e.g. 'next Thursday'. "
                            "Empty string if the email states no deadline."
                        ),
                    },
                    "is_conditional": {
                        "type": "boolean",
                        "description": "True if the claim depends on a stated condition.",
                    },
                    "condition": {
                        "type": "string",
                        "description": "The condition, if any. Empty string otherwise.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "0.0 to 1.0.",
                    },
                },
                "required": [
                    "attribute", "summary", "evidence_quote", "owner_address",
                    "requester_address", "due_surface_form", "is_conditional",
                    "condition", "confidence",
                ],
            },
        }
    },
    "required": ["claims"],
}

PROMPT_HASH = hashlib.sha256(
    (SYSTEM + json.dumps(CLAIM_SCHEMA, sort_keys=True)).encode()
).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Quote verification
# --------------------------------------------------------------------------- #

_WS = re.compile(r"\s+")


def find_quote(quote: str, haystacks: list[str]) -> tuple[bool, int | None]:
    """Locate a quote in the source text.

    Exact match first. Falls back to whitespace-insensitive matching, because
    line wrapping is a formatting artefact rather than a fabrication -- but any
    change to the actual words still fails, which is the point.
    """
    q = (quote or "").strip()
    if len(q) < 8:
        return False, None
    for hay in haystacks:
        if not hay:
            continue
        idx = hay.find(q)
        if idx >= 0:
            return True, idx
    flat_q = _WS.sub(" ", q)
    for hay in haystacks:
        if not hay:
            continue
        if _WS.sub(" ", hay).find(flat_q) >= 0:
            return True, None
    return False, None


# --------------------------------------------------------------------------- #

def build_user_prompt(row: sqlite3.Row, participants: list[sqlite3.Row]) -> str:
    who = "\n".join(
        f"  {p['role']:9} {p['raw_name'] or '(no name)'} <{p['raw_address']}>"
        + ("   [MAILBOX OWNER]" if p["is_user"] else "")
        for p in participants
    )
    attachments = json.loads(row["attachments"] or "[]")
    att_text = ""
    for a in attachments:
        extracted = (a.get("extracted_text") or "").strip()
        if extracted:
            att_text += f"\n--- attachment: {a.get('filename')} ---\n{extracted[:4000]}\n"

    quoted = ""
    full, novel = row["body_text_full"] or "", row["body_text_novel"] or ""
    if len(full) > len(novel):
        quoted = f"\n--- quoted / earlier text in this message ---\n{full[len(novel):][:4000]}\n"

    return f"""\
Mailbox owner: {row['owner_name']} <{row['owner_address']}> ({row['timezone']})
This message was: {'SENT BY the owner' if row['direction'] == 'outbound' else 'RECEIVED by the owner'}
Sent at: {row['provider_ts']}
Folder: {row['folder']}   Thread position: {row['thread_position']} of {row['message_count']}

Participants:
{who}

Subject: {row['subject']}

--- message text ---
{novel[:8000]}
{quoted}{att_text}
--- end ---

Extract claims. Return an empty list if this email states no request, \
commitment, decision, completion, delivery or cancellation."""


def extract_one(llm: LLM, row: sqlite3.Row, participants: list[sqlite3.Row]) -> dict:
    result = llm.structured(
        system=SYSTEM,
        user=build_user_prompt(row, participants),
        schema=CLAIM_SCHEMA,
        schema_name="email_claims",
    )
    return result


def run(conn: sqlite3.Connection, stats: dict, limit: int | None = None,
        only_email_ids: list[str] | None = None, profile: str | None = None,
        redo: bool = False) -> None:
    seed_vocab(conn)

    primary, fallback = resolve_models()
    print(f"  extracting with {primary} (fallback {fallback})")
    llm = LLM(primary, fallback)

    where = "WHERE s.is_noise = 0"
    params: list = []
    if only_email_ids:
        where += f" AND e.id IN ({','.join('?' * len(only_email_ids))})"
        params += only_email_ids
    if profile:
        where += " AND a.provider_account_id = ?"
        params.append(profile)
    if not redo:
        # Resume: skip emails that already produced evidence or were
        # quarantined, so an interrupted batch can be restarted without
        # paying twice for the same messages.
        where += (
            " AND e.id NOT IN (SELECT source_email_id FROM evidence)"
            " AND e.id NOT IN (SELECT source_email_id FROM evidence_quarantine)"
        )

    sql = f"""
        SELECT e.id, e.user_id, e.thread_id, e.subject, e.body_text_novel,
               e.body_text_full, e.attachments, e.direction, e.provider_ts,
               e.folder, s.thread_position, t.message_count,
               u.display_name AS owner_name, u.timezone,
               a.email_address AS owner_address
        FROM emails e
        JOIN email_signals s ON s.email_id = e.id
        JOIN threads t ON t.id = e.thread_id
        JOIN users u ON u.id = e.user_id
        JOIN accounts a ON a.id = e.account_id
        {where}
        ORDER BY e.provider_ts
    """
    if limit:
        sql += f" LIMIT {limit}"
    rows = conn.execute(sql, params).fetchall()
    stats["items_in"] = len(rows)

    parts: dict[str, list[sqlite3.Row]] = {}
    for p in conn.execute(
        "SELECT email_id, role, position, raw_address, raw_name, is_user "
        "FROM email_participants ORDER BY email_id, role, position"
    ):
        parts.setdefault(p["email_id"], []).append(p)

    people_by_addr = {
        r["address"]: r["person_id"]
        for r in conn.execute("SELECT address, person_id FROM person_identities")
    }

    print(f"  {len(rows)} emails to extract, concurrency {config.CONCURRENCY}")
    done = 0
    aborted = False

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.CONCURRENCY) as pool:
        futures = {
            pool.submit(extract_one, llm, row, parts.get(row["id"], [])): row
            for row in rows
        }
        for future in concurrent.futures.as_completed(futures):
            row = futures[future]
            done += 1
            if done % 100 == 0:
                print(f"    {done}/{len(rows)}  ${llm.usage.cost_usd:.3f}")
            try:
                result = future.result()
            except BudgetExceeded as exc:
                print(f"  ABORT: {exc}")
                aborted = True
                for f in futures:
                    f.cancel()
                break
            except Exception as exc:  # noqa: BLE001
                conn.execute(
                    "INSERT INTO evidence_quarantine (id, user_id, source_email_id, "
                    "raw_claim, rejection_reason, extractor_version, model, observed_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (new_id(), row["user_id"], row["id"], j({"error": str(exc)}),
                     "call_failed", config.EXTRACTOR_VERSION, primary, now_iso()),
                )
                continue

            _persist(conn, row, result, people_by_addr, primary, stats)

    conn.commit()
    stats["notes"] = llm.usage.summary() + (" ABORTED-ON-BUDGET" if aborted else "")
    stats["input_tokens"] = llm.usage.input_tokens
    stats["output_tokens"] = llm.usage.output_tokens
    stats["cost_usd"] = llm.usage.cost_usd
    print(f"  {llm.usage.summary()}")


def _persist(conn, row, result, people_by_addr, model, stats) -> None:
    haystacks = [
        row["body_text_novel"] or "",
        row["body_text_full"] or "",
        row["subject"] or "",
    ]
    for a in json.loads(row["attachments"] or "[]"):
        if a.get("extracted_text"):
            haystacks.append(a["extracted_text"])

    valid_attrs = {a for a, _, _ in VOCAB}
    act_of = {a: s for a, s, _ in VOCAB}

    for claim in result.get("claims", []):
        attribute = claim.get("attribute")
        quote = claim.get("evidence_quote") or ""

        if attribute not in valid_attrs:
            _quarantine(conn, row, claim, "bad_attribute", model)
            continue

        found, offset = find_quote(quote, haystacks)
        if not found:
            # The anti-fabrication guard. A claim whose quote is not in the
            # source never enters the ledger, however plausible it reads.
            _quarantine(conn, row, claim, "quote_not_found", model)
            continue

        # Empty string is the schema's stand-in for "absent"; normalise to None
        # so an unassigned owner stays genuinely unresolved rather than
        # resolving to a person row keyed on "".
        owner = (claim.get("owner_address") or "").strip().lower() or None
        requester = (claim.get("requester_address") or "").strip().lower() or None
        conn.execute(
            "INSERT INTO evidence (id, user_id, source_email_id, thread_id, speech_act, "
            "attribute, value, owner_person_id, requester_person_id, owner_is_self, "
            "owner_resolved, evidence_quote, evidence_offset, quote_verified, "
            "date_surface_form, date_anchor, confidence, extractor_version, model, "
            "prompt_hash, valid_from, observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id(), row["user_id"], row["id"], row["thread_id"],
                act_of[attribute], attribute,
                j({
                    "summary": claim.get("summary"),
                    "is_conditional": bool(claim.get("is_conditional")),
                    "condition": (claim.get("condition") or "").strip() or None,
                }),
                people_by_addr.get(owner), people_by_addr.get(requester),
                1 if (owner and owner == row["owner_address"]) else (0 if owner else None),
                1 if owner else 0,
                quote, offset, 1,
                (claim.get("due_surface_form") or "").strip() or None, row["provider_ts"],
                float(claim.get("confidence") or 0.5),
                config.EXTRACTOR_VERSION, model, PROMPT_HASH,
                row["provider_ts"], now_iso(),
            ),
        )
        stats["items_out"] += 1


def _quarantine(conn, row, claim, reason, model) -> None:
    conn.execute(
        "INSERT INTO evidence_quarantine (id, user_id, source_email_id, raw_claim, "
        "rejection_reason, extractor_version, model, observed_at) VALUES (?,?,?,?,?,?,?,?)",
        (new_id(), row["user_id"], row["id"], j(claim), reason,
         config.EXTRACTOR_VERSION, model, now_iso()),
    )


def main(limit: int | None = None, profile: str | None = None, redo: bool = False) -> None:
    conn = connect()
    with run_record(conn, "extract", config.PRIMARY_MODEL) as stats:
        run(conn, stats, limit=limit, profile=profile, redo=redo)
    kept = conn.execute("SELECT count(*) FROM evidence").fetchone()[0]
    quarantined = conn.execute("SELECT count(*) FROM evidence_quarantine").fetchone()[0]
    verified = conn.execute(
        "SELECT count(*) FROM evidence WHERE quote_verified = 1"
    ).fetchone()[0]
    reasons = conn.execute(
        "SELECT rejection_reason, count(*) n FROM evidence_quarantine "
        "GROUP BY rejection_reason ORDER BY n DESC"
    ).fetchall()
    conn.close()
    print(f"evidence: {kept} claims kept ({verified} quote-verified), {quarantined} quarantined")
    for r in reasons:
        print(f"  quarantined [{r['rejection_reason']}]: {r['n']}")


if __name__ == "__main__":
    main()
