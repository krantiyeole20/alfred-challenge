-- =============================================================================
-- alfred_ email indexing -- SCOPED SCHEMA (PostgreSQL) -- rev 0.4
--
-- 35 tables. v0.4 change: work_items is thread-independent; membership lives
-- in work_item_threads. Contains only what the six questions require.
--
-- Layering contract:
--   L0 raw        emails. Immutable. Never derived from.
--   L1 structural threads, participants, people. Derived, rebuildable.
--   L2 signals    deterministic, no LLM.
--   L3 evidence   append-only LLM claims. Never UPDATEd.
--   L4 projection work_items. Disposable cache, rebuildable by replay.
--   L5 surface    attention, digests, feedback.
--
-- Invariants:
--   1. Nothing above L0 is a source of truth.
--   2. evidence is INSERT-only. Corrections are new rows.
--   3. Projection tables are written only by the reducer.
--   4. The fold orders by emails.provider_ts, NOT evidence.observed_at.
--   5. Every LLM-derived row carries extractor_version and a verified quote.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- 1. IDENTITY
-- =============================================================================

CREATE TABLE users (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name           TEXT,
    timezone               TEXT NOT NULL,          -- "due tomorrow" needs an owner's midnight
    role_profile           TEXT,                   -- drives ranking only, never schema
    extraction_window_days SMALLINT NOT NULL DEFAULT 30,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Provider message IDs are unique only within an account, so every mail-bearing
-- FK points here rather than at users.
CREATE TABLE accounts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL,             -- gmail | outlook
    provider_account_id TEXT NOT NULL,
    email_address       TEXT NOT NULL,
    is_primary          BOOLEAN NOT NULL DEFAULT FALSE,
    connected_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    disconnected_at     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_account_id)
);

CREATE INDEX idx_accounts_user ON accounts (user_id) WHERE disconnected_at IS NULL;

-- Two of six questions are entirely "is the owner me?"
CREATE TABLE user_identities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id  UUID REFERENCES accounts(id) ON DELETE CASCADE,
    address     TEXT NOT NULL,
    source      TEXT NOT NULL,                     -- account | send_as | alias | manual
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, address)
);

-- Written on every sync; separate so it does not churn accounts.updated_at.
CREATE TABLE account_sync_state (
    account_id             UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    surface                TEXT NOT NULL,          -- mail
    cursor                 TEXT,                   -- historyId | deltaLink
    cursor_issued_at       TIMESTAMPTZ,
    last_success_at        TIMESTAMPTZ,
    last_full_reconcile_at TIMESTAMPTZ,            -- push delivery is lossy; poll is the backstop
    last_error             TEXT,
    consecutive_failures   SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, surface)
);

-- Gmail watch expires ~7d, Graph ~3d. This table makes that failure loud.
CREATE TABLE provider_subscriptions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id               UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    surface                  TEXT NOT NULL,
    provider_subscription_id TEXT,
    expires_at               TIMESTAMPTZ NOT NULL,
    last_renewed_at          TIMESTAMPTZ,
    renewal_failures         SMALLINT NOT NULL DEFAULT 0,
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (account_id, surface)
);

CREATE INDEX idx_subscriptions_expiring ON provider_subscriptions (expires_at)
    WHERE is_active;

-- =============================================================================
-- 2. COUNTERPARTIES
-- A person is a cluster of observed addresses. Exact-address resolution only:
-- a wrong merge hides work items silently.
-- =============================================================================

