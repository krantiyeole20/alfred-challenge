# alfred_ — presentation study guide

Everything needed to present this project, defend the decisions, and answer
the hard questions. Numbers verified against the build on 2026-08-18.

- **Demo** — https://krantiyeole20.github.io/alfred-challenge/demo/
- **Design doc** — https://krantiyeole20.github.io/alfred-challenge/
- **Repo** — https://github.com/krantiyeole20/alfred-challenge

---

## 1. The 60-second pitch

> The six questions alfred_ has to answer look like search problems. They aren't.
> "What changed?" needs two points in time. "What am I forgetting?" needs the
> *absence* of an event. A retrieval pipeline is stateless per query — there's
> nothing to compare against, so it invents the answer.
>
> So I store a **ledger of claims over time** instead. One LLM call per email
> extracts claims with verbatim quotes. It never decides current state, never
> updates, never deletes. A deterministic reducer folds those claims into
> current state, and the six questions become indexed SQL.
>
> **The LLM proposes. Plain code disposes.**
>
> The result: 1,500 emails → 1,198 quote-verified claims → 1,069 work items,
> for $0.77. Every answer on screen carries the sentence it came from.

**If you say one sentence, say this:** the model is confined to the write
path; the read path is pure SQL.

---

## 2. Why the six questions aren't search

Put this table up early. It's the whole argument.

| Question | What it actually requires | Why retrieval fails |
|---|---|---|
| What is waiting on me? | State that has **persisted** | Needs open/closed status, not similarity |
| What am I waiting on? | Same state, ownership **inverted** | Needs to know who owes whom |
| What changed? | A **comparison between two points in time** | Stateless per query — nothing to diff |
| What am I forgetting? | The **absence** of an expected event | You cannot retrieve a thing that isn't there |
| What needs attention? | A **ranking** over open state | Needs a total order, not top-k similarity |
| What's slipping? | Open state **plus unusual silence** | Silence has no embedding |

Four of the six are temporal or negative. **None is semantic.**

---

## 3. Architecture — five stages

```
data/profiles/*.canonical.jsonl
        │
  ①  load_corpus.py     no LLM   →  users, people, threads, emails, participants
        │                            + identity resolution + impersonation scan
  ②  signals.py         no LLM   →  email_signals (is_automated, is_noise, dates)
        │
  ③  extract.py         1 LLM call per email  →  evidence (quote-verified)
        │                                        evidence_quarantine (rejects)
  ④  reduce.py          no LLM   →  work_items, work_item_changes,
        │                            attention_candidates
  ⑤  export_web.py      no LLM   →  alfred.db.gz (2.6 MB) for the browser
        │
  score.py              no LLM   →  recall vs the gold sets
```

Run any stage independently; all are idempotent. Only ③ costs money and it
aborts at `ALFRED_BUDGET_USD`.

```bash
.venv/bin/python -m src.pipeline.run all
```

### Where the money went

| Stage | Calls | Tokens in / out | Cost |
|---|---|---|---|
| extract, first pass (`gemini-3.5-flash-lite`) | 1,367 | 1,078,609 / 157,140 | $0.716 |
| `--retry-failures` (`gemini-3.6-flash`) | 4 | 3,050 / 6,398 | $0.053 |
| extract, after the gate fix | 615 | 481,538 / 12,244 | $0.175 |
| everything else | 0 | — | $0.000 |
| | | **total** | **$0.944** |

⚠️ That third row is a lesson worth telling: resuming re-processed **615**
emails to add 61 claims, because resume skipped "emails with evidence" — and
most email correctly produces none, so it paid for the empty ones again. Now
fixed with an `extraction_attempts` table keyed on extractor version. A resume
is now 0 calls and $0.00.

0 retries, 0 fallbacks on the main run — worth mentioning, it means the
schema survived 1,367 consecutive calls without a shape failure.

---

## 4. Current numbers (memorise these)

