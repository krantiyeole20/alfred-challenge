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