CREATE TABLE people (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    canonical_name TEXT,
    is_self        BOOLEAN NOT NULL DEFAULT FALSE,  -- ownership checks become an FK test
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_people_one_self ON people (user_id) WHERE is_self;

CREATE TABLE person_identities (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id         UUID NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    address           TEXT NOT NULL,
    observed_name     TEXT,
    first_seen_at     TIMESTAMPTZ NOT NULL,
    last_seen_at      TIMESTAMPTZ NOT NULL,
    message_count     INTEGER NOT NULL DEFAULT 0,
    confidence        NUMERIC(3,2),
    resolution_method TEXT NOT NULL,                -- exact | manual (fuzzy deferred)
    resolver_version  TEXT NOT NULL,
    UNIQUE (user_id, address)
);

-- =============================================================================
-- 3. THREADS AND MESSAGES
-- Threads are OUR entity, rebuilt from RFC Message-ID and References.
-- =============================================================================

CREATE TABLE threads (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    root_rfc_message_id  TEXT,
    normalized_subject   TEXT,
    first_message_at     TIMESTAMPTZ,
    last_message_at      TIMESTAMPTZ,
    message_count        INTEGER NOT NULL DEFAULT 0,   -- cache, rebuilt by cluster job
    participant_count    SMALLINT NOT NULL DEFAULT 0,  -- cache
    median_reply_gap_sec BIGINT,                       -- staleness baseline, per-thread
    clustering_version   TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_threads_user_recent ON threads (user_id, last_message_at DESC);

-- Raw MIME in object storage. body_text_novel stays inline: it is the only
-- text extraction reads, and re-extraction becomes a table scan.
CREATE TABLE emails (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id           UUID REFERENCES threads(id),

    provider_message_id TEXT NOT NULL,
    provider_thread_id  TEXT,
    rfc_message_id      TEXT,                      -- cross-provider dedup key
    in_reply_to         TEXT,
    references_chain    TEXT[],

    subject             TEXT,
    normalized_subject  TEXT,
    snippet             TEXT,
    body_text_novel     TEXT,
    body_char_count     INTEGER,
    normalizer_version  TEXT,

    direction           TEXT NOT NULL,             -- inbound | outbound
    provider_ts         TIMESTAMPTZ NOT NULL,      -- ordering truth for the fold
    header_date         TIMESTAMPTZ,               -- client-supplied, skews; never order by this

    has_attachments     BOOLEAN NOT NULL DEFAULT FALSE,
    raw_storage_uri     TEXT NOT NULL,

    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, provider_message_id)
);

CREATE INDEX idx_emails_user_ts     ON emails (user_id, provider_ts DESC);
CREATE INDEX idx_emails_thread      ON emails (thread_id, provider_ts);
CREATE INDEX idx_emails_rfc         ON emails (user_id, rfc_message_id);
CREATE INDEX idx_emails_unthreaded  ON emails (user_id) WHERE thread_id IS NULL;

-- raw_address is retained after resolution: reverting the resolver never
-- destroys the underlying observation.
CREATE TABLE email_participants (
    email_id    UUID NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,                     -- from | to | cc | bcc | reply_to
    position    SMALLINT NOT NULL,
    raw_address TEXT NOT NULL,
    raw_name    TEXT,
    person_id   UUID REFERENCES people(id),
    is_user     BOOLEAN NOT NULL DEFAULT FALSE,    -- denormalized; hot path
    PRIMARY KEY (email_id, role, position)
);

CREATE INDEX idx_participants_person  ON email_participants (person_id);
CREATE INDEX idx_participants_address ON email_participants (raw_address);

-- =============================================================================
-- 4. DETERMINISTIC SIGNALS -- no LLM. is_noise is the cost lever.
--
-- TWO FLAGS, DELIBERATELY SEPARATE:
--   is_automated  mechanism   -- was this machine-sent?
--   is_noise      routing     -- is there anything here worth tracking?
-- Only is_noise gates extraction. Gating on is_automated would silently drop
-- application status changes, receipts, ticket updates and delivery
-- confirmations: all machine-sent from no-reply@, all carrying state that
-- closes or advances a real work item.
-- =============================================================================

CREATE TABLE email_signals (
    email_id          UUID PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE,
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id         UUID REFERENCES threads(id),

    -- mechanism: machine-sent. Retained for aggregates; does NOT gate extraction.
    is_automated      BOOLEAN NOT NULL DEFAULT FALSE,
    automation_reason TEXT,                        -- list_unsubscribe | precedence_bulk | auto_submitted | noreply_local | esp_return_path

    -- routing: bulk mail with nothing to track. THIS is the extraction gate.
    -- Keyed on bulk markers only -- auto_submitted / noreply_local alone are
    -- NOT sufficient, because transactional machine mail carries real state.
    is_noise          BOOLEAN NOT NULL DEFAULT FALSE,
    noise_reason      TEXT,                        -- spam_label | bulk_marketing | newsletter | promotion | advert | list_archive

    user_in_to        BOOLEAN NOT NULL,            -- To vs Cc: cheapest salience signal
    user_in_cc        BOOLEAN NOT NULL,
    user_is_sender    BOOLEAN NOT NULL,
    recipient_count   SMALLINT NOT NULL,

    thread_position   SMALLINT,
    reply_latency_sec BIGINT,                      -- proxy for counterparty importance
    novel_char_count  INTEGER,
    contains_question BOOLEAN,

    date_mentions     JSONB,                       -- [{surface, resolved, anchor_date, char_offset}]

    signal_version    TEXT NOT NULL,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_signals_user_content ON email_signals (user_id) WHERE NOT is_noise;

-- =============================================================================
-- 5. EVIDENCE (APPEND-ONLY LEDGER)
-- =============================================================================

-- Append-only vocabulary. NEVER rename: the fold groups on attribute, so a
-- rename leaves an immortal ghost value. Deprecate and supersede instead.
CREATE TABLE attribute_vocab (
    attribute     TEXT PRIMARY KEY,
    speech_act    TEXT NOT NULL,                   -- request | commitment | decision
    value_schema  JSONB NOT NULL,                  -- JSON Schema, validated at write
    introduced_in TEXT NOT NULL,
    deprecated_in TEXT,
    supersedes    TEXT REFERENCES attribute_vocab(attribute)
);

CREATE TABLE evidence (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_email_id     UUID NOT NULL REFERENCES emails(id),
    thread_id           UUID NOT NULL REFERENCES threads(id),

    speech_act          TEXT NOT NULL,
    attribute           TEXT NOT NULL REFERENCES attribute_vocab(attribute),
    value               JSONB NOT NULL,

    owner_person_id     UUID REFERENCES people(id),
    requester_person_id UUID REFERENCES people(id),
    owner_is_self       BOOLEAN,
    owner_resolved      BOOLEAN NOT NULL DEFAULT FALSE,

    -- anti-fabrication: quote must be found verbatim in the source body
    evidence_quote      TEXT NOT NULL,
    evidence_offset     INTEGER,
    quote_verified      BOOLEAN NOT NULL DEFAULT FALSE,

    date_surface_form   TEXT,                      -- "next Thursday"
    date_anchor         TIMESTAMPTZ,               -- what we resolved it against

    confidence          NUMERIC(3,2) NOT NULL,
    extractor_version   TEXT NOT NULL,
    model               TEXT NOT NULL,
    prompt_hash         TEXT NOT NULL,

    valid_from          TIMESTAMPTZ,               -- when true in the world
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),  -- when we learned it

    -- NULLABLE ON PURPOSE: evidence precedes the work item it justifies
    work_item_id        UUID,

    superseded_by       UUID REFERENCES evidence(id),
    invalidated_at      TIMESTAMPTZ,
    invalidation_reason TEXT
);

CREATE INDEX idx_evidence_fold ON evidence (user_id, thread_id, attribute, observed_at DESC)
    WHERE invalidated_at IS NULL;
CREATE INDEX idx_evidence_recent    ON evidence (user_id, observed_at DESC);
CREATE INDEX idx_evidence_email     ON evidence (source_email_id);
CREATE INDEX idx_evidence_work_item ON evidence (work_item_id);

-- Quality gate at WRITE time. Rejection rate by reason is a fabrication alarm.
CREATE TABLE evidence_quarantine (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_email_id   UUID NOT NULL REFERENCES emails(id),
    thread_id         UUID REFERENCES threads(id),
    payload           JSONB NOT NULL,              -- the rejected extraction, verbatim
    reason            TEXT NOT NULL,               -- low_confidence | quote_not_found | schema_invalid | unknown_attribute
    confidence        NUMERIC(3,2),
    extractor_version TEXT NOT NULL,
    quarantined_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_quarantine_reason ON evidence_quarantine (reason, quarantined_at DESC);

-- Ambiguous ownership waits here. Promotion at TTL keeps owner NULL.
CREATE TABLE pending_evidence (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id            UUID NOT NULL REFERENCES threads(id),
    source_email_id      UUID NOT NULL REFERENCES emails(id),
    attribute            TEXT NOT NULL REFERENCES attribute_vocab(attribute),
    value                JSONB NOT NULL,
    evidence_quote       TEXT NOT NULL,
    ambiguity_type       TEXT NOT NULL,            -- owner | date | amount | subject
    candidate_owners     UUID[],
    confidence           NUMERIC(3,2) NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at           TIMESTAMPTZ NOT NULL,
    resolved_at          TIMESTAMPTZ,
    resolution           TEXT,                     -- promoted_unconfirmed | expired | superseded | user_resolved
    promoted_evidence_id UUID REFERENCES evidence(id)
);

CREATE INDEX idx_pending_open      ON pending_evidence (thread_id) WHERE resolved_at IS NULL;
CREATE INDEX idx_pending_expiring  ON pending_evidence (expires_at) WHERE resolved_at IS NULL;

CREATE TABLE sweeper_runs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    swept_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    scanned_count    INTEGER NOT NULL,
    promoted_count   INTEGER NOT NULL,
    expired_count    INTEGER NOT NULL,
    superseded_count INTEGER NOT NULL,
    sweeper_version  TEXT NOT NULL
);

-- =============================================================================
-- 6. PROJECTION -- disposable, written ONLY by the reducer.
-- v0.4: work_items is THREAD-INDEPENDENT. Membership lives in
-- work_item_threads; origin_thread_id is a display anchor only.
-- =============================================================================

CREATE TABLE work_items (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    origin_thread_id    UUID REFERENCES threads(id),  -- v0.4: display anchor only

    speech_act          TEXT NOT NULL,             -- request | commitment | decision
    title               TEXT NOT NULL,

    owner_person_id     UUID REFERENCES people(id),
    requester_person_id UUID REFERENCES people(id),
    owner_is_self       BOOLEAN,
    owner_resolved      BOOLEAN NOT NULL DEFAULT FALSE,

    status              TEXT NOT NULL,             -- OPEN | RESOLVED | CANCELLED | EXPIRED
    is_unconfirmed      BOOLEAN NOT NULL DEFAULT FALSE,  -- promoted from pending at TTL

    due_at              TIMESTAMPTZ,
    due_confidence      NUMERIC(3,2),

    first_seen_email_id UUID NOT NULL REFERENCES emails(id),
    last_activity_at    TIMESTAMPTZ NOT NULL,
    opened_at           TIMESTAMPTZ NOT NULL,
    resolved_at         TIMESTAMPTZ,

    match_key           TEXT NOT NULL,             -- v0.4: speech_act + owner + normalized title
    reducer_version     TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_work_items_open_self ON work_items (user_id, due_at NULLS LAST)
    WHERE status = 'OPEN' AND owner_is_self;
CREATE INDEX idx_work_items_open_other ON work_items (user_id, last_activity_at)
    WHERE status = 'OPEN' AND owner_is_self = FALSE;
CREATE UNIQUE INDEX idx_work_items_match ON work_items (user_id, match_key);

-- NEW in v0.4: one obligation, many conversations. Cross-thread attachment
-- requires a stricter match threshold than same-thread.
CREATE TABLE work_item_threads (
    work_item_id          UUID NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    thread_id             UUID NOT NULL REFERENCES threads(id),
    first_linked_email_id UUID REFERENCES emails(id),
    linked_by             TEXT NOT NULL,           -- matcher | user | promotion
    link_score            NUMERIC(4,3),            -- NULL when linked_by = user
    linked_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (work_item_id, thread_id)
);

CREATE INDEX idx_wit_thread ON work_item_threads (thread_id);

ALTER TABLE evidence
    ADD CONSTRAINT fk_evidence_work_item
    FOREIGN KEY (work_item_id) REFERENCES work_items(id);

-- Both matching failure modes are silent. Logged so it can be measured.
-- v0.4: match_features carries thread_overlap for cross-thread audits.
CREATE TABLE work_item_matches (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    evidence_id            UUID NOT NULL REFERENCES evidence(id),
    candidate_work_item_id UUID REFERENCES work_items(id),
    chosen_work_item_id    UUID REFERENCES work_items(id),
    decision               TEXT NOT NULL,          -- matched_existing | created_new | rejected
    match_score            NUMERIC(4,3),
    match_features         JSONB,                  -- {thread_overlap, speech_act, owner, title_sim, due_delta}
    threshold              NUMERIC(4,3) NOT NULL,
    matcher_version        TEXT NOT NULL,
    decided_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_matches_evidence ON work_item_matches (evidence_id);

-- Field-level, not status-level. What makes "what changed" a range scan.
CREATE TABLE work_item_changes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    work_item_id        UUID NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    field               TEXT NOT NULL,             -- status | due_at | owner | title | speech_act
    prior_value         JSONB,
    new_value           JSONB NOT NULL,
    change_type         TEXT NOT NULL,             -- created | updated | resolved | cancelled | expired
    prior_evidence_id   UUID REFERENCES evidence(id),
    new_evidence_id     UUID NOT NULL REFERENCES evidence(id),
    triggering_email_id UUID NOT NULL REFERENCES emails(id),
    reducer_version     TEXT NOT NULL,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_changes_recent ON work_item_changes (user_id, detected_at DESC);
CREATE INDEX idx_changes_item   ON work_item_changes (work_item_id, detected_at DESC);

-- =============================================================================
-- 7. ATTENTION -- one candidate pool feeds three questions.
-- =============================================================================

CREATE TABLE attention_candidates (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    work_item_id      UUID NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    reason            TEXT NOT NULL,               -- due_soon | overdue | stale | unanswered_request | unfulfilled_commitment | repeated_followup

    -- each term normalized to 0..1 BEFORE weighting
    urgency_norm      NUMERIC(4,3) NOT NULL,
    importance_norm   NUMERIC(4,3) NOT NULL,
    staleness_norm    NUMERIC(4,3) NOT NULL,
    commitment_norm   NUMERIC(4,3) NOT NULL,
    score             NUMERIC(6,4) NOT NULL,
    score_breakdown   JSONB NOT NULL,              -- per-term contribution

    generator_version TEXT NOT NULL,
    generated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ
);

CREATE INDEX idx_attention_rank ON attention_candidates (user_id, score DESC, generated_at DESC);
CREATE UNIQUE INDEX idx_attention_dedup ON attention_candidates (user_id, work_item_id, reason);

-- Suppression is a FILTER, not a score penalty.
CREATE TABLE attention_suppressions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    work_item_id     UUID NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    suppression_type TEXT NOT NULL,                -- dismissed | snoozed | resolved | muted_thread
    suppressed_until TIMESTAMPTZ,                  -- NULL = permanent
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_suppressions_active ON attention_suppressions (user_id, work_item_id);

-- =============================================================================
-- 8. SURFACE
-- =============================================================================

-- Explicit window columns: a missed run widens the next window, no silent gap.
CREATE TABLE digests (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    window_start      TIMESTAMPTZ NOT NULL,
    window_end        TIMESTAMPTZ NOT NULL,
    role_profile      TEXT,
    item_count        SMALLINT NOT NULL DEFAULT 0,
    generator_version TEXT NOT NULL,
    generated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, window_start, window_end)
);

CREATE TABLE digest_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    digest_id       UUID NOT NULL REFERENCES digests(id) ON DELETE CASCADE,
    rank            SMALLINT NOT NULL,
    section         TEXT NOT NULL,                 -- owed_by_you | awaiting_others | changed | unconfirmed
    work_item_id    UUID REFERENCES work_items(id),
    change_id       UUID REFERENCES work_item_changes(id),
    evidence_id     UUID REFERENCES evidence(id),
    salience_score  NUMERIC(6,4) NOT NULL,
    was_exploration BOOLEAN NOT NULL DEFAULT FALSE,
    headline        TEXT NOT NULL,
    UNIQUE (digest_id, rank)
);

CREATE TABLE role_profiles (
    role_profile TEXT PRIMARY KEY,                 -- founder | marketing_exec | vp_finance | head_hr | consulting_president | generic
    description  TEXT
);

-- Day one has no behavioural data; priors beat pure recency.
CREATE TABLE role_feature_priors (
    role_profile TEXT NOT NULL REFERENCES role_profiles(role_profile),
    feature      TEXT NOT NULL,                    -- attribute:* | speech_act:* | signal:*
    prior_weight NUMERIC(6,4) NOT NULL,
    PRIMARY KEY (role_profile, feature)
);

-- Impressions logged, not just clicks: presentation bias is self-confirming.
CREATE TABLE feedback_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    digest_item_id  UUID REFERENCES digest_items(id),
    work_item_id    UUID REFERENCES work_items(id),
    thread_id       UUID REFERENCES threads(id),
    event_type      TEXT NOT NULL,                 -- impression | open | reply | dismiss | snooze | pin | mark_done
    shown_rank      SMALLINT,
    was_exploration BOOLEAN NOT NULL DEFAULT FALSE,
    dwell_ms        INTEGER,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_feedback_user ON feedback_events (user_id, occurred_at DESC);
CREATE INDEX idx_feedback_item ON feedback_events (digest_item_id);

-- =============================================================================
-- 9. PIPELINE CONTROL
-- =============================================================================

-- The push carries a cursor, not a message. Handler writes one row, returns.
CREATE TABLE ingestion_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id      UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    source          TEXT NOT NULL,                 -- push | poll | backfill | manual
    provider_cursor TEXT,
    payload         JSONB,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'pending'  -- pending | processing | done | failed
);