| Metric | Value |
|---|---|
| Mailboxes | 5 (300 messages each) |
| Emails | 1,500 |
| Threads | 971 |
| People / identities | 373 / 443 |
| Signals rows | 1,500 (279 automated, 132 noise-gated → 1,368 extracted) |
| **Evidence claims** | **1,259 — every one quote-verified** |
| Quarantined | 2 |
| Work items | 1,126 (1,062 open) |
| Change rows | 1,197 |
| **Impersonations caught** | **10** |
| Tests | 49 passing |
| Shipped DB | 2.6 MB gzipped |
| **Recall vs gold** | **34.7%** (59 of 170) |

---

## 5. The data model

**18 tables implemented in SQLite.** The design doc describes a 35-table
PostgreSQL schema; the implementation ships the read path plus what the
pipeline needs. ⚠️ **Do not claim 35 tables are implemented** — say "35 in the
design, 18 built, 16 shipped to the browser."

### The spine

```
emails ──< email_participants >── people ──< person_identities
   │                                │
   │                                └── identity_conflicts   (impersonation)
   ├──  email_signals               (1:1, deterministic)
   │
   └──< evidence                    (claims; append-only)
             │  work_item_id, superseded_by
             ▼
        work_items ──< work_item_changes    (the "what changed" log)
             │
             └──> attention_candidates      (the ranking)
```

### Tables that carry the argument

| Table | Why it exists |
|---|---|
| `evidence` | **Append-only claims.** Never updated. `superseded_by` points forward to the claim that replaced it, so history survives. |
| `evidence_quarantine` | Claims whose quote could not be found in the source. Rejected, not trusted. This is what makes citations true by construction. |
| `work_items` | The **fold** of evidence into current state. Derived, rebuildable, disposable. |
| `work_item_changes` | Written **in the same transaction** as the projection update. This is why "what changed" is a range scan, not a generation. |
| `attention_candidates` | The ranking. Score = urgency 0.40, importance 0.25, staleness 0.20, commitment 0.15. |
| `identity_conflicts` | Sender/domain pairs that look like impersonation. Deliberately *not* merged. |
| `email_search` | Plain lowercased blob + index. **Not FTS5** — see §9. |
| `extraction_attempts` | Every email processed, with claim count, keyed on extractor version. Lets resume tell "produced nothing" from "not reached". |

---

## 6. The twelve agent tools

The agent runs **in the browser**. Tool calls execute against the local SQLite
copy; only the conversation goes to the model. The proxy never sees the mail.

Model: `gemini-3.5-flash-lite`. Budget: **12 hops** per question.

### The six ranked questions — one tool each

| Tool | Answers | Params |
|---|---|---|
| `needs_attention` | "what should I look at", "what's urgent", "catch me up" | none |
| `forgetting` | "what am I forgetting", "did I drop anything" | none |
| `waiting_on_me` | "what's on my plate", "what do I owe people" | none |
| `waiting_on_others` | "who owes me", "who hasn't replied" | none |
| `what_changed` | "what moved", "what's new" | `window_days` (default 14) |
| `slipping` | "what's falling through the cracks", "what's stale" | none |

> **Key point for the call:** these six run the **byte-identical SQL** that the
> scorer measures, imported from `questions.py`. The thing being demoed and the
> thing being scored cannot drift. That's why `questions.py` is the single
> source of truth and the frontend reads exported JSON rather than a hand-copy.

### Lookup and escape-hatch tools

| Tool | Purpose | Notes |
|---|---|---|
| `mail_from_person` | Mail involving a person, by name or address | **Participant lookup, not text.** Added after a real failure — see §9 |
| `search_mail` | Text search across the mailbox | Terms AND, then retried OR |
| `read_thread` | Every message in a thread, oldest first | Used after search when an excerpt isn't enough |
| `suspected_impersonation` | Lookalike senders — invoice fraud, phishing | Reads `identity_conflicts` |
| `run_sql` | Read-only SELECT for anything uncovered | Guarded — see below |
| `get_schema` | CREATE TABLE statements | Called before writing SQL |

### How `run_sql` is guarded

```js
if (!/^\s*(select|with)\b/i.test(s)) return { error: "Only SELECT / WITH queries are allowed." };
if (/;/.test(s))                     return { error: "One statement at a time." };
// results capped at 50 rows
```

