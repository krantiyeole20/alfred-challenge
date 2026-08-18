# The four hard questions

Answers to the sharpest critiques of this design, with the numbers to back
them. Three of the four land on real weaknesses — the answers say so.

**The posture that works for all four:** name the limit before it is pointed
out, show the number, then say what you would change and why. These are
architecture questions, not gotchas. Someone asking them is checking whether
you know where your own design breaks.

---

## Q1 — "A ledger works when you have complete context. Email has maybe 50%, if that. Do you change systems, or chase context?"

### Short answer

**Neither. The ledger is the right substrate *because* context is partial — but
my implementation is too confident, and that's the real bug.**

### The argument

A ledger is the only one of the candidate designs that can *represent* the
problem. There are three distinct states:

1. I saw a promise, and I saw it closed.
2. I saw a promise, and I never saw it closed.
3. There was never a promise.

**State 2 is the entire product.** Retrieval cannot express it — there is no
persistent state to be uncertain about, so "what am I forgetting" becomes a
generation task and the model invents a plausible answer. The ledger at least
has somewhere to *put* the uncertainty.

So partial context isn't an argument against a ledger. It's an argument against
a ledger that pretends to be complete — which is what I built.

### Where mine is wrong, concretely

`work_items.status` is `OPEN | RESOLVED | CANCELLED`. That's a closed-world
assumption: it says "not closed" when the truth is "no closure *observed*."
Those are different claims and only one of them is defensible from email alone.

**What I'd change:**

| Now | Should be |
|---|---|
| `status = OPEN` | `status = OPEN`, plus `closure_observability` |
| binary open/closed | `OPEN_UNVERIFIABLE` — the thread died in a way that suggests it moved off-channel ("let's discuss Thursday", "grabbing 15 min") |
| no completeness signal | per-item `evidence_completeness`: did I see the channel where this would close? |

The output changes with it. Not *"you forgot to send the budget model"* but
*"you promised the budget model on Jul 3. Nothing since. You also met Dana on
Jul 8, so this may already be done."* The second is always true. The first is
often wrong, and it's wrong in the way that makes someone stop trusting the
product.

### Would I chase context surface?

**Selectively, and I'd rank it by what it closes rather than by volume.**

| Source | Value | Why |
|---|---|---|
| **Calendar** | highest | Meetings are where email obligations go to die. Directly closes Q4. Cheap, structured, already adjacent. |
| Sent folder | already have it | Underused — see Q4 |
| Slack / Teams | high, expensive | Where "let's take this offline" actually lands |
| Docs / tickets | medium | Closes "I'll write it up" |

But I'd stop well short of "index everything." Each new source raises privacy
cost and integration surface, and none of them makes the world closed — you'd
still be at 70% instead of 50%, still needing to represent what you can't see.

> **The line to say:** "I'd rather have a system that's honest about the 50% it
> can't see than one that needs 100% to be correct. Calendar is the one
> integration I'd actually chase, because it's the difference between 'you
> forgot this' and 'you may have handled this in a meeting' — and that's the
> difference between a useful product and an annoying one."

---

## Q2 — "How do you handle nuance? Head of HR is cc'd on everything, but her associate handles it and she only takes escalations."

### Start with the good news — and it's verified

**Being cc'd never confers ownership in this system today.** Measured over the
current build:

```
work items the mailbox owner "owns" where they were in To:   196
work items the mailbox owner "owns" where they were CC ONLY:   0
```

Ownership comes from **what the text says**, not from who is on the header
line. The extractor's rule is explicit:

> *"Ownership is who must ACT, not who sent the mail. 'Can someone look at
> this?' sent to six people is owned by nobody until a person claims it — set
> `owner_address` to null."*

Broadcast mail is already largely rejected: of open items owned by self, **228
came from 1-recipient mail and 1 from a 7+ recipient thread.**

So the naive version of this failure — *cc'd therefore responsible* — doesn't
happen. Say that first, with the number.

### Now the part I don't handle