CREATE INDEX idx_ingestion_pending ON ingestion_events (account_id, received_at)
    WHERE status = 'pending';

-- Delivery dedup != processing dedup. This prevents double-extraction after
-- a crash between the model call and the insert.
CREATE TABLE processing_state (
    email_id      UUID NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    stage         TEXT NOT NULL,                   -- normalize | signals | cluster | extract | reconcile
    stage_version TEXT NOT NULL,
    status        TEXT NOT NULL,                   -- pending | running | done | failed | skipped
    attempt_count SMALLINT NOT NULL DEFAULT 0,
    last_error    TEXT,
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    PRIMARY KEY (email_id, stage, stage_version)
);

CREATE INDEX idx_processing_failed ON processing_state (stage, status) WHERE status = 'failed';

-- Newest-first; depth split from retention. The cursor separates a hiccup
-- from a disaster.
CREATE TABLE backfill_jobs (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id         UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    depth              TEXT NOT NULL,              -- raw_only | signals | full_extraction
    range_start        TIMESTAMPTZ,
    range_end          TIMESTAMPTZ,
    page_cursor        TEXT,
    messages_fetched   INTEGER NOT NULL DEFAULT 0,
    messages_extracted INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'pending',
    last_checkpoint_at TIMESTAMPTZ,
    started_at         TIMESTAMPTZ,
    completed_at       TIMESTAMPTZ
);

