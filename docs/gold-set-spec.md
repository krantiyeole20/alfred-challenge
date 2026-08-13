# Gold set — output contract and scoring model

`docs/QUESTIONS.md` says what the six questions *mean*. This file says what the
answer file must *look like*, and how it gets scored.

Six questions × five mailboxes = **30 answer sets**. Each curation agent produces
one mailbox's six sets as `data/gold/<profile_id>.gold.json`; `src/build_gold.py`
merges the five into `data/gold_set.json`.

**As-of moment: 2026-08-12 23:59 in the mailbox owner's local timezone.** Every
message in the corpus is in the past. Nothing is "upcoming" in the sense of
unseen — but obligations, deadlines and meetings scheduled *after* that instant
are still in the future and are live.

---

## 1. The two layers, and why both exist

This is the part to understand before writing anything.

### Layer 1 — `ranked` (the scoring surface)

An ordered list of `item_id`s. This is the **only** thing used to score a
pipeline run. It answers "did the system surface the right things, in roughly the
right order?" and produces a single comparable number per (mailbox, question).

Keep it honest and tight: `ranked` contains **exactly the `must_include` and
`acceptable` items**, in priority order per the question's ranking principle.
`borderline` items are deliberately excluded from `ranked` — a system should
neither be rewarded nor punished for them.

### Layer 2 — everything else (the diagnostic surface)

Tiers, `must_not_include`, evidence quotes, due-date basis, consequence,
counterparty, trap annotations. **None of this is scored.** It exists for the
moment a score comes back low and the question becomes "*why*, and what do I
change?" The layers answer different diagnostic questions:

| If the score is low… | Look at | What it tells you |
|---|---|---|
| System missed items entirely | `tier: must_include` misses | Retrieval failure — the item never surfaced |
| System found them but ranked badly | `rank`, `consequence`, `due_date` | Prioritisation failure — signals are there, weighting is wrong |
| System returned confident junk | `must_not_include` hits | Precision failure, and the `trap` field names the exact confusion |
| System got the right thread, wrong claim | `evidence_quote`, `anchor_message_id` | Comprehension failure — it read the thread but misread the state |
| Right item, stale content | `superseded_by`, Q5 items | Recency failure — it cited a fact that a later message overwrote |

So: **compare on `ranked`, debug on the rest.** A gold set that only had `ranked`
would tell you that you scored 0.4 and nothing about what to fix.

### Suggested metrics (not prescriptive)

- **Recall@k** over `ranked` — the headline number.
- **Precision** counting any returned item not in `ranked ∪ borderline` as a miss.
- **False-positive rate** = fraction of `must_not_include` entries the system
  returned. This is the sharpest signal in the whole set: every entry there is a
  trap a naive system falls into, so a high rate localises the failure instantly.
- **Rank correlation** (Spearman) between the system's order and `ranked`.
- Weight `must_include` misses heavier than `acceptable` misses.

---

## 2. File shape

`data/gold/<profile_id>.gold.json`:

```json
{
  "profile_id": "founder",
  "owner": {
    "name": "Maya Rodriguez",
    "email": "maya@kettlehq.com",
    "role": "Founder & CEO, Kettle",
    "timezone": "America/Los_Angeles"
  },
  "as_of": "2026-08-12T23:59:00-07:00",
  "answers": {
    "q1_needs_attention": { ...AnswerSet... },
    "q2_forgetting": { ...AnswerSet... },
    "q3_waiting_on_me": { ...AnswerSet... },
    "q4_waiting_on_others": { ...AnswerSet... },
    "q5_what_changed": { ...AnswerSet... },
    "q6_slipping_through_cracks": { ...AnswerSet... }
  }
}
```

The six keys are fixed and must all be present.

### AnswerSet

```json
{
  "question": "What needs my attention?",
  "prose_answer": "Three things are genuinely urgent. Dana Kowalski at Lumenpay ...",
  "ranked": ["founder-q1-01", "founder-q1-02", "founder-q1-03"],
  "items": [ ...Item... ],
  "must_not_include": [ ...Distractor... ],
  "curator_notes": "Judgement calls made while building this set."
}
```

- `prose_answer` — 80–200 words. What a perfect assistant would actually say to
  the owner, in the owner's terms. Name people and amounts. No meta-commentary
  about the corpus. This is what a human grader reads to decide whether the
  answer is *useful*, separate from whether the ids match.
- `ranked` — ordered `item_id`s, `must_include` + `acceptable` only.
- `items` — every qualifying item including `borderline`.
- `must_not_include` — near-misses a plausible system would wrongly return.
  **Aim for at least 4 per question**, drawn from real messages in that mailbox.

### Item

