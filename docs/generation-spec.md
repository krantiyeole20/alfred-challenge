# Mock Gmail corpus — generation spec

Read this file **and** `docs/personas.md` **and** `src/schema.py` before writing
anything. Your job is to author one JSON file of **exactly 50 email records** for
one (profile, batch) pair.

Corpus totals: 5 profiles × 6 batches × 50 = **1,500 emails**.

---

## 1. Output contract

Write a single file to `data/raw/<profile_id>_b<N>.json` containing a **JSON array
of 50 objects**. Nothing else — no prose, no markdown fence, no trailing commas.
Your final chat message must be a short report only (counts, thread list,
anything you couldn't satisfy), *not* the data.

Every object must validate against `Email` in `src/schema.py`. `extra="forbid"` is
on: an unknown key is a hard failure. Note the JSON key is **`"from"`**, not
`"from_"`.

Minimal well-formed record:

```json
{
  "id": "a10f18c2e4b60001",
  "thread_id": "a10f18c2e4b60001",
  "profile_id": "founder",
  "history_id": "1247",
  "message_id_header": "<CAFa10f18c2e4b60001@mail.gmail.com>",
  "in_reply_to": null,
  "references": [],
  "thread_position": 1,
  "thread_length": 1,
  "from": {"name": "Dev Patel", "email": "dev@kettlehq.com"},
  "to": [{"name": "Maya Rodriguez", "email": "maya@kettlehq.com"}],
  "cc": [],
  "bcc": [],
  "delivered_to": "maya@kettlehq.com",
  "subject": "Staging is back up",
  "snippet": "Rolled back the migration, staging is green again. Root cause was the...",
  "body_text": "Rolled back the migration, staging is green again.\n\nRoot cause was the index rebuild locking writes for ~20 min. I'll write it up properly tomorrow.\n\nDev",
  "body_html": null,
  "date": "2026-08-10T18:42:11-07:00",
  "internal_date_ms": 1786239731000,
  "folder": "INBOX",
  "category": "CATEGORY_PERSONAL",
  "is_unread": false,
  "is_starred": false,
  "is_important": true,
  "user_labels": [],
  "attachments": [],
  "calendar_event": null,
  "extra_headers": {"Return-Path": "<dev@kettlehq.com>"},
  "size_estimate": 3184,
  "meta": {
    "email_class": "genuine_work",
    "tricky_tags": [],
    "is_actionable_for_user": false,
    "action_owner": "dev@kettlehq.com",
    "action_summary": "Dev will write up the incident postmortem.",
    "due_date": null,
    "commitment_made_by_user": null,
    "commitment_is_conditional": false,
    "commitment_condition": null,
    "superseded_by_message_id": null,
    "notes": null
  }
}
```

---

## 2. Identifiers — how to avoid collisions

Your prompt gives you a **12-character ID prefix**. Every `id` you emit is that
prefix plus a **4-digit zero-padded decimal counter** — literally the strings
`"0001"`, `"0002"`, … `"0050"`, in the order you write them. Do **not** encode the
counter as hex (that would stop at `0032`); the digits `0`–`9` are already valid
hex characters, so decimal counting keeps the id a legal 16-char hex string.

```
id = "<your prefix>" + "0001".."0050"     # always 16 lowercase hex chars
```

- `thread_id` = the `id` of the **first** message of that thread. A single-message
  email has `thread_id == id`.
- `message_id_header`:
  - sender on a Google-hosted domain (all five company domains, plus gmail.com) →
    `<CAF{id}@mail.gmail.com>`
  - anyone else → `<{id}@{sender's domain}>` e.g. `<a10f18c2e4b60007@klaviyo.com>`
- `history_id` = `str(internal_date_ms // 60000 - 29000000)`. Roughly monotonic
  with time, which is what matters.
- attachment `attachment_id` = `"ANGjdJ_"` + ~50 random base64url characters,
  unique within its message.
- calendar `uid` = `"{24 lowercase hex}@google.com"`.

---

## 3. Threading — the rule that must not break

Enforced by the validator, so get it right:

```
position 1 : in_reply_to = null,          references = []
position 2 : in_reply_to = <MID of #1>,   references = [<MID#1>]
position 3 : in_reply_to = <MID of #2>,   references = [<MID#1>, <MID#2>]
position 4 : in_reply_to = <MID of #3>,   references = [<MID#1>, <MID#2>, <MID#3>]
```

`references` is root→parent in order, and its **last element is always
`in_reply_to`**. All messages in a thread share `thread_id` and the same
`thread_length`. `thread_position` is 1-based and strictly increasing with `date`.

Threads never span batches — everything you reference must be in your own file.

**Forwards start a new thread**: new `thread_id`, `thread_position` 1,
`in_reply_to` null, subject prefixed `Fwd:`, body contains a
`---------- Forwarded message ---------` block with the original From/Date/Subject/To.

Replies use `Re: ` once — not `Re: Re: Re:`.

---

## 4. Time

Window: **2026-06-13 → 2026-08-12** (today is Wed 2026-08-12).

- **≥ 75% of your 50 records** must fall in **2026-07-13 → 2026-08-12**.
- The remainder spread over 2026-06-13 → 2026-07-12.
- `date` is ISO-8601 **with the sender's UTC offset** (e.g. `-07:00` for SF in
  August, `-05:00` for Chicago/Austin, `-04:00` for Boston/NY, `-06:00` for Denver).