**Why a freeform SQL tool is safe here:** it runs against a disposable copy in
the visitor's own browser. There is no server-side database to attack, no other
tenant's data, and the file is already public. The blast radius is the tab.

⚠️ **Known gap, be ready for this question.** `run_sql` does *not* enforce
mailbox scoping. The tool description asks the model to filter by
`user_id = :user_id`, but nothing rejects a query that omits it:

```
SELECT count(*) FROM work_items WHERE user_id = :user_id   →   229   (Aditi)
SELECT count(*) FROM work_items                            →  1069   (all five)
```

Honest answer: *"It's advisory, not enforced. All five mailboxes are synthetic
and already in the downloaded file, so it isn't a data-leak — but if this were
real multi-tenant data I'd wrap every query in a scoped view or reject any
statement whose parse tree doesn't bind user_id."*

---

## 7. Telemetry

Every tool call records a structured entry, in the page and in the console:

```js
{ hop, name, args, rows, failed, error, hint, at }
```

- `agent.telemetry()` returns the full trace for the session.
- `console.debug("[alfred tool]", entry)` on every call.
- The UI renders each call as a chip: **running** (pulsing dot) → **done**
  (green, with row count) or **failed** (red).

**Why this exists:** a silent tool failure is exactly what turned one broken
search into a six-hop stall (§9). A tool that returns zero rows and a tool that
threw used to look identical. Now they don't.

**Zero-row responses carry a hint back to the model**, e.g.:

> "No text match. If the question names a person, call `mail_from_person`
> instead — it searches participants, not body text."

That's cheaper than letting the model guess for six hops.

---

## 8. Demo script — what to click, in what order

**Total: about four minutes.** Rehearse this exact path.

### 0. Open the design doc first (10s)
Land on https://krantiyeole20.github.io/alfred-challenge/ — the demo button is
top-left with a live dot. Click it. This shows the doc and demo are one system.

### 1. The guide (20s)
The demo lands on a guide, not a mailbox. Read the one line that matters aloud:

> "The six questions are the agent's tools — one per question, each running the
> same SQL the scorer is measured against."

### 2. Pick Maya Rodriguez (founder) (10s)
Say: *"Five mailboxes, five different jobs, so five different kinds of mess."*

### 3. Question 01 — What needs my attention? (40s)
Point at the ranked list. Then **click any citation.** The source message opens
with the quote highlighted exactly where it was found.

> "Nothing reaches the ledger unless its quote verifies character-for-character
> against the source. Two claims failed that check and sit in quarantine."

### 4. Open "How this answer is produced" (40s)
Shows the trigger, the pipeline stages, what each writes, and the SQL that ran.

> "This isn't a chatbot explaining itself. It's the actual job definition."

### 5. Ask the agent — the fraud question (60s)
Type: **`is anything in here a scam?`**

Watch the chip say `suspected impersonation · 2 rows`, then the streamed answer
naming `accounts@kettlehq-billing.com`.

> "That's a lookalike of Kettle's *own* domain. It was caught at load time by
> deterministic domain comparison — no model involved in the detection."

### 6. Ask indirectly (30s)
Type: **`did I drop anything?`** → routes to `forgetting`, one hop.

> "The six aren't keywords. Ask sideways and the model still picks the right tool."

### 7. Close on the honest number (20s)
Go straight to it before anyone finds it. See §11.

**Backup if the network dies:** the six questions and all citations work
offline — only the chat needs the model. Say so and keep going.

---

## 9. Design decisions — what, why, and why not

### ① Ledger of claims, not retrieval over chunks
- **Why:** four of the six questions are temporal or negative. You cannot
  retrieve an absence.
- **Why not the alternative:** RAG is stateless per query. "What changed" has
  nothing to compare against, so the model invents a plausible diff.
- **Cost:** one LLM call per email up front instead of per query. $0.77 once.

### ② The LLM only proposes; a reducer disposes
- **Why:** ownership, deadlines, and status must be reproducible and testable.
  A reducer can be unit-tested; a prompt cannot.
- **Why not:** letting the model decide state means the answer changes between
  runs and no test can pin it.

