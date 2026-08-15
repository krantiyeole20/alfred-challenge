"""The six questions, as parameterized SQL.

One source of truth, used twice: the scorer runs these against the gold sets,
and the demo agent exposes them as its six fixed tools. If they ever diverge,
the thing being scored stops being the thing being demoed.

Every query returns work items with the evidence that justifies them, so an
answer carries its citation by construction rather than by prompting.
"""

from __future__ import annotations

from . import config

# Columns every question returns, so callers can render results uniformly.
_SELECT = """
    SELECT
        wi.id                AS work_item_id,
        wi.title             AS title,
        wi.speech_act        AS speech_act,
        wi.status            AS status,
        wi.due_at            AS due_at,
        wi.owner_is_self     AS owner_is_self,
        wi.owner_resolved    AS owner_resolved,
        wi.last_activity_at  AS last_activity_at,
        wi.opened_at         AS opened_at,
        owner.canonical_name AS owner_name,
        req.canonical_name   AS requester_name,
        th.id                AS thread_id,
        em.provider_message_id AS anchor_message_id,
        em.subject           AS subject,
        ev.evidence_quote    AS evidence_quote
    FROM work_items wi
    LEFT JOIN people owner ON owner.id = wi.owner_person_id
    LEFT JOIN people req   ON req.id   = wi.requester_person_id
    LEFT JOIN threads th   ON th.id    = wi.origin_thread_id
    JOIN emails em         ON em.id    = wi.first_seen_email_id
    LEFT JOIN evidence ev  ON ev.id = (
        SELECT e2.id FROM evidence e2
        WHERE e2.work_item_id = wi.id AND e2.quote_verified = 1
        ORDER BY e2.observed_at LIMIT 1
    )
"""

