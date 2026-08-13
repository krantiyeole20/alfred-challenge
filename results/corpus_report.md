# Mock Gmail corpus — build report

**1500 messages** across 5 mailboxes, window 2026-06-13 → 2026-08-12.

| profile | msgs | threads | longest | attach | cal | unread | actionable | recent 30d |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| `founder` | 300 | 198 | 10 | 27 | 14 | 38 | 72 | 79.3% |
| `marketing` | 300 | 189 | 10 | 30 | 14 | 41 | 108 | 79.0% |
| `finance` | 300 | 194 | 10 | 38 | 14 | 36 | 96 | 77.0% |
| `hr` | 300 | 195 | 10 | 32 | 12 | 41 | 106 | 77.0% |
| `consulting` | 300 | 195 | 10 | 30 | 15 | 37 | 121 | 79.0% |

## Folder mix

| profile | INBOX | SENT | DRAFT | SPAM | TRASH | ARCHIVE |
|---|---|---|---|---|---|---|
| `founder` | 210 | 81 | 4 | 5 | 0 | 0 |
| `marketing` | 199 | 92 | 4 | 5 | 0 | 0 |
| `finance` | 203 | 88 | 4 | 5 | 0 | 0 |
| `hr` | 199 | 91 | 4 | 6 | 0 | 0 |
| `consulting` | 190 | 101 | 4 | 5 | 0 | 0 |

## Tricky-case coverage

| tag | founder | marketing | finance | hr | consulting |
|---|---|---|---|---|---|
| `ambiguous_pronoun_owner` | 1 | 1 | 1 | 1 | 1 |
| `bcc_invisible_recipient` | 1 | 1 | 1 | 1 | 1 |
| `cancelled_commitment` | 2 | 1 | 2 | 2 | 1 |
| `conditional_promise` | 4 | 2 | 2 | 4 | 2 |
| `deadline_moved` | 2 | 2 | 2 | 2 | 2 |
| `delivered_attachment` | 1 | 1 | 1 | 1 | 1 |
| `diffused_group_ask` | 1 | 1 | 2 | 1 | 2 |
| `duplicate_resend` | 2 | 2 | 2 | 2 | 2 |
| `forwarded_task_not_users` | 1 | 1 | 1 | 1 | 1 |
| `implicit_deadline` | 1 | 1 | 1 | 1 | 1 |
| `long_thread` | 9 | 9 | 9 | 9 | 9 |
| `lookalike_domain` | 1 | 1 | 1 | 1 | 2 |
| `newsletter_unsubscribe` | 1 | 1 | 1 | 1 | 1 |
| `out_of_office` | 3 | 2 | 2 | 2 | 5 |
| `promised_attachment` | 1 | 1 | 1 | 2 | 1 |
| `quoted_text_only_task` | 1 | 1 | 1 | 1 | 1 |
| `reply_to_mismatch` | 1 | 1 | 1 | 1 | 1 |
| `same_person_two_addresses` | 2 | 2 | 2 | 2 | 2 |
| `shifting_number` | 4 | 3 | 3 | 3 | 3 |
| `task_in_attachment_only` | 1 | 1 | 1 | 1 | 1 |
| `topic_drift` | 6 | 7 | 7 | 7 | 7 |

## Files

- `data/profiles/<profile>.canonical.jsonl` — one `Email` per line
- `data/profiles/<profile>.gmail.jsonl` — `users.messages.get` shape
- `data/profiles/<profile>.threads.json` — `users.threads.get` shape
- `data/ground_truth.jsonl` — every message carrying a tricky tag
- `results/stats.json` — the numbers above, machine-readable
