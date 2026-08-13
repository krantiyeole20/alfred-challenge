# QUESTIONS.md — the semantic contract for the six questions

This document defines what each of the six evaluation questions *means*, precisely
enough that five curation agents working on five different mailboxes will produce
compatible gold sets. Read it with `src/schema.py` (the `GroundTruth` block and
`TrickyTag` enum) and `docs/generation-spec.md` §8 open. All examples below are
real messages from `data/profiles/founder.canonical.jsonl`; every other mailbox
has structurally identical cases (see `data/ground_truth.jsonl`).

**As-of moment for everything here: 2026-08-12 23:59 in the mailbox owner's own
timezone.** Details in the cross-cutting section at the end.

> **Precedence rule — the corpus outranks this document.** The worked examples
> below were written from a sample and are illustrative of the *reasoning*, not
> authoritative about any specific message's status. Curation confirmed that at
> least three founder examples do not survive a full read: Dana's go-live demand
> (`a10f18c2e4b60046`) was answered the same afternoon by `a10f18c2e4b60048`,
> Raj's bumped data-room request (`a10f18c2e4b60031`) was answered on Aug 5, and
> the Aug 14 payroll lock was approved on Aug 11. All three are therefore
> *closed*, and belong in `must_not_include`, not in `items`. Where an example
> here conflicts with what the messages actually say, follow the messages and
> note it in `curator_notes`. The definitions, boundaries and ranking principles
> below remain binding.

## Shared vocabulary

- An **obligation** is a unit of expected future action with an owner, an
  (explicit or implicit) counterparty, and possibly a due date. Obligations are
  the unit of answer; messages are evidence. One obligation may be attested by
  many messages and must be counted once.
- An obligation is **open** if no later message shows it completed, retracted,
  or transferred. Silence does not close an obligation; a passed date with no
  delivery message leaves it open *and overdue*. `delivered_attachment` closes
  the matching `promised_attachment`; `cancelled_commitment` closes what it
  retracts (and creates a "changed" fact instead).
- A **fact is current** only if no message with a later date supersedes it —
  follow `meta.superseded_by_message_id` chains and, absent annotation, take the
  latest statement by the same source on the same subject.
- The six questions are **overlapping views, not a partition.** Dana's
  `Lumenpay go-live date` (`a10f18c2e4b60046`) legitimately appears under both
  "needs my attention" and "waiting on me". What the contract forbids is
  *illegitimate* membership — the boundary rules below say which.

### Signal reliability (shared across all six)

| Signal | Reliability | Notes |
|---|---|---|
| `to:` contains only the owner, human sender, question/imperative in body | High | The strongest "this is mine" signal in the corpus |
| Owner in `cc:` | High (negative) | FYI. Almost never creates an obligation by itself |
| Owner one of many `to:` recipients, nobody named | High (negative) | `diffused_group_ask`: not the owner's unless claimed |
| `folder: SENT` + question/request in body + no later inbound reply in thread | High | The canonical "waiting on someone else" pattern |
| `in_reply_to` / thread position: who spoke last | High | Machine-verified in this corpus. Last substantive speaker ≠ the one being waited on |
| `Auto-Submitted`, `Precedence: bulk`, `List-Unsubscribe`, `X-Auto-Response-Suppress` | High | Machine mail. Never a human ask; may still *carry* a real fact (a bounce, a renewal) |
| `meta.due_date` vs dates in body | — | Curators may use `meta`; the system under test cannot. Body dates are often relative ("Friday", "before the board meeting") — resolve against the message's `date` |
| `is_important` / `is_starred` / `is_unread` | Low | Weak priors. Unread ≠ needs attention; read ≠ handled. Several planted traps are read and unstarred |
| Subject line | Low | `topic_drift` threads and `Fwd:`/`Re:` prefixes make subjects actively misleading |
| Sender domain / `Reply-To` | Trap | `lookalike_domain` (`kettlehq-billing.com`) and `reply_to_mismatch` are planted. Sender identity must survive a domain check |
| Two addresses, one voice | Trap | `same_person_two_addresses` (e.g. Dana Kowalski via `dana.kowalski@lumenpay.io` *and* `dkowalski@gmail.com`) — merge, or you split one obligation's context in half |
| Attachment `extracted_text`, `>`-quoted blocks | High but easy to skip | `task_in_attachment_only` and `quoted_text_only_task` live exactly where lazy parsers don't look |
| Sender relationship (board, flagship client, counsel, vendor, stranger) | High for *ranking* | Not encoded in a field; recoverable from `docs/personas.md` cast lists |