The real case is harder: **Marcus is named in the text, so the extractor
correctly assigns him the action — but in reality Priya always does it, and
Marcus only steps in on escalation.** Nothing in my system knows that.

### How I'd solve it — learned from behaviour, not declared

**1. A delegation graph, computed from observed behaviour.**

Nobody should have to configure this. It's directly observable:

> Marcus is named or copied on 40 benefits threads. Priya answers 38 of them.
> Marcus answers 2.

That's a `delegation_edge(marcus → priya, topic=benefits, n=40, confidence=.95)`
— derivable from the corpus with no model call, no user input, and it's fully
auditable. The UI can justify itself: *"Priya has handled 38 of the last 40 of
these."*

This is a missing table, and I'd name it as such: I have `people` and
`person_identities` but no edges between them.

**2. Escalation is the 5%, and it has signals.**

| Signal | Why it means *her*, not the associate |
|---|---|
| Direct address in body — "Marcus, can you approve" | The text names her specifically, not the role |
| The delegate hands off — "looping in Marcus" | Explicit escalation, observable |
| Thread depth | An unresolved thread on round 5 has escalated by definition |
| Sender seniority | GC or CEO writing directly is not routine |
| Threshold language | Termination, legal exposure, a number over a limit |

**3. Owner should be a distribution, not a boolean.**

Today `owner_is_self` is 0/1. It should be a ranked set of candidate owners with
the delegation graph as a prior — so the same message reads "Priya, 0.85 /
Marcus, 0.15" until an escalation signal flips it.

> **The line to say:** "Being cc'd doesn't make it yours — that's already true
> and I can show you the number. What I don't model is delegation: that Marcus
> is *named* but Priya always does it. I'd learn that from behaviour rather than
> ask anyone to configure it, because it's plainly visible in the data, and I'd
> keep it auditable so the product can say *why* it thinks Priya owns this."

---

## Q3 — "'Waiting on me' sounds good, but what about outreach you want nothing to do with? At 150-200 emails a day, how many false positives?"

**This is the question I'd concede most on. Lead with the math, not a defence.**

### The honest math

Current build, per 300-message mailbox:

| Mailbox | "waiting on me" rows |
|---|---|
| Aditi Sharma | 69 |
| Marcus Bell | 67 |
| Jordan Feld | 62 |
| Evelyn Thorne | 60 |
| Maya Rodriguez | 58 |

That's **~0.22 items per email**. Extrapolated to 175 emails/day:

> **≈ 38 new "waiting on me" items per day. Around 190 a working week.**

**That is not a usable product.** Nobody triages 190 items. And the questioner
is right about why: the system currently treats *"someone addressed a request
to me"* as equivalent to *"I owe someone a response."* Those are very different
claims, and the gap between them is almost entirely unwanted outreach.

### Three filters, in order of how much they buy

**1. The relationship gate — biggest single win, and it's already computable.**

Measured on this corpus:

```
Aditi Sharma      55 of 70 inbound senders never got a reply  →  43% of inbound
Evelyn Thorne     54 of 67                                    →  52% of inbound
Maya Rodriguez    57 of 74                                    →  49% of inbound
```

**Roughly 45% of inbound volume comes from people the user has never once
answered.** A first-contact sender with no prior correspondence is not an
obligation — ever. That's not a heuristic, it's the definition of a
relationship. This alone removes about half the surface, and at real inbox
volume the proportion of cold outreach is *higher*, not lower.

**2. Reciprocity prior — for senders you do know.**

Learn the actual per-sender response rate. Reply to Dana 90% of the time and to
a vendor 5% of the time, and those two should not produce equally weighted
obligations. This catches the harder case: someone you *once* engaged with and
have been ignoring ever since.

**3. Obligation strength, not obligation presence.**

I already store `user_in_to` vs `user_in_cc` and `recipient_count` but only use
them weakly. Direct + named-in-body + prior reciprocity should dominate; cc'd +
broadcast should be near zero.

### The metric change that matters

**My gold sets measure recall. For this question, recall is the wrong metric.**

