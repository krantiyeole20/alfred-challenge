// ============================================================================
// FLOW DATA — one object per dataflow. Each flow renders as stage cards and
// optionally as a decision graph; `defaultView` picks which opens first.
// ============================================================================

const FLOWS = [
  {
    id: 'ingestion',
    title: 'One email, end to end',
    intro: 'An email arrives. Fifteen steps take it from provider notification to a refreshed attention surface. The decision graph shows where messages exit — most volume never reaches the model. Switch to Stages for the step-by-step narrative; hover a step for what breaks if it is skipped.',
    defaultView: 'graph',
    stages: [
      {
        label: 'Ingest', sub: 'L0 · raw, immutable',
        steps: [
          { n: 1, title: 'Notification', desc: 'The provider’s push does not contain the email — it contains a marker saying something changed, come look. Write one ingestion_events row, return immediately.', skip: 'Doing fetch and extraction inside the handler blocks mailbox sync; the provider starts timing out and backing off.' },
          { n: 2, title: 'Fetch', desc: 'A background worker reads the marker and pulls the changed messages. The cursor advances only after the batch commits.', skip: 'Advance the cursor first and a crash loses that window permanently — provider history cursors expire and cannot be re-read.' },
          { n: 3, title: 'Deduplicate', desc: 'Check (account_id, provider_message_id). If present, stop.', skip: 'Distributed systems retry; without this, duplicates.' },
          { n: 4, title: 'Store raw', desc: 'Full message to object storage, row into emails. From this moment the message is permanent and everything else is rebuildable.' },
        ],
      },
      {
        label: 'Structure', sub: 'L1–L2 · derived, deterministic',
        steps: [
          { n: 5, title: 'Normalize', desc: 'Parse MIME, HTML → text, strip the quoted chain and signature, write body_text_novel.', skip: 'Every reply repeats the entire thread beneath it. Without stripping, a five-message thread contains message one five times and extraction cost scales quadratically with thread length.' },
          { n: 6, title: 'Resolve participants', desc: 'Map each address to a person; flag which are the user.', skip: 'Ownership questions cannot be answered.' },
          { n: 7, title: 'Compute signals', desc: 'Noise vs content, automated vs human, To vs Cc, reply latency, date mentions. No model involved — the noise flag is read from headers because it sits on the cost path for every message.', skip: 'This is the gate that decides whether extraction runs at all.' },
          { n: 8, title: 'Resolve thread', desc: 'Use References and Message-ID to attach the message to a conversation, creating one if needed. Provider-neutral, so a dual-account user never sees the same conversation twice.' },
        ],
      },
      {
        label: 'Extract', sub: 'L3 · append-only evidence',
        steps: [
          { n: 9, title: 'Extract', desc: 'If not noise: send the novel text plus the novel text of the previous 3–5 messages in the thread, token-capped, thread root always included. Extraction stays blind to prior conclusions on purpose.', skip: 'If the model sees what we already believe, it anchors on it and confirms it — claims become a chain of self-agreement, which is precisely what destroys change detection.' },
          { n: 10, title: 'Validate', desc: 'Quote must appear verbatim in the body; value must match the attribute’s schema; confidence must clear the floor.', skip: 'Failures go to evidence_quarantine; ambiguous ownership goes to pending_evidence.' },
          { n: 11, title: 'Write evidence', desc: 'Insert. Never update.' },
        ],
      },
      {
        label: 'Project & surface', sub: 'L4–L5 · disposable projections',
        steps: [
          { n: 12, title: 'Reconcile', desc: 'Existing work item or new? The decision is logged in work_item_matches; v0.4: evidence from a different thread can attach to an existing item via work_item_threads, at a stricter threshold.' },
          { n: 13, title: 'Emit changes', desc: 'If a field’s value differs, write a work_item_changes row BEFORE overwriting, in the same transaction.', skip: 'Reverse the order and a crash between the two loses the prior value permanently — "what changed" gets a hole nobody can see.' },
          { n: 14, title: 'Re-check pending', desc: 'Ambiguous claims waiting on this thread get one more look with the new context.' },
          { n: 15, title: 'Generate candidates', desc: 'Refresh attention_candidates for affected items.' },
        ],
      },
    ],
    graph: {
      height: 1150,
      nodes: [
        { id: 'notify', y: 40, label: '1 · notification', sub: 'ack in <100ms', info: 'The push carries a cursor, not a message. One ingestion_events row, return immediately — slow handling makes the provider time out and back off.', writes: ['ingestion_events'] },
        { id: 'fetch', y: 110, label: '2 · fetch', sub: 'cursor advances after commit', info: 'A worker reads the sync cursor and pulls the changed messages. The cursor advances only after the batch commits — provider history cursors expire and a lost window cannot be re-read. Push delivery is lossy; a 6-hourly reconcile poll is the backstop.' },
        { id: 'g-dedup', y: 185, gate: true, label: 'seen before?', spineLabel: 'no — new message', info: 'Check (account_id, provider_message_id). Distributed systems retry — this lookup makes retries free. Processing dedup is separate: (email_id, stage, stage_version) in processing_state.' },
        { id: 'store', y: 265, label: '4 · store raw', sub: 'immutable from here', info: 'Full MIME to object storage, row into emails. Raw is large and read almost never; the novel text stays in Postgres because it is the only thing extraction reads, and re-running extraction becomes a table scan instead of thousands of fetches.', writes: ['emails'] },
        { id: 'norm', y: 335, label: '5 · normalize', sub: 'novel text only', info: 'Parse MIME, strip quoted chain and signature. Without stripping, a five-message thread contains message one five times and cost scales quadratically.', writes: ['emails.body_text_novel', 'email_participants'] },
        { id: 'people', y: 405, label: '6 · participants', sub: 'who is the user', info: 'Map each address to a person; flag the user’s own addresses via user_identities. “Waiting on me vs. them” is decided here.', writes: ['people', 'person_identities'] },
        { id: 'signals', y: 475, label: '7 · signals', sub: 'deterministic, no LLM', info: 'Noise vs content, automated vs human, To vs Cc, reply latency, date mentions. Both flags are read from headers, never by model judgment — they sit on the cost path for every message.', writes: ['email_signals'] },
        { id: 'g-noise', y: 550, gate: true, label: 'noise?', spineLabel: 'no — carries content', info: 'The gate that protects the model budget: roughly two thirds of volume exits here. Noise is bulk mail with nothing to track — spam, newsletters, adverts, promotions, list archives — keyed on List-Unsubscribe + Precedence: bulk + ESP return-path + provider spam/promotions labels. NOT the same as automated: Auto-Submitted and no-reply alone do not qualify, because an application status change, a receipt, or a ticket update is machine-sent and still carries state a work item depends on.' },
        { id: 'thread', y: 630, label: '8 · thread', sub: 'References / Message-ID', info: 'Attach the message to our own conversation entity — Gmail and Outlook group by different rules, so threads are rebuilt from RFC headers.', writes: ['threads', 'emails.thread_id'] },
        { id: 'extract', y: 700, label: '9 · extract', sub: 'blind to prior beliefs', info: 'Novel text + the previous 3–5 novel texts go to the model, token-capped, thread root always included. Current work-item state is deliberately excluded — otherwise claims anchor on prior conclusions and change detection dies. Queue consumer, not a cron: a backfill burst of 400 emails must not become 400 simultaneous model calls.' },
        { id: 'g-valid', y: 775, gate: true, label: 'claim valid?', spineLabel: 'valid + owned', info: 'Three checks per claim: quote appears verbatim · value fits the attribute schema · confidence clears the floor. Then: is the owner unambiguous?' },
        { id: 'evidence', y: 860, label: '11 · evidence', sub: 'INSERT-only', info: 'Claims that pass land as append-only evidence rows carrying extractor_version, model, prompt_hash and the verified quote.', writes: ['evidence'] },
        { id: 'reconcile', y: 930, label: '12 · reconcile', sub: 'fold by provider_ts', info: 'Attach each claim to an existing work item or open a new one; every decision is logged with score and threshold. v0.4: cross-thread attachment goes through work_item_threads at a stricter threshold. The fold orders by the source email’s provider_ts.', writes: ['work_items', 'work_item_matches', 'work_item_threads'] },
        { id: 'g-diff', y: 1005, gate: true, label: 'value changed?', spineLabel: 'yes', info: 'If the folded value differs from the projection, the transition is recorded BEFORE the overwrite, in one transaction.', writes: ['work_item_changes'] },
        { id: 'cand', y: 1090, label: '15 · candidates', sub: 'attention refresh', info: 'attention_candidates refresh for affected items — scoped delete + rewrite, never upsert, because scores decay and reasons expire.', writes: ['attention_candidates'] },
      ],
      exits: [
        { id: 'x-stop', at: 'g-dedup', y: 185, cls: 'stop', label: 'stop', sub: 'duplicate — already stored', branchLabel: 'yes', info: 'Retries and double-deliveries exit here. No row is written twice.' },
        { id: 'x-skip', at: 'g-noise', y: 550, cls: 'ok', label: 'index-only lane', sub: 'no LLM · signals kept', branchLabel: 'yes — bulk / spam', rejoin: 'cand', info: 'Noise is NEVER discarded. It skips extraction, but is stored and signalled over full history — volume trends, automated-vs-human ratios, sender behaviour and "how much of my inbox is marketing" all come from this lane. Extraction cost stays proportional to mail that carries trackable content.' },
        { id: 'x-quar', at: 'g-valid', y: 745, cls: 'bad', label: 'quarantine', sub: 'failed verification', branchLabel: 'invalid', info: 'Quote not verbatim, value off-schema, or confidence below floor → evidence_quarantine, payload verbatim. A spike in quote_not_found means the model started fabricating.' },
        { id: 'x-pend', at: 'g-valid', y: 815, cls: 'warn', label: 'pending', sub: 'ambiguous owner', branchLabel: 'ambiguous owner', loop: 'reconcile', loopLabel: 're-checked on new mail (step 14)', info: '"We’ll circle back Friday" — who is we? Parked in pending_evidence; re-checked when the thread next moves, promoted unconfirmed at TTL with owner NULL — never with a guessed owner.' },
      ],
    },
  },

  {
    id: 'jobs-pipeline',
    title: 'The event-driven pipeline',
    intro: 'What runs when something happens. Extraction is the only model call on the write path; everything downstream of it is deterministic and unit-testable. Every stage records an idempotency row in processing_state keyed (email_id, stage, stage_version).',
    defaultView: 'stages',
    stages: [
      {
        label: 'Receive', sub: 'continuous',
        steps: [
          { n: 1, title: 'Webhook receiver', desc: 'Continuous. Writes ingestion_events, nothing else.' },
          { n: 2, title: 'Sync', desc: 'Queue. Writes emails and advances the cursor — only after the batch commits.' },
        ],
      },
      {
        label: 'Derive', sub: 'queue · no LLM',
        steps: [
          { n: 3, title: 'Normalize', desc: 'Writes body_text_novel and participants. Deterministic, freely re-runnable.' },
          { n: 4, title: 'Resolve people', desc: 'Writes people, person_identities. Exact-address only.' },
          { n: 5, title: 'Signals', desc: 'Writes email_signals. Full history, every message.' },
          { n: 6, title: 'Cluster threads', desc: 'Writes threads. Rebuilt from Message-ID / References.' },
        ],
      },
      {
        label: 'Extract', sub: 'queue, concurrency-capped',
        steps: [
          { n: 7, title: 'Extract', desc: 'The only LLM on the write path. A queue consumer, NOT a cron — a backfill burst of 400 emails must not become 400 simultaneous model calls.', skip: 'Uncapped concurrency turns bursts into rate-limit breakage; capped, a burst degrades latency instead.' },
        ],
      },
      {
        label: 'Project', sub: 'deterministic',
        steps: [
          { n: 8, title: 'Reconcile', desc: 'Folds evidence by provider_ts, emits work_item_changes before the projection write.' },
          { n: 9, title: 'Attention', desc: 'Incremental refresh on reconcile; full rebuild daily.' },
          { n: 10, title: 'Digest', desc: 'Daily per timezone. Explicit window columns — a missed run widens the next window instead of leaving a silent gap.' },
        ],
      },
    ],
  },
];