- `internal_date_ms` = epoch ms of that same instant, within ±120 s.
- Human senders write during business hours in **their own** timezone, weekdays
  mostly. A handful of late-night and weekend sends is realistic; automated
  senders can land any hour.
- Replies must be later than their parent — usually minutes to hours, occasionally
  days. **Batches 2 and 6 only:** include one thread with a multi-week gap before a
  "bumping this" reply. Other batches should not bother.

---

## 5. Labels, folders, realism rules

- `folder`: `INBOX` for received, `SENT` for anything the profile owner sent,
  `DRAFT` for unsent, `SPAM` for junk Gmail caught, `ARCHIVE` for read-and-filed.
- Exactly one `category` on `INBOX` mail; **`null`** on `SENT`/`DRAFT`.
  `CATEGORY_PERSONAL` = normal human mail, `CATEGORY_UPDATES` = receipts,
  notifications, calendar, `CATEGORY_PROMOTIONS` = marketing, `CATEGORY_SOCIAL` =
  LinkedIn/social, `CATEGORY_FORUMS` = groups/mailing lists.
- `is_unread` true on roughly 15–25 % of INBOX mail, concentrated in the last
  5 days. Never on `SENT`/`DRAFT`.
- `is_important` on genuinely consequential mail only (~20 %). `is_starred` rarer (~6 %).
- `user_labels`: use **only** ids from the shared registry below, so that all six
  batches of a profile agree. Apply them to roughly 15 % of records, and never to
  `SPAM`. Leave the list empty otherwise.

  | profile | registry |
  |---|---|
  | `founder` | `Label_8842` Board/Investors · `Label_3391` Customers · `Label_5567` Hiring · `Label_1120` Legal+Compliance |
  | `marketing` | `Label_2201` Q4 Planning · `Label_2202` Agency · `Label_2203` Campaigns · `Label_2204` Budget |
  | `finance` | `Label_4410` Close · `Label_4411` Audit · `Label_4412` Vendors · `Label_4413` Board Reporting |
  | `hr` | `Label_6630` Recruiting · `Label_6631` Employee Relations · `Label_6632` Benefits · `Label_6633` Comp Cycle |
  | `consulting` | `Label_7750` Clients · `Label_7751` Business Development · `Label_7752` Billing · `Label_7753` Firm Admin |
- `size_estimate`: ~2,000–15,000 for text mail; add ~1.34× each attachment's bytes.

## 6. Writing the actual emails

This is the part that determines whether the corpus is useful.

- **Vary length hard.** Target shape, aspirational rather than a hard quota — but
  do not let everything collapse into the comfortable 100–350 word middle: ~25 %
  one or two lines ("works for me", "see attached", "+1", "call you in 5"), ~45 %
  3–8 sentences, ~20 % substantial (150–400 words), ~10 % long-form (400 words+: a
  board update, an audit PBC list, a policy announcement, an incident writeup).
  Real inboxes are full of terse pings; include them even when the batch theme is
  "real work". Each file should contain at least **8** messages under 25 words and
  at least **3** over 400 words.
- **Real signatures**, quoted-reply blocks (`On Tue, Aug 4, 2026 at 9:14 AM Dev
  Patel <dev@kettlehq.com> wrote:` followed by `>`-quoted text) on some replies but
  not all, `Sent from my iPhone` on a few, typos and lowercase-only sends
  occasionally.
- **Concrete specifics**: real-sounding dollar amounts, dates, ticket numbers,
  vendor names, headcounts, percentages, file names. Avoid `[Client Name]`
  placeholder text — this data should read like a leak, not a template.
- `body_html`: supply it for marketing/automated/newsletter mail (~all of batch 3
  and most of batch 4) and for maybe 20 % of human mail; `null` otherwise. Keep
  HTML modest — a few hundred to a couple thousand characters, inline styles.
- `snippet` = first ~180 characters of `body_text`, whitespace collapsed to single
  spaces, truncated mid-word if needed.