```json
{
  "item_id": "founder-q1-01",
  "rank": 1,
  "tier": "must_include",
  "claim": "Dana Kowalski (Lumenpay) is blocked on a firm SSO go-live date; asked Aug 10, still unanswered.",
  "thread_id": "a10f18c2e4b60044",
  "anchor_message_id": "a10f18c2e4b60046",
  "message_ids": ["a10f18c2e4b60044", "a10f18c2e4b60046"],
  "evidence_quote": "I need a date I can put in front of my CFO by Wednesday.",
  "why_it_qualifies": "Open obligation of the owner's, external flagship customer escalating, no reply sent.",
  "counterparty": "dana.kowalski@lumenpay.io",
  "due_date": "2026-08-12",
  "due_basis": "explicit",
  "days_open": 2,
  "consequence": "high",
  "tricky_tags": ["same_person_two_addresses"]
}
```

Field rules:

- `item_id` — `<profile_id>-<qN>-<NN>`, e.g. `finance-q4-03`. Unique corpus-wide.
- `tier` — `must_include` (any competent system must find this; omission is a real
  failure) · `acceptable` (genuinely qualifies, lower stakes) · `borderline`
  (defensible either way — say why in `why_it_qualifies`).
- `claim` — one sentence, self-contained, states the *current* state. A reader
  who never opens the mailbox should understand the obligation from this alone.
- `anchor_message_id` — the single message that best establishes the item's
  current state. For Q5 this is the message carrying the *new* value.
- `message_ids` — every message that evidences the item, anchor included. This is
  how one obligation spread across a nine-message thread stays **one** item.
- `evidence_quote` — verbatim from `body_text` or an attachment's
  `extracted_text`, ≤200 chars. Must be findable by exact substring search.
- `due_date` — ISO date or `null`. `due_basis`: `explicit` (a date is stated),
  `implicit` (derivable — "before the board meeting", "end of next week"),
  `none`.
- `days_open` — whole days from the obligation's origin to the as-of moment.
- `consequence` — `high` / `medium` / `low`: cost if this is missed entirely.
- `tricky_tags` — any `TrickyTag` values on the cited messages, `[]` if none.

Q5 items carry four extra fields and may omit `counterparty` / `due_date`:

```json
"changed_field": "Q4 paid social budget",
"previous_value": "$150K",
"current_value": "$120K",
"from_message_id": "b60f18c2e4b60012",
"to_message_id": "b60f18c2e4b60013"
```

`to_message_id` is mandatory — it is the message carrying the new value.
`from_message_id` may be `null` when the old value is asserted *inside* the same
message ("your role was changed from Admin to Account Owner"), i.e. when no
earlier message in the mailbox independently establishes the previous state.

### Distractor

```json
{
  "message_id": "a60f18c2e4b60016",
  "subject": "Fwd: URGENT — webhook retries breaking Lumenpay SLA",
  "why_excluded": "Dev forwarded this FYI; the ask is Priya's. Body says 'nothing needed from you'.",
  "trap": "forwarded_task_not_users"
}
```

`trap` is a `TrickyTag` value where one applies, otherwise a short kebab-case
label of your own (`unread-but-noise`, `newsletter-deadline`,
`already-completed`, `automated-not-human`, `cc-not-addressed`).

---

## 3. Rules that produce a usable set

1. **One obligation, one item.** A nine-message thread about one thing is one
   item with nine `message_ids`. Never one item per message.
2. **Cite the current state.** Where a `shifting_number` or `deadline_moved`
   chain exists, the item states the *latest* value. The earlier values are Q5
   material and `must_not_include` material elsewhere.
3. **Merge dual-address humans.** `same_person_two_addresses` is one
   counterparty, one obligation.
4. **Collapse duplicate resends** into one item; note that the resend raises
   urgency.
5. **Ownership is checkable.** Before putting something in Q2/Q3, verify from the
   message that the owner is the one on the hook. `forwarded_task_not_users` and
   `diffused_group_ask` are the planted failures here — both belong in
   `must_not_include`, not in `items`.
6. **Machine mail is not a human ask.** `Auto-Submitted`, `Precedence: bulk` and
   `List-Unsubscribe` senders never create Q3 items — though a bounce or a
   renewal notice can carry a real Q1 fact.
7. **Ground every claim.** If you cannot produce a verbatim `evidence_quote`, the
   item does not go in. No inference beyond what the text supports.
8. **Sizing.** Expect roughly 5–12 items per question, and at least 4
   distractors. If a question yields 25 items, the inclusion criteria are being
   read too loosely; re-read the relevant section of `QUESTIONS.md`.

---

## 4. Validation

Every one of the 30 sets is then handed to an independent adversarial validator
whose job is to **break it**: find qualifying items that were missed, items that
don't survive reading the full thread, wrong owners, superseded values cited as
current, evidence quotes that aren't in the source, distractors that actually
belong in `items`. Surviving findings are applied before the set ships.