QUESTIONS: dict[str, dict] = {
    # ---------------------------------------------------------------- #
    "q1_needs_attention": {
        "label": "What needs my attention?",
        "why": "A ranking over open state. Ordered by the attention score, "
               "which weights urgency, importance, staleness and commitment.",
        "trigger": "Scheduled — attention rebuild, daily; deadline scan, hourly.",
        "reads": ["work_items", "attention_candidates", "evidence", "emails"],
        "pipeline": [
            ("Signals", "Noise gate decides what reaches the model at all.", "email_signals"),
            ("Extract", "One LLM call per message emits claims with verbatim quotes.", "evidence"),
            ("Reduce", "Claims fold into work items by match_key; nothing is overwritten.", "work_items"),
            ("Rank", "urgency 0.40 · importance 0.25 · staleness 0.20 · commitment 0.15", "attention_candidates"),
            ("Read", "Pure SQL join. No model involved in answering.", None),
        ],
        "sql": f"""
            {_SELECT}
            JOIN attention_candidates ac ON ac.work_item_id = wi.id
            WHERE wi.user_id = :user_id AND wi.status = 'OPEN'
            ORDER BY ac.score DESC
            LIMIT :limit
        """,
    },
    # ---------------------------------------------------------------- #
    "q2_forgetting": {
        "label": "What am I forgetting?",
        "why": "The absence of an expected event: the owner's own promises "
               "with no completion recorded, weighted toward the ones that "
               "have gone quiet longest.",
        "trigger": "Scheduled — commitment scan, daily.",
        "reads": ["work_items", "evidence", "emails"],
        "pipeline": [
            ("Extract", "commitment.action and commitment.attachment claims are recorded.", "evidence"),
            ("Reduce", "A later completion.action closes the item it matches.", "work_items"),
            ("Detect", "What is left OPEN is a promise with no closing event.", None),
            ("Read", "Filter to owner_is_self, order by silence.", None),
        ],
        "sql": f"""
            {_SELECT}
            WHERE wi.user_id = :user_id
              AND wi.status = 'OPEN'
              AND wi.speech_act = 'commitment'
              AND wi.owner_is_self = 1
              AND julianday(:as_of) - julianday(wi.last_activity_at) >= 3
            ORDER BY julianday(:as_of) - julianday(wi.last_activity_at) DESC
            LIMIT :limit
        """,
    },
    # ---------------------------------------------------------------- #
    "q3_waiting_on_me": {
        "label": "What is waiting on me?",
        "why": "Open state the owner owns: requests addressed to them plus "
               "commitments they made themselves.",
        "trigger": "Scheduled — waiting scan, daily.",
        "reads": ["work_items", "people", "evidence"],
        "pipeline": [
            ("Participants", "Every address resolves to a person; the owner's own addresses are flagged via user_identities.", "people"),
            ("Extract", "Each claim records who must act, not who sent the mail.", "evidence"),
            ("Reduce", "owner_is_self is decided once, on the fold, not per query.", "work_items"),
            ("Read", "An indexed lookup on (user_id, owner_is_self, status).", None),
        ],
        "sql": f"""
            {_SELECT}
            WHERE wi.user_id = :user_id
              AND wi.status = 'OPEN'
              AND wi.owner_is_self = 1
            ORDER BY
                CASE WHEN wi.due_at IS NULL THEN 1 ELSE 0 END,
                wi.due_at ASC,
                wi.last_activity_at DESC
            LIMIT :limit
        """,
    },
    # ---------------------------------------------------------------- #
    "q4_waiting_on_others": {
        "label": "What am I waiting on?",
        "why": "The same state with ownership inverted: someone else owes the "
               "action, and nothing has closed it.",
        "trigger": "Scheduled — waiting scan, daily. Detects counterparty silence.",
        "reads": ["work_items", "people", "threads"],
        "pipeline": [
            ("Participants", "Ownership is inverted from the same resolved identities.", "people"),
            ("Reduce", "Items owned by anyone but the mailbox owner stay OPEN until closed.", "work_items"),
            ("Staleness", "Measured against the thread's own median reply gap, not a fixed number of days.", "threads"),
            ("Read", "Same table, opposite ownership filter.", None),
        ],
        "sql": f"""
            {_SELECT}
            WHERE wi.user_id = :user_id
              AND wi.status = 'OPEN'
              AND (wi.owner_is_self = 0 OR wi.owner_is_self IS NULL)
            ORDER BY
                CASE WHEN wi.due_at IS NULL THEN 1 ELSE 0 END,
                wi.due_at ASC,
                julianday(:as_of) - julianday(wi.last_activity_at) DESC
            LIMIT :limit
        """,
    },
    # ---------------------------------------------------------------- #
    "q5_what_changed": {
        "label": "What changed?",
        "why": "A comparison between two points in time. This is a range scan "
               "over the change log, not a generation task.",
        "trigger": "Event-driven — a change row is written before the projection, in the same transaction.",
        "reads": ["work_item_changes", "work_items", "evidence"],
        "pipeline": [
            ("Extract", "A later claim about the same thing is appended, never overwritten.", "evidence"),
            ("Reduce", "Before any field is updated, the prior value is written to the change log.", "work_item_changes"),
            ("Supersede", "The older evidence row is marked superseded_by, so the chain is auditable.", "evidence"),
            ("Read", "A range scan over changed_at. This is why it is not a generation task.", None),
        ],
        "sql": """
            SELECT
                wic.change_type      AS change_type,
                wic.old_value        AS old_value,
                wic.new_value        AS new_value,
                wic.changed_at       AS changed_at,
                wi.id                AS work_item_id,
                wi.title             AS title,
                wi.speech_act        AS speech_act,
                wi.status            AS status,
                wi.due_at            AS due_at,
                wi.owner_is_self     AS owner_is_self,
                th.id                AS thread_id,
                em.provider_message_id AS anchor_message_id,
                em.subject           AS subject,
                ev.evidence_quote    AS evidence_quote
            FROM work_item_changes wic
            JOIN work_items wi ON wi.id = wic.work_item_id
            LEFT JOIN threads th ON th.id = wi.origin_thread_id
            JOIN emails em ON em.id = wi.first_seen_email_id
            LEFT JOIN evidence ev ON ev.id = wic.evidence_id
            WHERE wic.user_id = :user_id
              AND julianday(:as_of) - julianday(wic.changed_at) <= :window_days
              AND wic.change_type <> 'opened'
            ORDER BY wic.changed_at DESC
            LIMIT :limit
        """,
    },
    # ---------------------------------------------------------------- #
    "q6_slipping_through_cracks": {
        "label": "What's slipping through the cracks?",
        "why": "Open state plus unusual silence: nothing has moved, and either "
               "the deadline has passed or nobody ever claimed ownership.",
        "trigger": "Scheduled — staleness scan, daily.",
        "reads": ["work_items", "attention_candidates", "threads"],
        "pipeline": [
            ("Reduce", "Items with no closing event remain OPEN indefinitely; silence never closes one.", "work_items"),
            ("Staleness", "Quiet time is compared against the thread's own rhythm.", "threads"),
            ("Ownership", "An item nobody ever claimed (owner_resolved = 0) qualifies on its own.", None),
            ("Read", "Overdue, or quiet, or unclaimed - ordered by how long it has been still.", None),
        ],
        "sql": f"""
            {_SELECT}
            WHERE wi.user_id = :user_id
              AND wi.status = 'OPEN'
              AND (
                    julianday(:as_of) - julianday(wi.last_activity_at) >= 7
                 OR (wi.due_at IS NOT NULL AND julianday(wi.due_at) < julianday(:as_of))
                 OR wi.owner_resolved = 0
              )
            ORDER BY julianday(:as_of) - julianday(wi.last_activity_at) DESC
            LIMIT :limit
        """,
    },
}


def ask(conn, question: str, user_id: str, limit: int = 25,
        as_of: str | None = None, window_days: int = 14) -> list[dict]:
    """Run one of the six questions for one mailbox."""
    if question not in QUESTIONS:
        raise KeyError(f"unknown question {question!r}; expected one of {list(QUESTIONS)}")
    rows = conn.execute(
        QUESTIONS[question]["sql"],
        {
            "user_id": user_id,
            "limit": limit,
            "as_of": as_of or config.AS_OF,
            "window_days": window_days,
        },
    ).fetchall()
    return [dict(r) for r in rows]