### ③ Every claim carries a verbatim quote, checked before insert
- **Why:** it makes "every answer carries a citation" true **by construction**
  rather than by prompting.
- **Consequence:** 2 claims of 1,261 were rejected. Prefer that to a fluent lie.
- **Trade-off:** costs recall. A real obligation phrased across two sentences
  can fail the check and be dropped.

### ④ Append-only evidence with `superseded_by`
- **Why:** a superseded fact is still evidence of what was believed and when.
  Deleting it destroys "what changed".
- **Why not update in place:** you lose the audit trail and the diff.

### ⑤ Identity: merge on name, but **never across lookalike domains**
- **The trap:** merging two addresses under one person on an identical display
  name solves `same_person_two_addresses`. Done naively it *also* folds an
  impersonator into the person they're impersonating — and the fraud signal
  vanishes.
- **The discriminator:** registrable domain, compared **at a token boundary**.
  - `email.united.com` vs `united.com` → same registrable domain → **merge**
  - `klaviyo-billing.com` vs `klaviyo.com` → appends a word to the brand → **never merge**, record a conflict
- **Why token boundary, not substring:** a bare `in` test also flags
  `vanta.com` against `vantageassurance.com` (unrelated) and `google.com`
  against `googlemail.com` (same company). Both were false positives; both
  are gone.
- **The asymmetry that justifies it:** splitting a pair that was really one
  party costs a little recall. Merging an impersonator into their target
  destroys the fraud signal outright. Not symmetric, so bias toward splitting.

### ⑥ Domain-level impersonation scan, independent of display name
- **Why:** the name-collision check only fires when an attacker reuses a real
  *name*. The more dangerous shape reuses a real *domain* under a new name —
  "Kettle Compliance Certification" writing from `kettlehq-billing.com`.
  Nothing collides, so nothing was flagged, and the owner is told there's no
  fraud. **The demo actively said "no suspicious emails found" for Maya.**
- **How found:** generating a README screenshot. Worth telling — it's a good
  story about why you look at your own output.
- **Result:** 10 impersonations across 5 mailboxes, 0 false positives.

### ⑦ The noise gate is narrow, and that was measured

Reproducible: `python tools/measure_noise_gate.py` → `results/noise_gate_report.txt`.
"Real work lost" counts gold items whose source email the rule would discard
before extraction ever saw it.

| Gate rule | Real work lost | Gated | Cost |
|---|---|---|---|
| Gmail category OR bulk headers | **88** | 483 | $0.53 |
| List-ID OR (promo AND automated) | 3 | 139 | $0.71 |
| List-ID first, transactional override | 3 | 138 | $0.71 |
| **shipped: transactional checked first** | **2** | 134 | $0.72 |
| List-ID only | 3 | 95 | $0.74 |
| no gate | 0 | 0 | $0.79 |

- **Decision:** the aggressive gate saves **$0.26** and destroys **88** real
  obligations. Not a trade worth making.
- **Ordering mattered as much as the rules.** Checking List-ID *before* the
  transactional override still dropped a plan-renewal notice and a speaker-slot
  confirmation — the override sat behind the rule that had already discarded
  them. Moving it first recovers one for $0.002.
- **Point to make:** Gmail files real speaking-slot confirmations under
  Promotions. The category label alone is not evidence.

### ⑧ Only `is_noise` gates extraction — never `is_automated`
- **Why:** transactional mail carries state. "Your card was declined" is
  automated *and* actionable.

### ⑨ SQLite in the browser, not a server
- **Why:** the corpus is read-only and small. Ship 2.6 MB gzipped, query it in
  WASM. No server, no cold start, nothing to keep alive, nothing to rot.
- **Bonus:** it makes the freeform SQL tool safe by construction.
- **Why not a server:** it would be a running cost and a liability for a demo
  someone opens once, months from now.

### ⑩ A Cloudflare Worker for exactly one reason
- **Why it exists:** an API key cannot ship in a static page. That's all.
- **What protects the bill:** per-IP 40/hour, **global 1,500/day** enforced in
  D1. The global cap is the one that actually caps spend.
