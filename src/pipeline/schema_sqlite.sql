-- SQLite translation of the shipped subset of the design schema.
--
-- The design (web/docs/data/schema.sql) is PostgreSQL and has 35 tables. This
-- is the read path only: identity, people, mail, signals, evidence, work items,
-- attention. Build-time tables (dead_letter_queue, backfill_jobs,
-- processing_state, sweeper_runs, ingestion_events, eval_*) are not here --
-- they exist to operate a live pipeline, and a static demo has none.
--
-- Dialect mapping from the Postgres original:
--   UUID / gen_random_uuid()  -> TEXT, uuid4 generated in Python
--   TIMESTAMPTZ               -> TEXT, ISO-8601 with offset (sorts correctly)
--   JSONB                     -> TEXT, json.dumps
--   NUMERIC(p,s)              -> REAL
--   BOOLEAN                   -> INTEGER 0/1
--   TEXT[]                    -> TEXT, JSON array
--   Partial indexes           -> kept; SQLite supports WHERE on indexes

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- =============================================================================
-- 1. IDENTITY
-- =============================================================================

CREATE TABLE IF NOT EXISTS users (
    id                     TEXT PRIMARY KEY,
    display_name           TEXT,
    timezone               TEXT NOT NULL,   -- "due tomorrow" needs an owner's midnight
    role_profile           TEXT,
    extraction_window_days INTEGER NOT NULL DEFAULT 30,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    email_address       TEXT NOT NULL,
    is_primary          INTEGER NOT NULL DEFAULT 0,
    connected_at        TEXT NOT NULL,
    disconnected_at     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (provider, provider_account_id)
);

CREATE INDEX IF NOT EXISTS idx_accounts_user
    ON accounts (user_id) WHERE disconnected_at IS NULL;

-- Two of the six questions are entirely "is the owner me?"
CREATE TABLE IF NOT EXISTS user_identities (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    address    TEXT NOT NULL,
    source     TEXT NOT NULL,   -- account | send_as | alias | manual
    created_at TEXT NOT NULL,
    UNIQUE (user_id, address)
);

-- =============================================================================
-- 2. COUNTERPARTIES
-- =============================================================================