-- A silent failure is a MISSING OBLIGATION, not a crash.
CREATE TABLE dead_letter_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stage           TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_id       UUID NOT NULL,
    payload         JSONB,
    error_class     TEXT NOT NULL,
    error_detail    TEXT,
    attempt_count   SMALLINT NOT NULL,
    first_failed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_failed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX idx_dlq_open ON dead_letter_queue (stage, last_failed_at DESC)
    WHERE resolved_at IS NULL;

-- =============================================================================
-- 10. EVALUATION -- errors multiply across the chain.
-- =============================================================================

CREATE TABLE eval_labels (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component    TEXT NOT NULL,                    -- clustering | entity_resolution | extraction | matching | ranking
    entity_type  TEXT NOT NULL,
    entity_id    UUID NOT NULL,
    expected     JSONB NOT NULL,
    label_source TEXT NOT NULL,                    -- human | user_correction | synthetic
    labeled_by   TEXT,
    labeled_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes        TEXT
);

CREATE INDEX idx_eval_labels_component ON eval_labels (component, entity_type, entity_id);

CREATE TABLE eval_runs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component         TEXT NOT NULL,
    component_version TEXT NOT NULL,
    label_count       INTEGER NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    git_sha           TEXT
);

CREATE TABLE eval_results (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_run_id  UUID NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    label_id     UUID NOT NULL REFERENCES eval_labels(id),
    actual       JSONB NOT NULL,
    outcome      TEXT NOT NULL,                    -- match | mismatch | miss | spurious
    error_detail TEXT
);

