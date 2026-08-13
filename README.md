# alfred-challenge — mock Gmail corpus

A synthetic but structurally faithful Gmail dataset for five people with very
different jobs. Built to stress-test anything that reads a mailbox and tries to
work out what the owner actually has to do.

**1,500 messages · 5 mailboxes · 300 each · window 2026-06-13 → 2026-08-12**
(≈75 % of each mailbox falls in the last 30 days, mirroring how real inbox
attention is distributed.)

## The five mailboxes

| `profile_id` | who | org |
|---|---|---|
| `founder` | Maya Rodriguez, founder & CEO | Kettle — 28-person Series A dev-tooling startup |
| `marketing` | Jordan Feld, VP Marketing | Habitat Goods — 180-person DTC brand |
| `finance` | Aditi Sharma, VP Finance | Northwind Robotics — 50 people |
| `hr` | Marcus Bell, Head of People | Vireo Health Systems — 200 people |
| `consulting` | Evelyn Thorne, President | Thorne & Cadwell Advisory — 10-person consultancy |

Full cast lists, domains and running storylines: [docs/personas.md](docs/personas.md).

## Layout

```
data/
  raw/                       30 generator batches, <profile>_b<1..6>.json
  profiles/
    <profile>.canonical.jsonl    one Email record per line
    <profile>.gmail.jsonl        users.messages.get shape, one per line
    <profile>.threads.json       users.threads.get shape
  ground_truth.jsonl         every message carrying a deliberate tricky tag
docs/
  research/gmail-email-data-model.md   how Gmail actually models a message
  personas.md                          the five owners and their casts
  generation-spec.md                   the spec the generator agents followed
src/
  schema.py                  Pydantic models + Gmail API projection
  build.py                   validate 30 batches, emit the corpus
  load.py                    loader helpers
results/
  corpus_report.md           coverage tables
  stats.json                 the same numbers, machine-readable
```

## Two representations of every message

Each message is authored once and serialised twice.

**Canonical** — flat, typed, easy to consume:

```python
from load import load_profile
mailbox = load_profile("founder")
e = mailbox[0]
e.from_.email, e.subject, e.body_text, e.attachments[0].extracted_text
e.meta.is_actionable_for_user, e.meta.action_owner
```

**Gmail-API-faithful** — what `users.messages.get` really returns, with a nested
MIME `payload`, a `headers` list, `labelIds`, `internalDate` as a string of epoch
milliseconds, `historyId` and `sizeEstimate`:

```python
from load import load_gmail
msg = next(load_gmail("founder"))
msg["payload"]["mimeType"]                      # multipart/mixed
[h["name"] for h in msg["payload"]["headers"]]  # Delivered-To, Date, Message-ID, ...
```

Attachments are metadata plus `extracted_text` — the text a parser would recover
from the file, kept consistent with the email body.

## What the corpus deliberately contains

Beyond ordinary work mail, every mailbox carries noise (newsletters with real
RFC 8058 unsubscribe headers, promotional blasts, cold outreach, spam and one
phishing attempt), automated traffic (calendar `REQUEST`/`REPLY`/`CANCEL` with
ICS bodies, SaaS notifications, security alerts, bounces), the owner's own sent
mail and drafts, and long multi-party threads.

It also contains eleven **planted adversarial cases in every mailbox**, each
tagged in `meta.tricky_tags` and indexed in `data/ground_truth.jsonl`:

1. a forwarded email whose task belongs to a third party, not the owner
2. two emails from the same human via two different addresses
3. a conditional promise ("if legal approves, Friday")
4. a number that changes across three emails, with `superseded_by_message_id` chained forward
5. "can someone handle this?" sent to six people including the owner
6. a newsletter with a working unsubscribe link and `List-Unsubscribe-Post`
7. an out-of-office auto-reply, with the owner's originating message
8. a thread of 8+ genuine back-and-forth messages
9. a thread that silently switches topic halfway, subject line unchanged
10. a promise to send a file, and the later email that actually carries it
11. correct `In-Reply-To` / `References` chains throughout, machine-verified

Plus lookalike-domain invoice fraud, duplicate resends, moved deadlines,
retracted commitments, tasks that exist only inside an attachment, tasks that
survive only in quoted text, bcc-invisible recipients, `Reply-To` mismatches,
ambiguous pronoun ownership, and implicit deadlines.

## Rebuilding

```bash
python3 -m venv .venv && .venv/bin/pip install pydantic python-dotenv anthropic
```

```bash
.venv/bin/python src/build.py
```

`build.py` re-validates all 30 batch files and refuses to emit if anything is
wrong — duplicate ids, a broken `References` chain, a reply predating its parent,
a date outside the window, a `SENT` message carrying a category, a missing
required tricky case. It then writes `data/profiles/`, `data/ground_truth.jsonl`
and `results/`.

Validate a single batch on its own:

```bash
.venv/bin/python src/schema.py data/raw/founder_b1.json
```

## A note on the data

Every person, company, domain and dollar figure here is invented. Any resemblance
to a real organisation is coincidental. The mail is written to read like a leak
rather than a template — that is what makes it useful — but none of it is real.
