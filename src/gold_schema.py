"""Typed models for the gold answer set.

Mirrors docs/gold-set-spec.md. Used by src/build_gold.py to validate the five
per-mailbox files before they are merged into data/gold_set.json.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

QUESTION_KEYS = [
    "q1_needs_attention",
    "q2_forgetting",
    "q3_waiting_on_me",
    "q4_waiting_on_others",
    "q5_what_changed",
    "q6_slipping_through_cracks",
]

QUESTION_TEXT = {
    "q1_needs_attention": "What needs my attention?",
    "q2_forgetting": "What am I forgetting?",
    "q3_waiting_on_me": "What is waiting on me?",
    "q4_waiting_on_others": "What am I waiting on someone else for?",
    "q5_what_changed": "What changed?",
    "q6_slipping_through_cracks": "What important thing might be slipping through the cracks?",
}

ITEM_ID_RE = re.compile(r"^[a-z]+-q[1-6]-\d{2}$")


class Tier(str, Enum):
    MUST = "must_include"
    ACCEPTABLE = "acceptable"
    BORDERLINE = "borderline"


class Consequence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    rank: int = Field(ge=1)
    tier: Tier
    claim: str = Field(min_length=10)
    thread_id: str
    anchor_message_id: str
    message_ids: list[str] = Field(min_length=1)
    evidence_quote: str = Field(min_length=5, max_length=400)
    why_it_qualifies: str = Field(min_length=10)
    counterparty: str | None = None
    due_date: str | None = None
    due_basis: Literal["explicit", "implicit", "none"] = "none"
    days_open: int | None = None
    consequence: Consequence
    tricky_tags: list[str] = Field(default_factory=list)

    # Q5 only
    changed_field: str | None = None
    previous_value: str | None = None
    current_value: str | None = None
    from_message_id: str | None = None
    to_message_id: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "Item":
        if not ITEM_ID_RE.match(self.item_id):
            raise ValueError(f"item_id {self.item_id!r} must look like founder-q1-01")
        if self.anchor_message_id not in self.message_ids:
            raise ValueError(f"{self.item_id}: anchor_message_id not in message_ids")
        if self.due_date and self.due_basis == "none":
            raise ValueError(f"{self.item_id}: has a due_date but due_basis is 'none'")
        if self.due_basis in ("explicit", "implicit") and not self.due_date:
            raise ValueError(f"{self.item_id}: due_basis {self.due_basis!r} but no due_date")
        return self


class Distractor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    subject: str
    why_excluded: str = Field(min_length=10)
    trap: str


class AnswerSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    prose_answer: str = Field(min_length=100)
    ranked: list[str]
    items: list[Item]
    must_not_include: list[Distractor]
    curator_notes: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "AnswerSet":
        by_id = {i.item_id: i for i in self.items}
        if len(by_id) != len(self.items):
            raise ValueError("duplicate item_id within an answer set")

        for iid in self.ranked:
            if iid not in by_id:
                raise ValueError(f"ranked references unknown item_id {iid!r}")
            if by_id[iid].tier is Tier.BORDERLINE:
                raise ValueError(f"ranked contains borderline item {iid!r}")
        if len(set(self.ranked)) != len(self.ranked):
            raise ValueError("duplicate entries in ranked")

        scored = {i.item_id for i in self.items if i.tier is not Tier.BORDERLINE}
        missing = scored - set(self.ranked)
        if missing:
            raise ValueError(
                f"must_include/acceptable items absent from ranked: {sorted(missing)}"
            )

        cited = {m for i in self.items for m in i.message_ids}
        clash = cited & {d.message_id for d in self.must_not_include}
        if clash:
            raise ValueError(f"message cited as both evidence and distractor: {sorted(clash)}")
        return self


class Owner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    email: str
    role: str
    timezone: str


class MailboxGold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    owner: Owner
    as_of: str
    answers: dict[str, AnswerSet]

    @model_validator(mode="after")
    def _all_questions(self) -> "MailboxGold":
        missing = set(QUESTION_KEYS) - set(self.answers)
        if missing:
            raise ValueError(f"{self.profile_id}: missing answer sets {sorted(missing)}")
        extra = set(self.answers) - set(QUESTION_KEYS)
        if extra:
            raise ValueError(f"{self.profile_id}: unknown answer keys {sorted(extra)}")
        for key, a in self.answers.items():
            prefix = f"{self.profile_id}-{key.split('_')[0]}-"
            for i in a.items:
                if not i.item_id.startswith(prefix):
                    raise ValueError(
                        f"{i.item_id}: expected prefix {prefix!r} for {key}"
                    )
        return self
