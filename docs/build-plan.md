# Build plan — from corpus to live demo

Two phases. Phase 1 fills the schema from the mock corpus. Phase 2 puts it on
the internet behind an agent. This document is the contract for both; it records
the decisions so they don't get re-litigated halfway through.

## Decisions already made

| Question | Answer | Why |
|---|---|---|
| Extraction model | GPT-5.6 **Luna** (`$0.20/$1.20` per MTok) | A full 1,500-email pass costs ~$1.70. At that price the whole corpus is disposable and re-runnable. |
| Upgrade path | Score against gold, re-extract **only failures** on Sol | A blanket Sol re-run is ~$42 and mostly re-pays for emails the cheap model already got right. |
| Demo hosting | Static page + Cloudflare Worker key-proxy | $0 forever, no cold starts, nothing to keep alive. |
| Demo data | Read-only SQLite file, queried in-browser via WASM | The corpus is ~3.4 MB. It fits in a download; a database server buys nothing. |
| Demo UI | Agent-first; mail browsing is evidence | Citations are the point. Mail exists to prove they're real. |
| Agent tools | 6 fixed SQL tools + read-only freeform SQL | Deterministic answers by construction, with range for off-script questions. |
| Agent model | Same GPT tier as extraction | One key, one SDK, one cost model in the Worker. |

Rejected: **Supabase** (free projects pause after a week of inactivity and need
*manual* unpause — a demo link that dies quietly is worse than no demo link),
and **Neon** (auto-resumes fine, but it's a database server for data nobody
writes to).

## Phase 1 — fill the schema

```
src/pipeline/
  schema_sqlite.sql   the shipped subset of the 35 tables, SQLite dialect
  config.py           env loading, model tiers, cost table
  llm.py              OpenAI client: retries, fallback chain, cost accounting
  load_corpus.py      canonical JSONL -> users/people/threads/emails/participants
  signals.py          deterministic email_signals (no LLM) — sets the noise gate
  extract.py          non-noise emails -> evidence, with quote verification
  reduce.py           evidence -> work_items -> attention_candidates
  score.py            output vs data/gold_set.json + data/gold/validation/
  run.py              CLI: load | signals | extract | reduce | score | all
```

Pipeline order, each stage idempotent and independently re-runnable:

```
load → signals → extract → reduce → score
                    ↑ only stage that costs money
```

### The noise gate is the cost lever

`email_signals.is_noise` decides what reaches the extractor. Per the schema's
own comment, it keys on **bulk markers only** — `List-Unsubscribe`,
`Precedence: bulk`, promotional categories, spam. It does *not* gate on
`is_automated`, because transactional machine mail (bounces, renewals, security
alerts, application status changes) carries real state that opens and closes
work items. Getting this wrong in either direction is expensive: too aggressive
and we drop real obligations, too permissive and we pay to extract newsletters.

### Attribute vocabulary

`attribute_vocab` is unseeded in the design — it's a placeholder. Phase 1 seeds
it, derived from the semantics in `docs/QUESTIONS.md`:

| attribute | speech_act | closes |
|---|---|---|
| `request.action` | request | `completion.action` |
| `commitment.action` | commitment | `completion.action` |
| `commitment.attachment` | commitment | `delivery.attachment` |
| `decision.value` | decision | superseded by a later `decision.value` |
| `completion.action` | decision | — |
| `delivery.attachment` | decision | — |
| `cancellation` | decision | — |

Append-only. Never rename an attribute — the fold groups on it, so a rename
leaves an immortal ghost value. Deprecate and supersede instead.

### Anti-fabrication

Every evidence row carries `evidence_quote`, checked verbatim against the source
`body_text_novel` (or an attachment's `extracted_text`). A quote that isn't
found sets `quote_verified = FALSE` and the row goes to `evidence_quarantine`
rather than into the fold. This is the one guard that makes "every answer
carries a citation by construction" true rather than aspirational.

### Adversarial cases the extractor must survive

All eleven planted cases are indexed in `data/ground_truth.jsonl` and tagged in
`meta.tricky_tags`. The ones that break cheap models specifically:
`lookalike_domain` (invoice fraud from `kettlehq-billing.com`),
`shifting_number` / `superseded` (a value that changes across three emails),
`quoted_text_only_task` and `task_in_attachment_only` (the task lives where lazy
parsers don't look), `diffused_group_ask` (six recipients, nobody named),
`conditional_promise`, and `same_person_two_addresses`. The scorer reports per
tag so we can see exactly which ones need Sol.

## Phase 2 — the demo

```
web/app/            the demo (separate from web/docs, which is the design doc)
worker/             Cloudflare Worker: key proxy + rate limit + budget cap
```

- **Frontend**: static. Loads `alfred.db` once, runs it in WASM, keeps it in
  memory for the session.
- **Worker**: holds `OPENAI_API_KEY`, enforces a per-IP rate limit and a **hard
  global daily call cap in D1**. The global cap is what actually protects the
  bill; the per-IP limit only stops casual hammering.
- **Tool execution stays in the browser.** The Worker never sees the data — it
  relays model turns and the page runs the SQL locally. The freeform SQL tool is
  therefore safe by construction: it queries a throwaway copy in the user's own
  tab, with no backend to attack and nothing to corrupt.

Tables that ship to the browser are the read path only — identity, people,
threads, emails, participants, signals, evidence, work_items, attention,
projection. Build-time tables (`dead_letter_queue`, `backfill_jobs`,
`processing_state`, `sweeper_runs`, `ingestion_events`, `eval_*`) stay
server-side. The full 35 remain the design; the demo ships what answers
questions.

## Setup

One manual step — create `.env` (already gitignored):

```bash
OPENAI_API_KEY=sk-proj-...
```

Everything else has a default in `src/pipeline/config.py`. Model IDs are
confirmed against the live account with `python src/pipeline/run.py list-models`
rather than guessed, since the pricing pages don't publish the exact strings.