CREATE INDEX idx_eval_results_run ON eval_results (eval_run_id, outcome);

CREATE TABLE eval_metrics (
    eval_run_id UUID NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    metric      TEXT NOT NULL,                     -- precision | recall | f1 | ndcg
    value       NUMERIC(6,4) NOT NULL,
    slice       TEXT,                              -- optional cohort: role, volume band
    PRIMARY KEY (eval_run_id, metric, slice)
);

-- =============================================================================
-- REDUCER AND QUERY REFERENCE
-- =============================================================================

-- Current value per (thread, attribute). Ordered by the SOURCE EMAIL's
-- provider_ts, not observed_at.
CREATE VIEW v_current_thread_facts AS
SELECT DISTINCT ON (e.user_id, e.thread_id, e.attribute)
       e.user_id,
       e.thread_id,
       e.attribute,
       e.value,
       e.confidence,
       e.id AS source_evidence_id,
       e.extractor_version
FROM evidence e
JOIN emails m ON m.id = e.source_email_id
WHERE e.invalidated_at IS NULL
ORDER BY e.user_id, e.thread_id, e.attribute, m.provider_ts DESC, e.observed_at DESC;

-- "What is waiting on me?" -- derived from ownership, never a stored status.
CREATE VIEW v_awaiting_me AS
SELECT w.*
FROM work_items w
WHERE w.status = 'OPEN'
  AND w.owner_is_self
  AND NOT EXISTS (
      SELECT 1 FROM attention_suppressions s
      WHERE s.work_item_id = w.id
        AND (s.suppressed_until IS NULL OR s.suppressed_until > now())
  );

