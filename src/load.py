"""Convenience loaders for the built corpus.

    from load import load_profile, load_all, threads_of, tricky

    mailbox = load_profile("founder")            # list[Email], oldest first
    inbox   = [e for e in mailbox if e.folder is Folder.INBOX]
    convo   = threads_of(mailbox)["a20f18c2e4b60001"]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from schema import Email, TrickyTag  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "data" / "profiles"

PROFILE_IDS = ["founder", "marketing", "finance", "hr", "consulting"]


def load_profile(profile_id: str) -> list[Email]:
    """All messages for one mailbox, oldest first."""
    path = PROFILES_DIR / f"{profile_id}.canonical.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} — run `python src/build.py` first")
    with path.open() as fh:
        return [Email.model_validate_json(line) for line in fh if line.strip()]


def load_all() -> dict[str, list[Email]]:
    return {pid: load_profile(pid) for pid in PROFILE_IDS}


def load_gmail(profile_id: str) -> Iterator[dict[str, Any]]:
    """Raw Gmail-API-shaped message resources, as `users.messages.get` returns them."""
    path = PROFILES_DIR / f"{profile_id}.gmail.jsonl"
    with path.open() as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def threads_of(emails: list[Email]) -> dict[str, list[Email]]:
    """Group messages into threads, each ordered by thread_position."""
    out: dict[str, list[Email]] = defaultdict(list)
    for e in emails:
        out[e.thread_id].append(e)
    for msgs in out.values():
        msgs.sort(key=lambda m: m.thread_position)
    return dict(out)


def tricky(emails: list[Email], tag: TrickyTag | str | None = None) -> list[Email]:
    """Every message carrying a tricky tag, optionally filtered to one tag."""
    if tag is None:
        return [e for e in emails if e.meta.tricky_tags]
    want = TrickyTag(tag)
    return [e for e in emails if want in e.meta.tricky_tags]


def render(email: Email, *, width: int = 88) -> str:
    """Human-readable rendering, roughly what a mail client would show."""
    head = [
        f"From:    {email.from_.rfc()}",
        f"To:      {', '.join(a.rfc() for a in email.to) or '(none)'}",
    ]
    if email.cc:
        head.append(f"Cc:      {', '.join(a.rfc() for a in email.cc)}")
    if email.bcc:
        head.append(f"Bcc:     {', '.join(a.rfc() for a in email.bcc)}")
    head += [
        f"Date:    {email.date.strftime('%a, %d %b %Y %H:%M %z')}",
        f"Subject: {email.subject}",
        f"Folder:  {email.folder.value}"
        + (f" / {email.category.value}" if email.category else "")
        + (" / UNREAD" if email.is_unread else ""),
    ]
    if email.attachments:
        head.append(
            "Attach:  "
            + ", ".join(f"{a.filename} ({a.size_bytes:,}B)" for a in email.attachments)
        )
    if email.meta.tricky_tags:
        head.append("Tricky:  " + ", ".join(t.value for t in email.meta.tricky_tags))
    return "\n".join(head) + "\n" + "-" * width + "\n" + email.body_text + "\n"


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "founder"
    mailbox = load_profile(which)
    ths = threads_of(mailbox)
    print(f"{which}: {len(mailbox)} messages in {len(ths)} threads")
    longest = max(ths.values(), key=len)
    print(f"longest thread: {len(longest)} messages — {longest[0].subject!r}\n")
    print(render(longest[0]))
