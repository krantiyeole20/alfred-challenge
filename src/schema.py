"""Canonical mock-email schema.

Two representations live here:

  * ``Email``          - the canonical, flat-ish record that subagents author and
                         downstream code consumes.
  * ``to_gmail_api()``  - projects an ``Email`` into the exact shape Gmail's
                         ``users.messages.get`` returns (nested MIME payload,
                         header list, labelIds, internalDate, sizeEstimate).

Derived from docs/research/gmail-email-data-model.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- #
# Vocabularies
# --------------------------------------------------------------------------- #


class Folder(str, Enum):
    INBOX = "INBOX"
    SENT = "SENT"
    DRAFT = "DRAFT"
    SPAM = "SPAM"
    TRASH = "TRASH"
    ARCHIVE = "ARCHIVE"  # no INBOX label, not spam/trash


class Category(str, Enum):
    PERSONAL = "CATEGORY_PERSONAL"
    SOCIAL = "CATEGORY_SOCIAL"
    PROMOTIONS = "CATEGORY_PROMOTIONS"
    UPDATES = "CATEGORY_UPDATES"
    FORUMS = "CATEGORY_FORUMS"


class EmailClass(str, Enum):
    """What the message fundamentally is - the primary ground-truth axis."""

    GENUINE_WORK = "genuine_work"
    INTERNAL_ANNOUNCEMENT = "internal_announcement"
    CLIENT_CUSTOMER = "client_customer"
    VENDOR_INVOICE = "vendor_invoice"
    RECRUITING = "recruiting"
    LEGAL_COMPLIANCE = "legal_compliance"
    CALENDAR = "calendar"
    AUTOMATED_NOTIFICATION = "automated_notification"
    SECURITY_ALERT = "security_alert"
    NEWSLETTER = "newsletter"
    PROMOTIONAL = "promotional"
    COLD_OUTREACH = "cold_outreach"
    SPAM = "spam"
    PHISHING = "phishing"
    PERSONAL = "personal"
    AUTO_REPLY = "auto_reply"
    BOUNCE = "bounce"
    SOCIAL = "social"


class TrickyTag(str, Enum):
    """Deliberately planted adversarial cases. Empty for ordinary mail."""

    FORWARDED_TASK_NOT_USERS = "forwarded_task_not_users"
    SAME_PERSON_TWO_ADDRESSES = "same_person_two_addresses"
    CONDITIONAL_PROMISE = "conditional_promise"
    SHIFTING_NUMBER = "shifting_number"
    DIFFUSED_GROUP_ASK = "diffused_group_ask"
    NEWSLETTER_UNSUBSCRIBE = "newsletter_unsubscribe"
    OUT_OF_OFFICE = "out_of_office"
    LONG_THREAD = "long_thread"
    TOPIC_DRIFT = "topic_drift"
    PROMISED_ATTACHMENT = "promised_attachment"
    DELIVERED_ATTACHMENT = "delivered_attachment"
    DEADLINE_MOVED = "deadline_moved"
    SUPERSEDED = "superseded"
    AMBIGUOUS_PRONOUN_OWNER = "ambiguous_pronoun_owner"
    IMPLICIT_DEADLINE = "implicit_deadline"
    LOOKALIKE_DOMAIN = "lookalike_domain"
    DUPLICATE_RESEND = "duplicate_resend"
    TASK_IN_ATTACHMENT_ONLY = "task_in_attachment_only"
    QUOTED_TEXT_ONLY_TASK = "quoted_text_only_task"
    CANCELLED_COMMITMENT = "cancelled_commitment"
    BCC_INVISIBLE_RECIPIENT = "bcc_invisible_recipient"
    REPLY_TO_MISMATCH = "reply_to_mismatch"


# --------------------------------------------------------------------------- #
# Leaf models
# --------------------------------------------------------------------------- #


class EmailAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    email: str

    @field_validator("email")
    @classmethod
    def _lower_and_check(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError(f"not an email address: {v!r}")
        return v

    def rfc(self) -> str:
        return f"{self.name} <{self.email}>" if self.name else self.email


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachment_id: str
    filename: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    is_inline: bool = False
    content_id: str | None = None  # e.g. "<logo@habitatgoods>" for cid: refs
    extracted_text: str | None = Field(
        default=None,
        description="Text rendering of the attachment's content: CSV rows, PDF/docx "
        "prose, ICS body, spreadsheet dump. This is what a parser would recover.",
    )

    @model_validator(mode="after")
    def _inline_needs_cid(self) -> "Attachment":
        if self.is_inline and not self.content_id:
            raise ValueError(f"inline attachment {self.filename!r} needs a content_id")
        return self


class CalendarAttendee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    name: str | None = None
    role: Literal["REQ-PARTICIPANT", "OPT-PARTICIPANT", "CHAIR", "NON-PARTICIPANT"] = (
        "REQ-PARTICIPANT"
    )
    partstat: Literal["NEEDS-ACTION", "ACCEPTED", "DECLINED", "TENTATIVE"] = (
        "NEEDS-ACTION"
    )
    rsvp: bool = True


class CalendarEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    method: Literal["REQUEST", "REPLY", "CANCEL", "PUBLISH"] = "REQUEST"
    sequence: int = 0
    summary: str
    description: str | None = None
    location: str | None = None
    conference_url: str | None = None
    organizer: EmailAddress
    attendees: list[CalendarAttendee] = Field(default_factory=list)
    dtstart: datetime
    dtend: datetime
    timezone: str = "America/Los_Angeles"
    all_day: bool = False
    rrule: str | None = None  # e.g. "FREQ=WEEKLY;BYDAY=MO"
    status: Literal["CONFIRMED", "TENTATIVE", "CANCELLED"] = "CONFIRMED"

    @model_validator(mode="after")
    def _ordered(self) -> "CalendarEvent":
        if self.dtend < self.dtstart:
            raise ValueError("dtend precedes dtstart")
        return self


class GroundTruth(BaseModel):
    """Annotations that make the corpus evaluable. Never rendered into the mail."""

    model_config = ConfigDict(extra="forbid")

    email_class: EmailClass
    tricky_tags: list[TrickyTag] = Field(default_factory=list)
    is_actionable_for_user: bool = Field(
        description="Does this message create a real obligation for the profile owner?"
    )
    action_owner: str | None = Field(
        default=None,
        description="Email address of whoever actually owns the action. Deliberately "
        "NOT the profile owner for forwarded/diffused cases.",
    )
    action_summary: str | None = None
    due_date: str | None = Field(default=None, description="ISO date, if one exists.")
    commitment_made_by_user: str | None = Field(
        default=None, description="Promise the profile owner made in this message."
    )
    commitment_is_conditional: bool = False
    commitment_condition: str | None = None
    superseded_by_message_id: str | None = None
    notes: str | None = Field(
        default=None, description="Why this message is interesting or tricky."
    )


# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #


class Email(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- identity -------------------------------------------------------- #
    id: str = Field(description="16 lowercase hex chars, unique corpus-wide.")
    thread_id: str = Field(description="id of the first message in the thread.")
    profile_id: str = Field(description="Which mailbox this record belongs to.")
    history_id: str

    # --- threading headers ------------------------------------------------ #
    message_id_header: str = Field(description="RFC 5322 Message-ID, angle-bracketed.")
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)
    thread_position: int = Field(ge=1, description="1-based index within the thread.")
    thread_length: int = Field(ge=1)

    # --- participants ------------------------------------------------------ #
    from_: EmailAddress = Field(alias="from")
    sender: EmailAddress | None = None  # send-on-behalf-of
    reply_to: EmailAddress | None = None
    to: list[EmailAddress] = Field(default_factory=list)
    cc: list[EmailAddress] = Field(default_factory=list)
    bcc: list[EmailAddress] = Field(default_factory=list)
    delivered_to: str | None = None

    # --- content ----------------------------------------------------------- #
    subject: str
    snippet: str = Field(description="First ~180 chars of body_text, whitespace-collapsed.")
    body_text: str
    body_html: str | None = None

    # --- time -------------------------------------------------------------- #
    date: datetime = Field(description="RFC 5322 Date header, timezone-aware.")
    internal_date_ms: int = Field(description="Epoch milliseconds; Gmail's sort key.")

    # --- state ------------------------------------------------------------- #
    folder: Folder
    category: Category | None = None
    is_unread: bool = False
    is_starred: bool = False
    is_important: bool = False
    user_labels: list[str] = Field(default_factory=list)

    # --- payload ----------------------------------------------------------- #
    attachments: list[Attachment] = Field(default_factory=list)
    calendar_event: CalendarEvent | None = None
    extra_headers: dict[str, str] = Field(
        default_factory=dict,
        description="List-Unsubscribe, Auto-Submitted, Precedence, X-Mailer, "
        "Authentication-Results, Return-Path, Feedback-ID, ...",
    )
    size_estimate: int = Field(default=0, ge=0)

    # --- annotations -------------------------------------------------------- #
    meta: GroundTruth

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # --- validation --------------------------------------------------------- #

    @field_validator("id", "thread_id")
    @classmethod
    def _hex16(cls, v: str) -> str:
        if len(v) != 16 or any(c not in "0123456789abcdef" for c in v):
            raise ValueError(f"expected 16 lowercase hex chars, got {v!r}")
        return v

    @field_validator("message_id_header", "in_reply_to")
    @classmethod
    def _angled(cls, v: str | None) -> str | None:
        if v is not None and not (v.startswith("<") and v.endswith(">")):
            raise ValueError(f"Message-ID must be angle-bracketed: {v!r}")
        return v

    @field_validator("references")
    @classmethod
    def _all_angled(cls, v: list[str]) -> list[str]:
        for ref in v:
            if not (ref.startswith("<") and ref.endswith(">")):
                raise ValueError(f"References entry not angle-bracketed: {ref!r}")
        return v

    @model_validator(mode="after")
    def _coherent(self) -> "Email":
        if self.thread_position == 1:
            if self.in_reply_to is not None:
                raise ValueError(f"{self.id}: thread root must not set in_reply_to")
        else:
            if self.in_reply_to is None:
                raise ValueError(f"{self.id}: reply must set in_reply_to")
            if self.in_reply_to not in self.references:
                raise ValueError(
                    f"{self.id}: References must end with the parent's Message-ID"
                )
        if self.thread_position > self.thread_length:
            raise ValueError(f"{self.id}: thread_position exceeds thread_length")

        if self.folder in (Folder.SENT, Folder.DRAFT):
            if self.category is not None:
                raise ValueError(f"{self.id}: {self.folder.value} carries no CATEGORY_*")
            if self.is_unread:
                raise ValueError(f"{self.id}: {self.folder.value} cannot be UNREAD")
        elif self.folder is Folder.INBOX and self.category is None:
            raise ValueError(f"{self.id}: INBOX message needs exactly one CATEGORY_*")

        if self.calendar_event and self.meta.email_class is not EmailClass.CALENDAR:
            raise ValueError(f"{self.id}: calendar_event set but email_class is not calendar")

        seen = set()
        for a in self.attachments:
            if a.attachment_id in seen:
                raise ValueError(f"{self.id}: duplicate attachment_id {a.attachment_id}")
            seen.add(a.attachment_id)
        return self

    # --- convenience --------------------------------------------------------- #

    @property
    def label_ids(self) -> list[str]:
        labels: list[str] = []
        if self.folder is Folder.INBOX:
            labels.append("INBOX")
        elif self.folder in (Folder.SENT, Folder.DRAFT, Folder.SPAM, Folder.TRASH):
            labels.append(self.folder.value)
        if self.is_unread:
            labels.append("UNREAD")
        if self.is_starred:
            labels.append("STARRED")
        if self.is_important:
            labels.append("IMPORTANT")
        if self.category:
            labels.append(self.category.value)
        labels.extend(self.user_labels)
        return labels


# --------------------------------------------------------------------------- #
# Gmail API projection
# --------------------------------------------------------------------------- #


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _part(
    part_id: str,
    mime_type: str,
    *,
    filename: str = "",
    headers: list[dict[str, str]] | None = None,
    body: dict[str, Any] | None = None,
    parts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "partId": part_id,
        "mimeType": mime_type,
        "filename": filename,
        "headers": headers or [],
        "body": body or {"size": 0},
    }
    if parts:
        node["parts"] = parts
    return node


def _ics(ev: CalendarEvent) -> str:
    fmt = "%Y%m%dT%H%M%S"
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Google Inc//Google Calendar 70.9054//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        f"METHOD:{ev.method}",
        "BEGIN:VEVENT",
        f"UID:{ev.uid}",
        f"DTSTAMP:{ev.dtstart.strftime(fmt)}Z",
        f"DTSTART;TZID={ev.timezone}:{ev.dtstart.strftime(fmt)}",
        f"DTEND;TZID={ev.timezone}:{ev.dtend.strftime(fmt)}",
        f"SEQUENCE:{ev.sequence}",
        f"STATUS:{ev.status}",
        f"SUMMARY:{ev.summary}",
        "TRANSP:OPAQUE",
    ]
    if ev.rrule:
        lines.append(f"RRULE:{ev.rrule}")
    if ev.location:
        lines.append(f"LOCATION:{ev.location}")
    if ev.description:
        lines.append("DESCRIPTION:" + ev.description.replace("\n", "\\n"))
    org = ev.organizer
    lines.append(
        f"ORGANIZER;CN={org.name or org.email}:mailto:{org.email}"
    )
    for att in ev.attendees:
        lines.append(
            f"ATTENDEE;CUTYPE=INDIVIDUAL;ROLE={att.role};PARTSTAT={att.partstat};"
            f"RSVP={'TRUE' if att.rsvp else 'FALSE'};CN={att.name or att.email};"
            f"X-NUM-GUESTS=0:mailto:{att.email}"
        )
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines)


def build_headers(email: Email) -> list[dict[str, str]]:
    """RFC 5322 header list, in the order Gmail typically reports them."""
    h: list[tuple[str, str]] = []
    if email.delivered_to:
        h.append(("Delivered-To", email.delivered_to))
    h.append(("MIME-Version", "1.0"))
    h.append(("Date", email.date.strftime("%a, %d %b %Y %H:%M:%S %z")))
    h.append(("Message-ID", email.message_id_header))
    if email.in_reply_to:
        h.append(("In-Reply-To", email.in_reply_to))
    if email.references:
        h.append(("References", " ".join(email.references)))
    h.append(("Subject", email.subject))
    h.append(("From", email.from_.rfc()))
    if email.sender:
        h.append(("Sender", email.sender.rfc()))
    if email.reply_to:
        h.append(("Reply-To", email.reply_to.rfc()))
    if email.to:
        h.append(("To", ", ".join(a.rfc() for a in email.to)))
    if email.cc:
        h.append(("Cc", ", ".join(a.rfc() for a in email.cc)))
    if email.bcc:
        h.append(("Bcc", ", ".join(a.rfc() for a in email.bcc)))
    for name, value in email.extra_headers.items():
        h.append((name, value))

    if email.attachments or email.calendar_event:
        ctype = 'multipart/mixed; boundary="000000000000%s"' % email.id[:12]
    elif email.body_html:
        ctype = 'multipart/alternative; boundary="000000000000%s"' % email.id[:12]
    else:
        ctype = 'text/plain; charset="UTF-8"'
    h.append(("Content-Type", ctype))
    return [{"name": n, "value": v} for n, v in h]


def build_payload(email: Email) -> dict[str, Any]:
    """Assemble the MessagePart tree matching the message's content."""
    headers = build_headers(email)

    text_part = _part(
        "0",
        "text/plain",
        headers=[{"name": "Content-Type", "value": 'text/plain; charset="UTF-8"'}],
        body={"size": len(email.body_text.encode()), "data": _b64(email.body_text)},
    )

    alt_children = [text_part]
    if email.body_html:
        alt_children.append(
            _part(
                "1",
                "text/html",
                headers=[{"name": "Content-Type", "value": 'text/html; charset="UTF-8"'}],
                body={"size": len(email.body_html.encode()), "data": _b64(email.body_html)},
            )
        )

    if email.calendar_event:
        ics = _ics(email.calendar_event)
        alt_children.append(
            _part(
                str(len(alt_children)),
                "text/calendar",
                headers=[
                    {
                        "name": "Content-Type",
                        "value": f'text/calendar; charset="UTF-8"; '
                        f"method={email.calendar_event.method}",
                    }
                ],
                body={"size": len(ics.encode()), "data": _b64(ics)},
            )
        )

    if len(alt_children) == 1 and not email.attachments:
        root = dict(text_part)
        root["partId"] = ""
        root["headers"] = headers
        return root

    if len(alt_children) == 1:
        # attachments + a text-only body: Gmail emits multipart/mixed -> text/plain
        body_node = dict(text_part)
        body_node["partId"] = "0"
        children: list[dict[str, Any]] = [body_node]
    else:
        alt = _part("0", "multipart/alternative", parts=alt_children)
        for i, child in enumerate(alt["parts"]):
            child["partId"] = f"0.{i}"
        if not email.attachments:
            alt["partId"] = ""
            alt["headers"] = headers
            return alt
        children = [alt]
    for i, att in enumerate(email.attachments, start=1):
        disp = "inline" if att.is_inline else "attachment"
        att_headers = [
            {"name": "Content-Type", "value": f'{att.mime_type}; name="{att.filename}"'},
            {
                "name": "Content-Disposition",
                "value": f'{disp}; filename="{att.filename}"',
            },
            {"name": "Content-Transfer-Encoding", "value": "base64"},
        ]
        if att.content_id:
            att_headers.append({"name": "Content-ID", "value": att.content_id})
        children.append(
            _part(
                str(i),
                att.mime_type,
                filename=att.filename,
                headers=att_headers,
                body={"attachmentId": att.attachment_id, "size": att.size_bytes},
            )
        )

    return _part("", "multipart/mixed", headers=headers, parts=children)


