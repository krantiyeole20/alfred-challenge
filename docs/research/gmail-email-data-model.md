# Gmail email data model — research notes

Sources are linked at the bottom. This is the basis for `src/schema.py`.

## 1. The Gmail API `Message` resource

`users.messages.get` returns:

```json
{
  "id": "18f2a9c4b7d1e003",          // immutable message id (16 lowercase hex chars in practice)
  "threadId": "18f2a9c4b7d1e003",    // id of the FIRST message in the conversation
  "labelIds": ["INBOX", "UNREAD", "IMPORTANT", "CATEGORY_PERSONAL"],
  "snippet": "First ~200 chars of the body, HTML-stripped",
  "historyId": "9284471",            // monotonically increasing mailbox revision
  "internalDate": "1754990400000",   // epoch MILLISECONDS as a STRING — this is what orders the inbox
  "payload": { ...MessagePart... },
  "sizeEstimate": 24518,             // approx bytes
  "raw": "<base64url RFC 2822>"      // only when format=RAW
}
```

Key gotchas that matter for realistic mock data:

- `internalDate` is a **string** of epoch **milliseconds**, and it is what Gmail sorts by — not the `Date:` header. They can legitimately differ (delayed delivery, clock skew).
- `threadId` equals the `id` of the thread's first message. Every reply carries the same `threadId`.
- `historyId` increases across the whole mailbox, so later messages have larger values.
- `sizeEstimate` grows with attachments; a text-only mail is ~2–20 KB, one with a 2 MB PDF is ~2.8 MB (base64 inflates by ~4/3).

## 2. `MessagePart` (the MIME tree)

```json
{
  "partId": "0",
  "mimeType": "multipart/alternative",
  "filename": "",
  "headers": [{"name": "From", "value": "Dev Patel <dev@kettlehq.com>"}],
  "body": {"attachmentId": "...", "size": 1234, "data": "<base64url>"},
  "parts": [ ...MessagePart... ]
}
```

Canonical shapes:

| Message kind | Tree |
|---|---|
| Plain text only | `text/plain` |
| Normal formatted mail | `multipart/alternative` → `text/plain`, `text/html` |
| Mail with attachment | `multipart/mixed` → (`multipart/alternative` → text/plain, text/html), `application/pdf` |
| Inline image + attachment | `multipart/mixed` → `multipart/related` → (`multipart/alternative`, `image/png` w/ `Content-ID`), `application/pdf` |
| Calendar invite | `multipart/mixed` → `multipart/alternative` → (`text/plain`, `text/html`, `text/calendar; method=REQUEST`), + `application/ics` part named `invite.ics` |
| Bounce / DSN | `multipart/report; report-type=delivery-status` → `text/plain`, `message/delivery-status`, `message/rfc822` |

`partId` is `"0"`, `"1"`, `"0.0"`, `"0.1"` … by tree position. Leaf parts that are attachments carry a non-empty `filename` and an `attachmentId` in `body`; container parts have `body.size = 0` and no data.

## 3. Threading headers (RFC 5322 §3.6.4)

This is the part most mock datasets get wrong.

- `Message-ID: <unique@domain>` — globally unique, set by the sending MUA.
- `In-Reply-To:` — the `Message-ID` of the **immediate parent only**.
- `References:` — parent's `References` **plus** parent's `Message-ID`, in order. So it grows by one entry per hop and reads root → … → parent.

Worked example for a 3-message thread:

```
M1  Message-ID: <a@x>     (no In-Reply-To, no References)
M2  Message-ID: <b@y>     In-Reply-To: <a@x>        References: <a@x>
M3  Message-ID: <c@z>     In-Reply-To: <b@y>        References: <a@x> <b@y>
```

Gmail's own outbound `Message-ID`s look like `<CAF=abc123...@mail.gmail.com>`; server-sent mail uses the sending platform's domain (e.g. `<01000191...@email.klaviyo.com>`).

A **forward** starts a new thread in Gmail (new `threadId`), has no `In-Reply-To`, subject prefixed `Fwd:`, and quotes the original with a `---------- Forwarded message ---------` block.

## 4. Labels