-- "What am I waiting on someone else for?" -- same query, one boolean flipped.
CREATE VIEW v_awaiting_others AS
SELECT w.*
FROM work_items w
WHERE w.status = 'OPEN'
  AND w.owner_is_self = FALSE
  AND NOT EXISTS (
      SELECT 1 FROM attention_suppressions s
      WHERE s.work_item_id = w.id
        AND (s.suppressed_until IS NULL OR s.suppressed_until > now())
  );

-- "What changed?"
-- SELECT c.field, c.prior_value, c.new_value, c.detected_at,
--        w.title, e.evidence_quote, m.subject
-- FROM work_item_changes c
-- JOIN work_items w ON w.id = c.work_item_id
-- JOIN evidence   e ON e.id = c.new_evidence_id
-- JOIN emails     m ON m.id = c.triggering_email_id
-- WHERE c.user_id = $1 AND c.detected_at >= $2
-- ORDER BY c.detected_at DESC;

-- =============================================================================
-- DEFERRED -- deliberate omissions, not oversights
--
--   thread_segments     Topic drift within a thread. BIGGEST KNOWN GAP.
--   message_embeddings  Semantic search. No question in scope needs it.
--   query_route_cache   Query routing. All six queries are fixed shapes.
--   organizations       Company-level importance. Reply latency substitutes.
--   salience_weights    Learned ranking. Feedback is logged from day one.
--   person_merges       Fuzzy identity merging. Exact match only for now.
--   thread_merges       Late-arriving replies joining two threads.
--   alert_fires         Push notification firing state.
--   segment_facts       Non-obligation facts (launch dates, quoted amounts).
--   money_mentions      Spend aggregation over history.
--   labels/folders      Not read by any of the six questions.
--   drafts, deletions   Mutable and tombstoned respectively; neither needed yet.
--   calendar            Different object, different mutation rules.
--
--   (cross-thread work items: promoted into v0.4 as work_item_threads)
-- =============================================================================