def compute_size_estimate(email: Email) -> int:
    base = len(email.body_text.encode()) + len((email.body_html or "").encode())
    base += sum(len(f"{h['name']}: {h['value']}") for h in build_headers(email))
    base += sum(int(a.size_bytes * 4 / 3) for a in email.attachments)
    return base + 512


def to_gmail_api(email: Email) -> dict[str, Any]:
    """Project a canonical Email into a users.messages.get response body."""
    return {
        "id": email.id,
        "threadId": email.thread_id,
        "labelIds": email.label_ids,
        "snippet": email.snippet,
        "historyId": email.history_id,
        "internalDate": str(email.internal_date_ms),
        "payload": build_payload(email),
        "sizeEstimate": email.size_estimate or compute_size_estimate(email),
    }


def to_gmail_thread(emails: list[Email]) -> dict[str, Any]:
    """Project a set of same-thread Emails into a users.threads.get response body."""
    ordered = sorted(emails, key=lambda e: e.thread_position)
    return {
        "id": ordered[0].thread_id,
        "historyId": max(e.history_id for e in ordered),
        "messages": [to_gmail_api(e) for e in ordered],
    }


def stable_attachment_id(email_id: str, filename: str) -> str:
    """Gmail attachment ids are long opaque base64url blobs; mimic deterministically."""
    digest = hashlib.sha256(f"{email_id}:{filename}".encode()).digest()
    return "ANGjdJ_" + base64.urlsafe_b64encode(digest).decode().rstrip("=")[:56]


if __name__ == "__main__":  # tiny smoke test
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print(json.dumps(Email.model_json_schema(), indent=2)[:2000])
    else:
        with open(path) as fh:
            records = json.load(fh)
        for rec in records:
            Email.model_validate(rec)
        print(f"OK: {len(records)} records valid")