System labels: `INBOX`, `SENT`, `DRAFT`, `SPAM`, `TRASH`, `UNREAD`, `STARRED`, `IMPORTANT`, plus the tab categories `CATEGORY_PERSONAL`, `CATEGORY_SOCIAL`, `CATEGORY_PROMOTIONS`, `CATEGORY_UPDATES`, `CATEGORY_FORUMS`. User labels get opaque ids like `Label_4823719283`.

Rules worth honouring:
- `SENT` and `DRAFT` are applied by Gmail, never manually. A `SENT` message is normally **not** `UNREAD` and has no `CATEGORY_*`.
- Exactly one `CATEGORY_*` per received message.
- `SPAM` and `TRASH` are mutually exclusive with `INBOX`.
- `UNREAD` is presence/absence — there is no "READ" label.

## 5. Metadata headers beyond the obvious

Delivery/auth (added by the receiving side, so they appear on **received** mail only):
`Delivered-To`, `Received` (multiple, newest first), `Return-Path`, `Received-SPF`, `Authentication-Results` (spf/dkim/dmarc verdicts), `DKIM-Signature`, `ARC-Seal` / `ARC-Message-Signature` / `ARC-Authentication-Results` (Google re-seals forwarded mail), `X-Google-Smtp-Source`, `X-Received`.

Bulk / marketing:
`List-Unsubscribe: <mailto:...>, <https://...>`, `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058 — Gmail requires this from bulk senders since Feb 2024; the DKIM signature must cover both headers), `List-ID`, `Precedence: bulk`, `Feedback-ID`, `X-Campaign-ID`, `X-Mailer`.

Automated responses (RFC 3834):
`Auto-Submitted: auto-replied` (out-of-office) or `auto-generated` (system notification), `X-Auto-Response-Suppress: All` (Exchange), `Precedence: bulk`. An OOO with `Auto-Submitted: no` or missing is a loop hazard — real Exchange/Gmail vacation responders set it.

Other commonly present: `MIME-Version`, `Content-Type`, `Content-Transfer-Encoding`, `Content-Disposition`, `Content-ID` (inline images, referenced as `cid:`), `Reply-To`, `Sender` (differs from `From` for send-on-behalf), `X-Priority` / `Importance`, `Thread-Topic` + `Thread-Index` (Outlook senders), `X-Original-Sender` / `X-Original-Authentication-Results` (Google Groups), `X-Entity-Ref-ID` (Google), `X-Failed-Recipients` (bounces).

## 6. Calendar invites

Gmail-rendered invites carry a `text/calendar; charset=UTF-8; method=REQUEST` part inside `multipart/alternative`, plus an `invite.ics` attachment. The ICS body carries `UID`, `DTSTAMP`, `DTSTART`/`DTEND` (with `TZID`), `ORGANIZER;CN=`, one `ATTENDEE` line per invitee with `PARTSTAT=NEEDS-ACTION|ACCEPTED|DECLINED|TENTATIVE` and `RSVP=TRUE`, `SEQUENCE`, `STATUS:CONFIRMED`, `TRANSP:OPAQUE`, and `RRULE` for recurrence.

`METHOD` drives what the message means: `REQUEST` = new/updated invite, `REPLY` = an RSVP, `CANCEL` = cancellation. Updates reuse the same `UID` with an incremented `SEQUENCE` — that's how "the 2pm moved to 3pm" is modelled.

## Sources

- [Gmail API — users.messages resource](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages)
- [Gmail API — labels guide](https://developers.google.com/workspace/gmail/api/guides/labels)
- [RFC 5322 — Internet Message Format](https://www.rfc-editor.org/rfc/rfc5322.html)
- [RFC 8058 — one-click List-Unsubscribe](https://www.mailgun.com/blog/deliverability/what-is-rfc-8058/)
- [RFC 3834 — automatic responses](https://datatracker.ietf.org/doc/html/rfc3834)
- [Precedence / Auto-Submitted in practice](https://reviewmyemails.com/emailalmanac/esp-and-infrastructure/message-mechanics-mime-attachments-list-unsubscribe/precedence-bulk-auto-submitted-headers)
- [ARC headers in Gmail](https://postmarkapp.com/blog/what-is-arc-or-authenticated-received-chain)