- Never reuse a subject line within your file unless it is the same thread.

**Attachments** carry `extracted_text` — the text a parser would recover. A CSV
attachment gets actual comma-separated rows; a PDF gets its prose; an XLSX gets a
readable dump; an ICS gets the calendar body. 300–1,200 characters is right. Make
the content *consistent with the email body* — if the mail says revenue was
$412K, the spreadsheet must say $412K.

**Ground truth `meta` is mandatory and must be honest.** `is_actionable_for_user`
is true only when the profile owner personally owes something. `action_owner` is
the address of whoever actually owes it — often *not* the owner.

---

## 7. Batch assignments

Your prompt names one. Compose exactly 50 records for it.

### B1 — Core operational work (short threads)
Day-to-day genuine work: 1–3 message threads, direct asks, quick answers,
approvals, status pings, scheduling chatter, client/customer questions, vendor
coordination. Aim for ~22 threads. Mostly `genuine_work`, `client_customer`,
`vendor_invoice`, `legal_compliance`, `recruiting` as fits the profile. Include
3–5 with attachments. Include 2–3 purely personal ones to the owner's work
address (school reminder, doctor, friend).

### B2 — Long and multi-party threads
~7 threads totalling 50 messages: one of 10, one of 8, one of 7, one of 6, two of
5, and the rest. 3+ distinct participants on most; people join mid-thread (added
to Cc with "adding Priya for visibility"), someone drops off, someone replies
top-post while another bottom-posts. The owner sends some messages
(`folder: "SENT"`) inside these threads. Include one thread that goes quiet for
2+ weeks then gets bumped, and one where two sub-conversations run in parallel
and get confusing. Do **not** duplicate batch 6's required topic-drift thread —
use different subject matter.

### B3 — Noise: newsletters, promotions, cold outreach, spam
Industry newsletters (with `List-Unsubscribe` + `List-Unsubscribe-Post`,
`List-ID`, `Precedence: bulk`, `Feedback-ID`), SaaS product-marketing blasts,
conference and webinar invites, vendor cold outreach that name-drops the company,
recruiter spam, LinkedIn/social notifications, 4–6 in `folder: "SPAM"` including
one crude phishing attempt (`email_class: "phishing"`) and one lottery/crypto
scam. Roughly 60 % `CATEGORY_PROMOTIONS`, plus `CATEGORY_SOCIAL` and
`CATEGORY_UPDATES`. Most are `is_actionable_for_user: false` — but include 2 that
genuinely do matter (a real renewal notice buried in marketing styling, a
conference the owner actually agreed to speak at).

### B4 — Automated, calendar, and system mail
- **12–16 calendar records**: new invites (`method: "REQUEST"`), an update to an
  existing event (same `uid`, `sequence: 1`, time changed), a cancellation
  (`method: "CANCEL"`, `status: "CANCELLED"`), 3–4 RSVPs (`method: "REPLY"`, e.g.
  "Priya Nair has accepted this invitation"), one recurring meeting with `rrule`,
  one all-day event. Attach `invite.ics` with a matching `extracted_text`.
  `email_class: "calendar"` whenever `calendar_event` is set.
- **SaaS/system notifications**: deploy alerts, error budgets, usage warnings,
  invoices and receipts, password/2FA notices, storage limits, doc-share
  notifications, e-signature requests, ticket updates.
- **2–3 security alerts** (new sign-in, suspicious login, admin permission change).
- **2–3 bounces / delivery failures** (`email_class: "bounce"`,
  `from: "mailer-daemon@googlemail.com"`, `X-Failed-Recipients` header,
  `Auto-Submitted: auto-replied`), one of which is a real problem (an invoice that
  never reached the client).
- **2–3 out-of-office auto-replies from other people** (distinct from batch 6's
  required one; those are replies to the owner's own sends, so include the owner's
  originating `SENT` message at position 1).

### B5 — The owner's outbox
At least 40 of 50 are `folder: "SENT"` (`from` = the profile owner). Cover: replies
that close loops, delegation ("Ana — can you own the Halberd deck?"), forwards
with a one-line instruction, follow-ups on unanswered mail, an apology for a
missed deadline, a long strategic email to the team, an intro connecting two
people, a negotiation reply, a rejection. Include **3–4 `folder: "DRAFT"`**
records (unsent, often unfinished mid-sentence, sometimes with an empty `to`).
Where the owner replies, include the parent inbound message too so the thread is
coherent. Owner commitments here should populate `meta.commitment_made_by_user`.

### B6 — Deliberately tricky cases
See section 8. This batch is where the adversarial material lives.

---

## 8. Batch 6 — the required tricky cases