"Waiting on me" should be a **ranked, capped list** — precision@10, not
completeness. Nobody reads item 47, so being right about it is worthless, while
being wrong about item 3 is expensive.

I don't currently compute precision@10. That's a real gap and I'd say so.

### And the feedback loop

Dismissal has to be a first-class action that feeds back: dismissed → lower the
prior for that sender, that pattern, that category. Without it the system can't
converge on one person's actual definition of "mine."

> **The line to say:** "About 38 a day, and that's not shippable — you're right.
> The root cause is that I'm treating 'addressed to me' as 'owed by me.' The
> single biggest fix is a relationship gate: on this corpus, 45% of inbound is
> from senders the user has never once replied to, and none of that should ever
> become an obligation. Then reciprocity weighting for people they do know.
> And I'd change the metric — this question should be judged on precision@10,
> not recall, and I currently don't measure that."

---

## Q4 — "How do you tell 'forgetting' from 'it was escalated, or handled in a meeting'?"

### The most important part of the answer is not an algorithm

**Change the claim the product makes.**

| Don't say | Say |
|---|---|
| "You forgot to send the model." | "You promised the model on Jul 3. Nothing recorded since." |
| asserts a fact about the world | asserts a fact about the record — always true |

The second survives being wrong. The first doesn't, and it only has to misfire
once on something the user demonstrably *did* handle before they stop believing
the whole surface. For a feature whose entire value is trust, that asymmetry
decides the design.

Note this is Q1 made concrete: the fix is representing what you didn't see.

### Then the detection work, cheapest first

**1. Cross-thread closure matching — my clearest gap.**

My reducer matches evidence to work items within a thread, plus a fuzzy title
match. So a promise made in thread A and delivered in thread B stays open
forever. Real work moves between threads constantly — subject changes, someone
starts a fresh mail. Matching closure claims across the whole mailbox by
`(owner, normalised title)` rather than by thread is a contained change to
`reduce.py` and it's the highest-value fix here.

**2. Someone else delivered it.**

If the thread continues without me and another participant does the thing, that
is an *observable* resolution — I have the messages. That covers a good part of
the escalation case: it didn't get forgotten, it got absorbed.

**3. Calendar co-occurrence — where the meeting case gets handled.**

A meeting with the counterparty, between the promise and now, with a matching
subject, sharply raises the odds it was dealt with verbally. Not enough to mark
it resolved — it *is* enough to downweight it and to say why.

**4. The commitment was superseded, not dropped.**

Already handled: `superseded_by` chains on evidence, and a later
`decision.value` supersedes an earlier one. Worth mentioning because it's built,
it's tested, and it's the one part of this that already works.

### What stays unsolvable, and say so

Something agreed verbally, in a corridor, by two people who never wrote it down,
is **not recoverable from email**. No amount of engineering closes that. The
right response is a system that ranks by *confidence that it's still open* and
makes it one click to say "handled" — that click being the cheapest way to
acquire the signal that no integration can give you.

> **The line to say:** "I'd rather say 'no closure recorded' than 'you forgot
> this' — the first is always true. Then three things narrow the gap: matching
> closures across threads instead of within them, which is my clearest gap
> today; detecting that someone else delivered it; and calendar, to catch the
> meeting case. What was agreed verbally and never written down is genuinely
> unrecoverable, so the honest design ranks by confidence and makes 'handled'
> one click."

---

## The through-line

All four questions are the same question: **what happens when the record is
incomplete?**

The answer that holds up across all of them:

> The ledger is right *because* the record is incomplete — it's the only design
> that can distinguish "closed" from "no closure seen." What I'd change is
> confidence, not architecture: represent what wasn't observed, rank instead of
> enumerate, learn from behaviour rather than ask for configuration, and make
> claims about the record rather than about the world.

**Don't be defensive on Q3.** The 38-a-day number is bad, the questioner knows
it's bad, and volunteering it with the relationship-gate fix is far stronger
than explaining why it's fine. It isn't fine.