- **Why D1 and not the Rate Limiting binding:** the binding is per-location and
  documented as "not an accurate accounting system" — fine for abuse, useless
  as a spend ceiling. A counter row is exact.
- **Also enforced:** model allowlist (else it's an open relay to any model on
  the account), 512 KB body cap, 40-turn cap, 2,048 output tokens.

### ⑪ Streaming, and a local proxy that mirrors the Worker
- **Why streaming:** a multi-hop agent turn is slow enough that a blank wait
  reads as broken.
- **Why a local proxy:** `tools/serve_demo.py` runs the demo with no Cloudflare
  account, no install, no deploy. Same SSE contract as the Worker.

### ⑫ `questions.py` is the single source of truth
- **Why:** the scorer and the demo agent run byte-identical SQL. Hand-copying
  queries into JavaScript is how the thing being scored quietly stops being the
  thing being demoed.

### ⑬ The guide as the landing view
- **Why:** the demo used to open into a mailbox, so a first-time visitor had to
  infer the whole model from a wall of ranked rows.
- **Arrows are hand-drawn** because they're scaffolding — annotation over an
  interface, the kind you stop seeing. They hide at widths where the furniture
  they point at has moved.

### ⑭ Citations are rendered by the interface, never parsed from model text
- **Why:** a quote on screen is always one the pipeline verified. The model
  cannot fabricate a citation, because it never writes one.

---

## 10. Bugs found and fixed — good stories to tell

### The FTS5 bug (the best one)
**Symptom:** asking *"do I have anything from Marcus?"* burned all six hops:
`search mail 0 rows`, then four failed `run_sql` guesses, then gave up.

**Root cause:** the sql.js WASM build ships **without the FTS5 module**. Every
`search_mail` call was throwing `no such module: fts5`. Python's `sqlite3`
*does* have FTS5 — so the export built and populated a table that only the
exporter could read. **The failure was invisible on the side I'd tested.**

**Fixes:**
1. Replaced the virtual table with a plain lowercased blob + index. LIKE over
   1,500 rows is milliseconds and works everywhere.
2. Added `mail_from_person` — "anything from X" is a *participant* question,
   not a text one. That gap was the real cause of the loop.
3. Zero-row results now return a hint naming the right tool.
4. Budget 6 → 12 hops.
5. Telemetry, so a throwing tool never again looks like an empty one.

**Result:** the same query now resolves in **one hop**.

**The lesson to state out loud:** the same code path behaved differently in two
SQLite builds, and my tests only exercised one of them.

### The collapsing sidebar
The rail lives in a `<details>` that collapses on narrow screens. Nothing
reopened it when the window widened — and the `<summary>` that would is
`display:none` at desktop width. Collapse once while narrow and **the entire
sidebar disappears permanently.** Now forced open on any change out of the
narrow breakpoint.

### The noise-gate numbers in the README were wrong
The original table was measured ad hoc in an earlier session and never
committed as a script. Rebuilding it as `tools/measure_noise_gate.py` produced
materially different numbers — the aggressive gate loses **88** gold items, not
6, and the costs were computed on a basis that never matched actual spend. The
README is now corrected and the measurement is reproducible.

**Lesson to state:** a number you cannot regenerate is not a measurement, it is
a memory. If it is worth putting in a README it is worth a script.

### Resume paid twice for empty emails
`extract` resumed on "emails with no evidence rows" — which includes every
email that correctly produced nothing, and most email does. One resume
re-processed 615 emails for $0.175 to add 61 claims. Fixed with an
`extraction_attempts` table keyed on extractor version; resume is now free.

### "No suspicious emails found"
See §9⑥. Found while generating a screenshot.

---

## 11. The honest numbers — say this before they ask

**Recall against the gold sets is 34.7% (59 of 170).**

| Question | Recall | | Question | Recall |
|---|---|---|---|---|
| q2 forgetting | **88.5%** | | q5 what changed | 22.2% |
| q3 waiting on me | 51.7% | | q1 needs attention | 11.8% |
| q4 waiting on others | 32.1% | | q6 slipping | 7.7% |

**The framing that is both true and strong:**