All eleven must appear **in every profile**, instantiated in that profile's own
world. Tag each with the matching `TrickyTag` and explain it in `meta.notes`.

1. **Forwarded email whose task belongs to someone else** — `forwarded_task_not_users`.
   A colleague forwards a client request with "FYI" or "keeping you in the loop".
   The ask inside is directed at a third party. `is_actionable_for_user: false`,
   `action_owner` = that third party. Make the body *sound* urgent and addressed
   to "you" inside the quoted original.
2. **Two emails from the same person via two different addresses** —
   `same_person_two_addresses` on both. Use the dual-address person named in
   `docs/personas.md` for your profile. Different threads, days apart, same voice
   and signature, related subject matter, and the second one references something
   only the first one said. Note this in `meta.notes` on both.
3. **Conditional promise** — `conditional_promise`. Someone (or the owner) writes
   "if legal signs off I'll have it to you Friday". Set
   `commitment_is_conditional: true` and fill `commitment_condition`. Add a second
   record elsewhere in the batch where the condition is *not yet resolved*.
4. **A number that keeps changing across three emails** — `shifting_number` on all
   three, chronological, days apart, same subject/thread family. Each states a
   different figure with a plausible reason for the change. The first two set
   `meta.superseded_by_message_id` to the *next* message's `message_id_header`.
   Use a profile-appropriate figure (paid-social budget, headcount, ARR
   commitment, requisition count, project fee).
5. **"Can someone handle this?" to a group of six** — `diffused_group_ask`. The
   owner is one of six `to` recipients. Nobody is named. `action_owner: null`,
   `is_actionable_for_user: false`. Optionally add a follow-up where one other
   person claims it.
6. **Newsletter with an unsubscribe link** — `newsletter_unsubscribe`. Real
   `List-Unsubscribe: <mailto:...>, <https://...>` plus
   `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, `List-ID`,
   `Precedence: bulk`, and an actual unsubscribe URL in both `body_text` and
   `body_html`.
7. **Out-of-office auto-reply** — `out_of_office`. Two records: the owner's
   outbound `SENT` message at position 1, then the auto-reply at position 2 with
   `Auto-Submitted: auto-replied`, `X-Auto-Response-Suppress: All`,
   `Precedence: bulk`, and a return date + delegate contact in the body.
8. **A thread of 8+ messages** — `long_thread` on every message. Make it 9 or 10,
   genuinely back-and-forth, at least 3 participants, the owner sending 3–4 of
   them, with a decision reached near the end that contradicts what was agreed in
   the middle.
9. **A thread that switches topic halfway** — `topic_drift` on all messages, and
   set the tag from the drift point onward in `meta.notes`. 6–8 messages. Starts
   on topic A, and by message 4 has silently become topic B — **subject line never
   changes**. That is the whole point.
10. **Promise-then-delivery attachment pair** — `promised_attachment` on the first
    (explicitly says the file is coming, has **no** attachment), then
    `delivered_attachment` 2–5 days later with the actual attachment plus
    `extracted_text`. The second should reference the first ("finally sending
    this over"). They may be separate threads.
11. **Correct reply chains throughout** — enforced by the validator on every
    record you write.

Fill your remaining ~20 records with further hard cases, at least one each of:
`lookalike_domain` (e.g. `kettlehq-billing.com` or `rn` for `m` in a vendor
domain, invoice fraud), `duplicate_resend` (same content resent two days later
with "resending — did this reach you?"), `deadline_moved` (a due date changes
across two messages), `cancelled_commitment` (a promise explicitly retracted),
`task_in_attachment_only` (body says "details in the attached", the real ask is
only in `extracted_text`), `quoted_text_only_task` (the ask survives only in the
quoted portion of a reply that otherwise discusses something else),
`bcc_invisible_recipient` (owner is on `bcc`, so `to`/`cc` don't show them),
`reply_to_mismatch` (`Reply-To` points at a different domain than `From`),
`ambiguous_pronoun_owner` ("he said he'd handle it" with two possible antecedents),
`implicit_deadline` ("before the board meeting" with the date never stated).

---

## 9. Self-check before you finish

Run this — it validates your file against the real schema:

```bash
cd /Users/krantiy/alfred-challenge && .venv/bin/python src/schema.py data/raw/<your_file>.json
```

Fix every error until it prints `OK: 50 records valid`. Then confirm by eye:

- exactly 50 records, all `id`s start with your prefix, counter `0001`–`0050`
- ≥ 38 records dated on/after 2026-07-13
- every reply's `references` ends with its `in_reply_to`
- no `SENT`/`DRAFT` record has a `category` or `is_unread: true`
- every `INBOX` record has exactly one `category`
- subjects are not repeated across different threads