---

## 1. What needs my attention?

**Real intent.** "Of everything in this mailbox, what should I actually look at
*today*, in what order, so that nothing expensive happens while I'm not
looking?" It is a triage question about the near future, asked by someone who
suspects the inbox's own sort order is lying to them. A good answer is short,
ranked, and survivable: if the owner does only the top three, nothing
catastrophic occurs.

**Inclusion.** A message (or the obligation it attests) qualifies if at least
one holds as of 2026-08-12:

1. It is an open obligation of the owner's with a due date on/before ~Aug 19 or
   already passed — e.g. `a40f18c2e4b60050` *"Please DocuSign: Board Consent —
   Option Pool Increase"* (needed before Aug 26/27).
2. A consequential counterparty is escalating or blocked *now* — Dana's
   `a10f18c2e4b60046` demanding a firm go-live date "this week", with her CFO
   watching.
3. It is a hazard requiring awareness even with no task attached: the
   `kettlehq-billing.com` lookalike invoice (`a60f18c2e4b60031`, attention =
   *don't pay, warn AP*), a genuine security alert, the bounced invoice to a
   typo'd address (`a40f18c2e4b60024`).
4. A real, non-automated decision window is closing: the Datadog renewal
   (`a30f18c2e4b60038`), the SaaStr speaker-deck deadline (`a30f18c2e4b60039`)
   — genuine deadlines wearing promotional styling.

**Exclusion.** Unread is not attention (most unread here is batch-3 noise).
Newsletters with urgent-sounding deadlines ("48 hours left!") are excluded
unless the owner has an actual commitment behind them — the SaaStr message
qualifies only because Maya really is speaking. Calendar RSVPs from others,
OOO auto-replies, and delivery receipts are facts, not attention items. A
forwarded thread whose ask belongs to a third party (`a60f18c2e4b60016`, Dev:
"nothing needed from you") is out even though the quoted original screams
URGENT at "you". FYI-cc threads are out. Anything already completed or
superseded is out.

**Boundary.** This is the broadest of the "do something" buckets, but it is
time-gated where Q3 is relationship-gated: *waiting on me* (Q3) requires a
specific blocked counterparty; *needs attention* does not. The phishing invoice
needs attention though nobody legitimate is waiting; conversely a low-stakes
question from a colleague three days ago is Q3 but may not crack the Q1 list.
Against Q2/Q6: Q1 is about the *next seven days*; a forgotten commitment or a
buried task enters Q1 only once it is also urgent or hazardous.

**Ranking.** Order by *(irreversibility of missing it) → (time pressure) →
(counterparty weight)*, lexicographically: first anything that becomes
unrecoverable within ~48h (Ramp card declined `a60f18c2e4b60049` blocking the
AWS renewal payment; the Aug 14 payroll lock `a10f18c2e4b60047`); then overdue
items with an external counterparty actively waiting (Dana's go-live date, two
days past its Aug 10 ask); then upcoming hard dates ordered by date; ties broken
by who is on the other end (board/flagship client/regulator > internal > vendor
> stranger). Ignored-duration matters as a tiebreaker: Raj's bumped data-room
request (`a10f18c2e4b60031`, first asked Jul 17) outranks a fresh ask of equal
stakes.

**Traps.** `lookalike_domain` — include, but as a hazard, never as a payable.
`forwarded_task_not_users` — exclude. `diffused_group_ask` — exclude unless a
follow-up shows nobody claimed it (then it drifts toward Q6). `shifting_number`
— if included at all, only the latest figure may be cited. `duplicate_resend`
(`a60f18c2e4b60032`/`33`) — one item, urgency *raised* by the resend, never two
items. `newsletter_unsubscribe` — exclude. `deadline_moved` — use the moved
date (SOC2 evidence is due Aug 13, not Aug 20 — `a60f18c2e4b60035` supersedes
`a60f18c2e4b60034`), which makes it *more* urgent than the original message
suggests.

---

## 2. What am I forgetting?

**Real intent.** "What did *I* promise, accept, or intend, that I have since
lost track of?" The anxiety is about the owner's own reliability — dropped
commitments that damage trust. The distinguishing feature versus Q6: the owner
demonstrably *knew* about these once (they wrote the promise, replied to the
ask, set the date). The trail went cold afterwards.

**Inclusion.**

1. Owner commitments (`meta.commitment_made_by_user` populated, mostly in SENT
   mail) whose date has passed or is imminent, with no later message showing
   delivery. Example: Maya's `a50f18c2e4b60017` — July 31 migration date,
   *weekly Friday status updates*, and an August fee credit promised to Dana.
   Any Friday since without a status email is a forgetting.
2. Conditional promises whose condition has silently resolved or gone stale:
   `a60f18c2e4b60019` — countersigned MSA amendment "by Friday [Jul 3] if Beth
   signs off". It is Aug 12; either Beth answered and Maya never sent it, or
   Maya never chased Beth. Both are forgettings.
3. Accepted obligations gone quiet: Maya replied to the Head-of-Marketing slate
   ask, then nothing; the board-deck sections she agreed (`a10f18c2e4b60044`)
   to deliver by Aug 20 with no visible progress.
4. Self-directed intentions with dates: her own `a50f18c2e4b60019` "reminder"
   note (vet refill, checkup booking).
5. A promised follow-up whose *trigger already fired*: "I'll follow up once the
   AWS case resolves" (`a10f18c2e4b60043`) — if a later message shows the case
   resolved, the follow-up is now owed.

**Exclusion.** Things the owner never engaged with are not "forgetting" — they
are Q6. Others' unfulfilled promises are Q4, not this. A commitment explicitly
retracted (`cancelled_commitment`) is not forgotten, it is cancelled. A
commitment already fulfilled — Peter's June financials, promised in
`a60f18c2e4b60029` and delivered with attachment in `a60f18c2e4b60030` — is
closed (that one is Peter's anyway). Recurring vendor auto-reminders do not make
the owner's list unless the owner personally owes the action.

**Boundary.** Q2 vs Q3: Q3 is defined by the *counterparty's* blocked state and
is often only hours old; Q2 is defined by the *owner's* lapsed engagement and
is usually days-to-weeks old. Dana's go-live demand sent Monday is Q3 not Q2 —
Maya hasn't forgotten it, it arrived two days ago. The weekly-status-update
promise from Jul 14 that produced no Friday emails is Q2 (and, because Dana is
now escalating, also Q1). Q2 vs Q6, the hardest boundary in the set: apply the
**engagement test** — is there evidence (a SENT reply, a self-authored promise,
a draft) that the owner registered the obligation? Yes → forgetting. No →
slipping through the cracks. Maya's unfinished draft `a50f18c2e4b60015`
("Lumenpay - where things stand") is Q2: she started it, never sent it.

**Signals.** `commitment_made_by_user`, `commitment_is_conditional` +
`commitment_condition`, SENT/DRAFT folders, promise language in owner-authored
bodies ("I'll have it to you by…"), then *absence*: no later thread activity,
no delivered attachment, no closing reply. Absence-of-evidence scanning is the
whole method here — curators must check the full mailbox for a closing message
before marking anything open. Trap: an inbound "thanks, got it" closes silently;
a `duplicate_resend` from the counterparty is positive evidence the owner *is*
forgetting.

**Ranking.** (How overdue) × (whether the counterparty has already chased) ×
(consequence of the lapse). A promise two weeks stale that the other side has
bumped twice (the migration status updates) sits above an equally stale promise
nobody has noticed (the vet refill).

**Traps.** `conditional_promise` — belongs here once the stated date passes
unresolved; report it *as conditional*, not as a flat missed deadline.
`cancelled_commitment` — must not appear. `out_of_office` — Maya's request to
Greg (`a60f18c2e4b60027`) bounced off an OOO until Jul 6 and was never re-sent;
whether that lands here or in Q6 follows the engagement test: she wrote the
ask, so the *follow-up* she implicitly owes herself is Q2-adjacent, but the
primary reading is Q4-then-Q6 (see below).

---

## 3. What is waiting on me?

**Real intent.** "Who is blocked on me, right now?" This is the guilt list: a
specific human (or a signature workflow acting for one) cannot proceed until
the owner replies, decides, signs, or sends. It is the inbox's IOU ledger with
the owner as debtor.

**Inclusion.** All four must hold:

1. A specific, identifiable requester — a human or a system acting for one
   (DocuSign envelope `a60f18c2e4b60042`; the expense-approval workflow in
   `a40f18c2e4b60041` where Maya is the designated approver).
2. The ask is directed at the owner *individually* — sole `to:` recipient, or
   named in the body ("Maya — need your sign-off", `a60f18c2e4b60044`).
3. The last substantive move in the exchange is toward the owner: the
   counterparty spoke last and their message contains the open request. Raj's
   bump `a10f18c2e4b60031` is the archetype.
4. Still open as of Aug 12 — no later owner reply, delivery, or transfer.

**Exclusion.** This is the bucket naive systems overfill. Not waiting on the
owner: `diffused_group_ask` (`a60f18c2e4b60024` — Maya is one of six, nobody
named; `action_owner: null`); `forwarded_task_not_users` (the SLA fire belongs
to Priya); an @-mention that merely informs; threads where the owner already
answered and the ball returned to the other side; automated reminders with no
human requester behind them (a SaaS "your trial expires" is nobody waiting);
`ambiguous_pronoun_owner` (`a60f18c2e4b60043` — "he said he'd handle it" binds
Dev or Tom, not Maya; the correct gold treatment is *unresolvable ownership*,
not owner-owned). Self-tasks with no counterparty (the vet appointment) are Q2,
not Q3 — nobody is waiting.

**Boundary.** Q3 ⊂ candidate-Q1, but with a stricter gate: Q1 admits hazards
and deadlines with no requester; Q3 never does. The lookalike invoice
*demands* a response and must still be excluded — the requester is fraudulent,
so no legitimate party is waiting. Worked pair: cap-table sign-off
(`a60f18c2e4b60044`, Wendy blocked, Q3 *and* Q1) versus the suspicious-sign-in
alert (`a40f18c2e4b60026`, Q1 only — Google is not "waiting").

**Signals.** Direct-`to` with a single recipient (high), interrogatives and
imperatives addressed by name (high), who-spoke-last from `in_reply_to` chains
(high, and machine-checkable here), resends and "bumping this" language (high —
proof the requester still considers it open). Traps: `cc` position, urgency
vocabulary inside forwarded/quoted blocks, `Reply-To` domains.

**Ranking.** (How long the requester has been blocked — age of *their* last
unanswered message) × (requester weight) × (stated due date). Raj (blocked
since Jul 17, bumped once, Series B on the line) above Wendy's cap-table ask
(Aug 7, board-gated but three weeks of runway) above a same-week internal
review request.

**Traps matrix for Q3 specifically:** `duplicate_resend` = one entry, elevated;
`task_in_attachment_only` (`a60f18c2e4b60038` — the actual Ramp asks live in
the attachment's `extracted_text`) = include, the ask is real even if the body
just says "see attached"; `bcc_invisible_recipient` (`a60f18c2e4b60041`) =
include if the body names the owner, even though `to:`/`cc:` don't show her;
`implicit_deadline` = include with the resolved date (before the board meeting
→ 2026-08-27); `same_person_two_addresses` = one requester, one queue.

---

## 4. What am I waiting on someone else for?

**Real intent.** "What did I ask for, delegate, or get promised, that hasn't
come back?" The mirror of Q3, with the owner as creditor. The user wants a
chase list: who to nudge, about what, and how stale each item is.

**Inclusion.** There must be a **directed expectation**: either (a) the owner
sent an ask/delegation and no responsive reply exists — Maya's delegation
"Have Wendy schedule the EDP call with Katie" (`a20f18c2e4b60033`), her request
to Greg for runway-slide feedback (`a60f18c2e4b60027`); or (b) someone made a
promise *to* the owner that remains undelivered — Peter's "I'll send the P&L by
end of day tomorrow" (`a60f18c2e4b60029`) during its open window; Beth's
pending review in the conditional-promise pair; Tom's "redlines coming from
their legal team this week" (`a60f18c2e4b60023`). Include third-party
conditions the owner's own commitments hang on (Beth's sign-off gates Maya's
promise to Dana — one edge in each direction, Q4 and Q2).

**Exclusion.** Passive interest is not waiting. The forwarded SLA fire
(`a60f18c2e4b60016`) explicitly says "nothing needed from you" — Priya owes
Dana, not Maya; it does not enter Maya's Q4 (a monitoring-minded system may
surface it under Q1 if it deteriorates, but the gold set keeps it out of Q4).
Fulfilled promises are out the moment the delivery message exists
(`a60f18c2e4b60030` closes Peter's promise — a system still listing "waiting on
June financials" on Aug 12 fails). Retracted promises are out of Q4 and into
Q5: Tom's early-access walk-back (`a60f18c2e4b60037`) means nobody should wait
for Aug 1 access anymore. Calendar RSVPs outstanding are only Q4 if the owner
actually needs the answer to proceed.

**Boundary.** Q4 vs Q2: both live in SENT mail. Q2 = owner owes; Q4 = owner is
owed. One thread can carry both (Maya owes Dana a date; Maya awaits the cert
fix from Priya's team that the date depends on). Q4 vs Q5: a promise that
*moved* (deadline_moved) stays in Q4 with the new date and additionally
generates a Q5 delta; a promise *cancelled* leaves Q4 entirely.

**Signals.** `folder: SENT` + request language + thread silence afterwards
(high); `meta.action_owner` naming someone other than the owner *plus* the
owner as requester/promisee (high); promise language in inbound mail ("I'll
send…", "coming this week"); `out_of_office` auto-replies (high-value: the wait
just got a floor — Greg is dark until Jul 6, delegate Nora is the workaround);
bounces (the wait will *never* end — `a40f18c2e4b60024` means Dana never got
the invoice, so "waiting for Dana to pay" is a phantom wait that must convert
into an owner action). Trap: an auto-reply is not a human response — the thread
is still unanswered.

**Ranking.** (Dependency criticality — what of the owner's own deadlines this
blocks) × (staleness since the ask/promise date) × (chase count already spent).
The cert-fix validation gating the Aug 10 Lumenpay go-live outranks the EDP
call scheduling, which outranks Greg's slide feedback.

**Traps.** `promised_attachment` without its `delivered_attachment` pair =
include; with it = exclude. `conditional_promise` by others = include, labelled
with the condition. `out_of_office` = adjust expectations, don't close.
`same_person_two_addresses` = Bob/Dana/Tessa/Nadia/Josh replying from their
personal address *does* answer the thread — a system that doesn't merge
identities reports a phantom wait. `shifting_number` = you are waiting on the
thing the *latest* message says (450-seat redlines), not any earlier version.

---

## 5. What changed?

**Real intent.** "Which of my beliefs are stale?" The owner acts on a cached
model of the world — dates, numbers, commitments, meeting times. This question
asks for the diffs: each answer is a triple *(what I probably believe, what is
now true, evidence)*, not a list of new mail. New items are not changes;
changes require a prior state.

**Inclusion.**

1. Superseded figures: the Lumenpay seat count 400 → 320 → 450
   (`a60f18c2e4b60021` → `22` → `23`, chained via
   `superseded_by_message_id`). The answer states 450 as current *because* it
   replaced 320, which replaced 400.
2. Moved dates: SOC2 evidence Aug 20 → Aug 13 (`a60f18c2e4b60034` →
   `a60f18c2e4b60035`); Ellen pulling board-financials timing earlier
   (`a50f18c2e4b60032`).
3. Retracted commitments: Tom cancelling Tarrow's early API access outright
   (`a60f18c2e4b60037`) — a change from "promised Aug 1" to "not happening
   until post-audit".
4. Calendar mutations: any `CalendarEvent` with `sequence ≥ 1` (time changed)
   or `method: CANCEL` — the batch-4 update/cancel records.
5. Decision reversals inside threads: the `long_thread` requirement plants "a
   decision reached near the end that contradicts what was agreed in the
   middle" — the *final* position is the change, the mid-thread agreement is
   the stale belief.
6. Identity/administrative shifts that alter how to act: a known contact now
   writing from a second address (`same_person_two_addresses`) is a minor but
   reportable change in how threads connect.

**Exclusion.** Ordinary thread progress is not "change" — a reply adding
detail changes nothing the owner believed. Newsletters describing industry
change are out. A `duplicate_resend` changes nothing (same content, new
timestamp). The first message ever stating a figure is a fact, not a change.
Automated notifications restating known state (another storage warning) are
out; a notification revealing *new* state (invoice up 42%, `a10f18c2e4b60038`)
is arguably new information but has no prior belief in-corpus — keep it in
Q1, not Q5, unless an earlier message stated the old bill.

**Boundary.** Q5 supplies the *corrections layer* for the other five: the old
seat count is precisely the wrong answer everywhere else, and the moved SOC2
date silently re-ranks Q1. Worked example: `a60f18c2e4b60035` belongs to Q5
(the delta Aug 20→13) *and* re-scores the Q1 entry; `a60f18c2e4b60022` (320
seats) belongs *only* to Q5's history column and may never be cited as current
anywhere.

**Signals.** `meta.superseded_by_message_id` (curator-side, definitive);
correction vocabulary ("Correction on…", "Update —", "walk this back",
"actually…"); same subject/thread family with divergent figures; calendar
`sequence`/`method`; `deadline_moved` / `cancelled_commitment` /
`shifting_number` / `superseded` tags. Trap: `topic_drift` — a changed subject
under an unchanged subject line is not itself a "change", but it hides where
the latest state of topic B lives.

**Ranking.** (Recency of the change — default window: last 14 days rank first,
older changes only if the stale belief is still likely operative) ×
(magnitude/consequence of the delta). A 130-seat ARR swing outranks a meeting
moved 30 minutes.

---

## 6. What important thing might be slipping through the cracks?

**Real intent.** "What would a perfect assistant catch that I, skimming this
inbox, structurally *cannot* see?" Where Q2 covers lapses of memory, Q6 covers
lapses of *visibility*: high-consequence items whose evidence is buried,
malformed, misdirected, or orphaned. This is the question that justifies the
assistant's existence; a correct answer routinely surprises the owner.

**Inclusion.** Two conjunctive tests: **consequence** (a stakeholder, deadline,
or dollar amount that matters) and **at least one burial factor**:

- The evidence lives where no one looks: `task_in_attachment_only`
  (`a60f18c2e4b60038` — the body says "please see attached"; the $85K limit
  approval and Aug 13 deadline exist only in `extracted_text`);
  `quoted_text_only_task` (`a60f18c2e4b60040` — Maya's reply chats about
  dinner; the unaddressed Friday diligence-call ask survives only in the
  `>`-quoted block).
- Delivery silently failed: the bounce `a40f18c2e4b60024` — Maya typo'd
  `kowaslki`, the Q3 invoice never reached Dana, and everyone at Kettle
  believes it was sent. Nothing about this thread looks urgent; it is the
  purest crack in the corpus.
- The request died in an auto-responder: feedback ask → Greg's OOO
  (`a60f18c2e4b60027`/`28`), never re-sent after his Jul 6 return.
- Ownership never resolved: the diffused SOC2-reschedule ask
  (`a60f18c2e4b60024`) *if no later message shows anyone claimed it*; the
  ambiguous "he'll handle it" (`a60f18c2e4b60043`) where the Friday in
  question has long passed and neither Dev nor Tom is on record doing it.
- The thread's label lies: `topic_drift` — the "Board deck — Aug 27" thread
  that silently becomes something else by message 4; whatever topic B needs is
  filed in memory under topic A.
- Misdirected visibility: `bcc_invisible_recipient` — the owner's copy exists
  but no recipient list shows her, so reply-all loops will drop her.
- A condition resolved and nobody noticed (`conditional_promise` past its
  date), or a hazard is one plausible AP click from loss (`lookalike_domain`,
  which appears here *as* "AP might pay this" in mailboxes where the owner
  isn't the one who spotted it).

**Exclusion.** Anything loudly attested — an ask that has been bumped twice is
many things (Q1, Q3) but it is not slipping through cracks; the requester is
personally preventing that. Low-consequence buried items (a newsletter's
footnote) fail the consequence test. Items the owner has visibly engaged with
fail the burial test and belong to Q2 if dropped.

**Boundary (the Q2/Q6 line, stated once more because it is the subtlest).**
Apply the engagement test: owner-authored evidence of awareness → Q2;
none → Q6. Worked pair: the unsent Lumenpay draft (`a50f18c2e4b60015`) is Q2 —
she wrote it. The quoted-text diligence ask (`a60f18c2e4b60040`) is Q6 even
though Maya technically *replied* to the message — her reply demonstrates she
never registered the ask, which is exactly the crack. Engagement means
engaging with the obligation, not merely touching the thread.

**Signals.** This bucket is defined by *weak or absent* standard signals:
read-state true, no star, stale subjects, position deep in long threads,
attachment-only or quote-only content, `Auto-Submitted` bounces, bcc delivery.
The method is second-order: cross-reference promises against deliveries, sends
against bounces, asks against answers, conditions against resolutions — and
report the unmatched edges.

**Ranking.** (Consequence) × (invisibility — the fewer surfacing signals, the
higher, because nothing else will catch it) × (time already elapsed). The
bounced invoice ranks near the top of the founder mailbox: real money, zero
visible urgency, guaranteed to surprise.

---

## Question × TrickyTag matrix

Correct gold-set placement of each planted case ("—" = must not appear):

| TrickyTag | Q1 attention | Q2 forgetting | Q3 waiting on me | Q4 waiting on them | Q5 changed | Q6 cracks |
|---|---|---|---|---|---|---|
| `forwarded_task_not_users` | — | — | **—** (owner ≠ actor) | — (no directed expectation) | — | — |
| `same_person_two_addresses` | merge identity | merge | one queue | reply from alt address closes the wait | minor delta | split identity = crack risk |
| `conditional_promise` | if date near | ✓ once date passes unresolved | if owner owes it | ✓ the condition-holder | condition resolution is a delta | ✓ if condition resolved silently |
| `shifting_number` | latest figure only | — | — | wait on latest version | ✓ core case | — |
| `diffused_group_ask` | — | — | **—** unless later claimed by owner | — | claim message is a delta | ✓ if never claimed |
| `newsletter_unsubscribe` | — | — | — | — | — | — |
| `out_of_office` | — | follow-up owed after return | — | ✓ adjusts wait floor + delegate | — | ✓ if never re-sent |
| `long_thread` | final state only | — | if last move is at owner | if owner asked last | ✓ mid-thread reversal | buried sub-asks |
| `topic_drift` | topic B on its merits | — | topic B asks | topic B waits | — | ✓ stale-subject burial |
| `promised_attachment` (unfulfilled) | if blocking | if owner promised | — | ✓ | — | if promise forgotten by all |
| `delivered_attachment` | closes the item everywhere | closes | closes | **closes** | — | — |
| `deadline_moved` | new date re-ranks | new date | new date | new date | ✓ | — |
| `superseded` | current fact only | — | — | — | ✓ | — |
| `ambiguous_pronoun_owner` | — | — | **—** (owner unresolvable) | not a clean edge | — | ✓ |
| `implicit_deadline` | resolve the date | resolve | resolve | resolve | — | ✓ if unresolvable in-message |
| `lookalike_domain` | ✓ as hazard | — | **—** | — | — | ✓ (about to be paid) |
| `duplicate_resend` | one item, boosted | evidence of forgetting | one item, boosted | — | — | — |
| `task_in_attachment_only` | ✓ | — | ✓ | — | — | ✓ |
| `quoted_text_only_task` | ✓ | — | depends on direction | — | — | ✓ |
| `cancelled_commitment` | — | **—** | — | **—** | ✓ | — |
| `bcc_invisible_recipient` | ✓ if actionable | — | ✓ if body names owner | — | — | ✓ |
| `reply_to_mismatch` | verify before acting | — | verify requester | — | — | ✓ if fraud-adjacent |

---

## The as-of moment

All six answers are computed as of **2026-08-12 23:59 local to the owner**
(Maya/PT, Jordan & Evelyn/CT, Aditi/ET, Marcus/MT). Consequences:

- **Overdue** = due date strictly before 2026-08-12. Due *today* (the Ramp
  decline, Dana's "this week") is not overdue but carries maximal urgency.
- **Recent** defaults: Q1's horizon is roughly the next 7 days plus everything
  overdue; Q5's default lookback is 14 days, extended when the stale belief is
  still operative (the seat-count chain ended Jul 15 and still governs).
- **A promise whose date passed with no follow-up is open and overdue** —
  never assume silent completion, and never assume silent abandonment. It stays
  in Q2/Q3/Q4 as an overdue item until a message closes or retracts it.
- **A meeting already held** kills its RSVP/logistics asks; artifacts owed
  *for* it convert to overdue apologies (Q2), not future tasks. The Aug 27
  board meeting is future — everything gated on it ("before the board meeting")
  resolves to a live 2026-08-27 deadline.
- Recurring weekly commitments (Friday status updates) are evaluated per
  occurrence: every elapsed Friday without evidence is a distinct miss, but
  report them as one obligation with a miss count.
- Timezone edge: a message dated Aug 12 late evening in another zone still
  counts as received; nothing in the corpus post-dates the as-of moment.

## What makes an answer WRONG

An answer entry is wrong, regardless of eloquence, if it:

1. **Hallucinates an obligation** — no message in the mailbox attests it.
2. **Misattributes ownership** — puts a `forwarded_task_not_users` or
   `diffused_group_ask` item on the owner, or resolves
   `ambiguous_pronoun_owner` to a confident name the text cannot support.
3. **Cites a superseded fact as current** — 400 or 320 seats, the Aug 20
   evidence date, the cancelled Aug 1 early access, the original time of a
   `sequence: 1` calendar event.
4. **Treats machine mail as a human ask** — an OOO as a reply, a newsletter
   deadline as the owner's, an unsubscribe nag as a task, a
   `List-Unsubscribe-Post` sender as a waiting counterparty.
5. **Treats a fraudulent ask as legitimate** — listing the
   `kettlehq-billing.com` invoice as payable (mentioning it as a hazard is
   *required*; paying-shaped language is disqualifying).
6. **Misses buried-but-attested items** where the burial was planted:
   attachment-only, quote-only, topic-drift, bcc, bounce.
7. **Double-counts** — one obligation reported once per message of its thread,
   or a `duplicate_resend` reported twice, or one human split across two
   addresses into two counterparties.
8. **Closes what is open / opens what is closed** — reports Peter's delivered
   financials as pending, or omits the still-open migration status updates
   because the thread went quiet.
9. **Drops a required condition** — reporting a `conditional_promise` as an
   unconditional deadline.
10. **Resolves an implicit date wrongly** or refuses to resolve one the corpus
    supports ("before the board meeting" → 2026-08-27 is recoverable from the
    mailbox and must be resolved).

## Thread-level vs message-level answers

**The unit of answer is the obligation or fact, not the message and not the
thread.** Rules for curators:

- Each gold entry names one obligation/fact/delta and anchors to the **latest
  message that defines its current state** (latest deadline, latest figure,
  latest ask), citing that message's `id`. Earlier thread messages are
  supporting evidence, listed but not separately counted.
- A thread normally yields **at most one entry per question** — except when it
  genuinely carries multiple independent obligations: a `topic_drift` thread
  yields up to one per topic; a `long_thread` may yield one obligation plus
  one Q5 reversal; Maya's reply-about-dinner yields a Q6 entry anchored on the
  reply while the same thread's diligence ask history is evidence.
- Q5 entries are the exception to single-anchoring: they cite **both
  endpoints** (the superseding message as anchor, the superseded one as the
  stale belief), and for chains, the full chain.
- Cross-thread obligations (promise in one thread, delivery or bounce in
  another; `promised_attachment` pairs "may be separate threads" per spec §8)
  are still one entry — the pairing is the point. Anchor on whichever message
  proves the *current* state (the delivery, or the bounce).
- Duplicate resends and dual-address sends collapse into their underlying
  single obligation before counting.