CREATE TABLE IF NOT EXISTS people (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    canonical_name TEXT,
    is_self        INTEGER NOT NULL DEFAULT 0,  -- ownership checks become an FK test
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_people_one_self
    ON people (user_id) WHERE is_self = 1;

CREATE TABLE IF NOT EXISTS person_identities (
    id                TEXT PRIMARY KEY,
    person_id         TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    address           TEXT NOT NULL,
    observed_name     TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    message_count     INTEGER NOT NULL DEFAULT 0,
    confidence        REAL,
    resolution_method TEXT NOT NULL,   -- exact | manual
    resolver_version  TEXT NOT NULL,
    UNIQUE (user_id, address)
);

-- Two addresses that share a display name but sit on confusingly similar
-- domains. Deliberately NOT merged into one person: an impersonator reuses
-- their target's name, so merging would erase the fraud. Surfaced instead.
CREATE TABLE IF NOT EXISTS identity_conflicts (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    observed_name TEXT,
    address_a     TEXT NOT NULL,   -- the address seen first / more often
    address_b     TEXT NOT NULL,   -- the suspicious lookalike
    relation      TEXT NOT NULL,   -- lookalike
    message_count_a INTEGER NOT NULL DEFAULT 0,
    message_count_b INTEGER NOT NULL DEFAULT 0,
    detected_at   TEXT NOT NULL,
    resolver_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_identity_conflicts_user ON identity_conflicts (user_id);

-- =============================================================================
-- 3. THREADS AND MESSAGES
-- =============================================================================

CREATE TABLE IF NOT EXISTS threads (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    root_rfc_message_id  TEXT,
    normalized_subject   TEXT,
    first_message_at     TEXT,
    last_message_at      TEXT,
    message_count        INTEGER NOT NULL DEFAULT 0,
    participant_count    INTEGER NOT NULL DEFAULT 0,
    median_reply_gap_sec INTEGER,   -- staleness baseline, per thread
    clustering_version   TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_threads_user_recent
    ON threads (user_id, last_message_at DESC);

CREATE TABLE IF NOT EXISTS emails (
    id                  TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    user_id             TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id           TEXT REFERENCES threads(id),

    provider_message_id TEXT NOT NULL,
    provider_thread_id  TEXT,
    rfc_message_id      TEXT,
    in_reply_to         TEXT,
    references_chain    TEXT,        -- JSON array

    subject             TEXT,
    normalized_subject  TEXT,
    snippet             TEXT,
    body_text_novel     TEXT,        -- quoted text stripped; what extraction reads
    body_text_full      TEXT,        -- retained: quoted_text_only_task lives here
    body_char_count     INTEGER,
    normalizer_version  TEXT,

    direction           TEXT NOT NULL,   -- inbound | outbound
    provider_ts         TEXT NOT NULL,   -- ordering truth for the fold
    header_date         TEXT,            -- client-supplied, skews; never order by this

    has_attachments     INTEGER NOT NULL DEFAULT 0,
    attachments         TEXT,        -- JSON array incl. extracted_text
    raw_storage_uri     TEXT NOT NULL,

    folder              TEXT,
    category            TEXT,
    is_unread           INTEGER NOT NULL DEFAULT 0,
    is_starred          INTEGER NOT NULL DEFAULT 0,
    is_important        INTEGER NOT NULL DEFAULT 0,

    ingested_at         TEXT NOT NULL,
    UNIQUE (account_id, provider_message_id)
);

CREATE INDEX IF NOT EXISTS idx_emails_user_ts    ON emails (user_id, provider_ts DESC);
CREATE INDEX IF NOT EXISTS idx_emails_thread     ON emails (thread_id, provider_ts);
CREATE INDEX IF NOT EXISTS idx_emails_rfc        ON emails (user_id, rfc_message_id);

CREATE TABLE IF NOT EXISTS email_participants (
    email_id    TEXT NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,   -- from | to | cc | bcc | reply_to
    position    INTEGER NOT NULL,
    raw_address TEXT NOT NULL,   -- retained after resolution, on purpose
    raw_name    TEXT,
    person_id   TEXT REFERENCES people(id),
    is_user     INTEGER NOT NULL DEFAULT 0,   -- denormalized; hot path
    PRIMARY KEY (email_id, role, position)
);

CREATE INDEX IF NOT EXISTS idx_participants_person  ON email_participants (person_id);
CREATE INDEX IF NOT EXISTS idx_participants_address ON email_participants (raw_address);

-- =============================================================================
-- 4. DETERMINISTIC SIGNALS -- no LLM. is_noise is the cost lever.
--
-- is_automated is mechanism (was this machine-sent?).
-- is_noise is routing (is there anything here worth tracking?).
-- Only is_noise gates extraction: transactional machine mail is automated but
-- carries real state that opens and closes work items.
-- =============================================================================

CREATE TABLE IF NOT EXISTS email_signals (
    email_id          TEXT PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE,
    user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id         TEXT REFERENCES threads(id),

    is_automated      INTEGER NOT NULL DEFAULT 0,
    automation_reason TEXT,

    is_noise          INTEGER NOT NULL DEFAULT 0,   -- THE extraction gate
    noise_reason      TEXT,

    user_in_to        INTEGER NOT NULL,
    user_in_cc        INTEGER NOT NULL,
    user_is_sender    INTEGER NOT NULL,
    recipient_count   INTEGER NOT NULL,

    thread_position   INTEGER,
    reply_latency_sec INTEGER,
    novel_char_count  INTEGER,
    contains_question INTEGER,

    date_mentions     TEXT,   -- JSON: [{surface, resolved, anchor_date, char_offset}]

    signal_version    TEXT NOT NULL,
    computed_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_user_content
    ON email_signals (user_id) WHERE is_noise = 0;

-- =============================================================================
-- 5. EVIDENCE (APPEND-ONLY LEDGER)
-- =============================================================================

-- Append-only vocabulary. NEVER rename: the fold groups on attribute, so a
-- rename leaves an immortal ghost value. Deprecate and supersede instead.
CREATE TABLE IF NOT EXISTS attribute_vocab (
    attribute     TEXT PRIMARY KEY,
    speech_act    TEXT NOT NULL,   -- request | commitment | decision
    value_schema  TEXT NOT NULL,   -- JSON Schema, validated at write
    introduced_in TEXT NOT NULL,
    deprecated_in TEXT,
    supersedes    TEXT REFERENCES attribute_vocab(attribute)
);

CREATE TABLE IF NOT EXISTS evidence (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_email_id     TEXT NOT NULL REFERENCES emails(id),
    thread_id           TEXT NOT NULL REFERENCES threads(id),

    speech_act          TEXT NOT NULL,
    attribute           TEXT NOT NULL REFERENCES attribute_vocab(attribute),
    value               TEXT NOT NULL,   -- JSON

    owner_person_id     TEXT REFERENCES people(id),
    requester_person_id TEXT REFERENCES people(id),
    owner_is_self       INTEGER,
    owner_resolved      INTEGER NOT NULL DEFAULT 0,

    -- anti-fabrication: quote must be found verbatim in the source body
    evidence_quote      TEXT NOT NULL,
    evidence_offset     INTEGER,
    quote_verified      INTEGER NOT NULL DEFAULT 0,

    date_surface_form   TEXT,   -- "next Thursday"
    date_anchor         TEXT,   -- what we resolved it against

    confidence          REAL NOT NULL,
    extractor_version   TEXT NOT NULL,
    model               TEXT NOT NULL,
    prompt_hash         TEXT NOT NULL,

    valid_from          TEXT,   -- when true in the world
    observed_at         TEXT NOT NULL,   -- when we learned it

    -- NULLABLE ON PURPOSE: evidence precedes the work item it justifies
    work_item_id        TEXT,

    superseded_by       TEXT REFERENCES evidence(id),
    invalidated_at      TEXT,
    invalidation_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_evidence_user_observed ON evidence (user_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_thread        ON evidence (thread_id);
CREATE INDEX IF NOT EXISTS idx_evidence_work_item     ON evidence (work_item_id);
CREATE INDEX IF NOT EXISTS idx_evidence_source        ON evidence (source_email_id);

-- Quotes that could not be found verbatim in the source. Never enters the fold.
CREATE TABLE IF NOT EXISTS evidence_quarantine (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    source_email_id   TEXT NOT NULL,
    raw_claim         TEXT NOT NULL,   -- JSON, as the model emitted it
    rejection_reason  TEXT NOT NULL,   -- quote_not_found | bad_attribute | schema_invalid
    extractor_version TEXT NOT NULL,
    model             TEXT NOT NULL,
    observed_at       TEXT NOT NULL
);

-- =============================================================================
-- 6. PROJECTION -- the deterministic fold. No LLM on this path.
-- =============================================================================

CREATE TABLE IF NOT EXISTS work_items (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    origin_thread_id    TEXT REFERENCES threads(id),   -- display anchor only

    speech_act          TEXT NOT NULL,   -- request | commitment | decision
    title               TEXT NOT NULL,

    owner_person_id     TEXT REFERENCES people(id),
    requester_person_id TEXT REFERENCES people(id),
    owner_is_self       INTEGER,
    owner_resolved      INTEGER NOT NULL DEFAULT 0,

    status              TEXT NOT NULL,   -- OPEN | RESOLVED | CANCELLED | EXPIRED
    is_unconfirmed      INTEGER NOT NULL DEFAULT 0,

    due_at              TEXT,
    due_confidence      REAL,

    first_seen_email_id TEXT NOT NULL REFERENCES emails(id),
    last_activity_at    TEXT NOT NULL,
    opened_at           TEXT NOT NULL,
    resolved_at         TEXT,

    match_key           TEXT NOT NULL,   -- speech_act + owner + normalized title
    reducer_version     TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_work_items_open
    ON work_items (user_id, status, due_at) WHERE status = 'OPEN';
CREATE INDEX IF NOT EXISTS idx_work_items_match  ON work_items (user_id, match_key);
CREATE INDEX IF NOT EXISTS idx_work_items_owner  ON work_items (user_id, owner_is_self, status);

CREATE TABLE IF NOT EXISTS work_item_threads (
    work_item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    thread_id    TEXT NOT NULL REFERENCES threads(id),
    PRIMARY KEY (work_item_id, thread_id)
);

-- "What changed?" is a range scan over this table, not a generation task.
CREATE TABLE IF NOT EXISTS work_item_changes (
    id            TEXT PRIMARY KEY,
    work_item_id  TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    user_id       TEXT NOT NULL,
    change_type   TEXT NOT NULL,   -- opened | due_moved | owner_changed | resolved | cancelled | value_superseded
    old_value     TEXT,            -- JSON
    new_value     TEXT,            -- JSON
    evidence_id   TEXT REFERENCES evidence(id),
    changed_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_changes_user_time ON work_item_changes (user_id, changed_at DESC);

-- =============================================================================
-- 7. ATTENTION -- ranking over open state
-- =============================================================================

CREATE TABLE IF NOT EXISTS attention_candidates (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    work_item_id      TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    reason            TEXT NOT NULL,   -- due_soon | overdue | stale | unanswered_request | unfulfilled_commitment | repeated_followup

    -- each term normalized to 0..1 BEFORE weighting
    urgency_norm      REAL NOT NULL,
    importance_norm   REAL NOT NULL,
    staleness_norm    REAL NOT NULL,
    commitment_norm   REAL NOT NULL,
    score             REAL NOT NULL,
    score_breakdown   TEXT NOT NULL,   -- JSON, per-term contribution

    generator_version TEXT NOT NULL,
    generated_at      TEXT NOT NULL,
    expires_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_attention_rank ON attention_candidates (user_id, score DESC);

-- =============================================================================
-- 8. RUN METADATA -- what produced this database
-- =============================================================================

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id             TEXT PRIMARY KEY,
    stage          TEXT NOT NULL,   -- load | signals | extract | reduce | score
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,   -- running | ok | failed
    model          TEXT,
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd       REAL NOT NULL DEFAULT 0,
    items_in       INTEGER NOT NULL DEFAULT 0,
    items_out      INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    notes          TEXT
);