> "88% of gold items *do* reach a work item. Only ~35% surface in the top 12.
> So extraction is working — the gap is ranking and the question filters. That's
> a tuning problem, not an architecture one, and it costs nothing in API calls
> to close. It's the most obvious next thing to do, and I haven't done it."

**Do not** oversell this. Volunteering it reads as confidence; being caught
hiding it reads as the opposite.

### Also not done, and worth naming yourself
1. **The demo doesn't surface the ledger itself** — the append-only chain,
   superseded values, and the quarantine are the parts of the schema that most
   distinguish it, and none are visible. Biggest gap against your own design.
2. `run_sql` mailbox scoping is advisory, not enforced (§6).
3. Recall tuning (above).
4. 624 non-noise emails produced no claims. Most legitimately state nothing
   actionable — but that number is unaudited.

---

## 12. Questions you will be asked — with answers

**"Why not just use RAG / embeddings?"**
> Four of the six questions are temporal or negative. "What changed" needs two
> points in time; retrieval is stateless per query. "What am I forgetting" needs
> the absence of an event, and you cannot retrieve a thing that isn't there.

**"Isn't one LLM call per email expensive?"**
> $0.77 for 1,500 emails, once. Query time is then pure SQL and costs nothing.
> RAG inverts that: cheap to index, pays the model on every question, and still
> can't answer four of the six.

**"How do you know the citations are real?"**
> Every claim's quote is checked character-for-character against its source
> before insert. Two of 1,261 failed and sit in quarantine. And the interface
> renders citations from tool results — the model never writes one, so it can't
> fabricate one.

**"What stops the model hallucinating a deadline?"**
> It can only report a `due_surface_form` copied from the text. Date resolution
> happens in the reducer, deterministically, against a frozen `AS_OF`.

**"Your agent has freeform SQL — isn't that dangerous?"**
> It's SELECT-only, single-statement, 50-row capped, and it runs against a
> disposable copy in the visitor's own browser. There's no server-side database
> to attack. The honest caveat is that mailbox scoping is advisory rather than
> enforced — for real multi-tenant data I'd bind it in a view.

**"Why 35% recall? Is it broken?"**
> See §11. Lead with the 88%.

**"What would you do next, with a week?"**
> Ranking first — it's where the recall gap is and it's free. Then surface the
> ledger in the demo: the evidence chain, superseded values, the quarantine.
> Then re-extract the ~5% weakest on the stronger model.

**"How much of this is the model versus your code?"**
> The model does exactly one thing: read one email and propose claims with
> quotes. Everything else — identity, dates, ownership folding, status, ranking,
> change detection, fraud — is deterministic code. That's the design.

**"Why Gemini Flash Lite and not something stronger?"**
> Cost, at 1,367 calls. The schema is deliberately narrow — no nullable unions —
> so it survives a small model: 0 retries, 0 fallbacks across the whole run.
> `--retry-failures` escalates the misses to `gemini-3.6-flash`, which recovered
> 4 of 5 for $0.05.

---

## 13. Cheat sheet

```
Emails 1,500 · Threads 971 · People 373
Claims 1,259 (2 quarantined) · Work items 1,126 (1,062 open)
Impersonations 10 · Tests 49 · Cost $0.94 · DB 2.6 MB gz
Recall 34.7% overall — q2 88.5% high, q6 7.7% low
Model gemini-3.5-flash-lite · 12 hops · 12 tools
Worker: 40/IP/hour, 1,500/day global in D1
```

**Run it locally, agent included:**
```bash
.venv/bin/python tools/serve_demo.py     # http://localhost:8899/demo/
```

**Deep links (useful live — skip the clicking):**
```
?mailbox=founder&q=q1_needs_attention
?mailbox=founder&q=q1_needs_attention&open=cite     # citation drawer open
?mailbox=founder&q=q2_forgetting&open=how           # "how produced" open
?mailbox=founder&ask=is%20anything%20a%20scam%3F    # runs the question
```

**Three things to say if you say nothing else**
1. The LLM proposes, deterministic code disposes.
2. Citations are true by construction — the quote is verified before insert.
3. Recall is 35% and the gap is ranking, not extraction.
