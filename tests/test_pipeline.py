"""Invariant tests for the pipeline.

These cover the rules that are expensive to get wrong and easy to break
silently: the identity merge that protects the fraud signal, the noise gate
that decides what reaches the model, and the quote check that is the only
thing standing between a citation and a fabrication.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.pipeline import config
from src.pipeline.db import connect
from src.pipeline.extract import find_quote
from src.pipeline.identity import domain_relation, may_merge, registrable
from src.pipeline.load_corpus import normalize_subject, split_novel
from src.pipeline.questions import QUESTIONS, ask
from src.pipeline.reduce import match_key, normalize_title, resolve_due
from src.pipeline.signals import automation, date_mentions, noise


# ── identity: the merge that must not swallow impersonation ──────────


class TestIdentity:
    @pytest.mark.parametrize(
        "domain,expected",
        [
            ("mail.notion.so", "notion.so"),
            ("notion.so", "notion.so"),
            ("email.united.com", "united.com"),
            ("foo.co.uk", "foo.co.uk"),
            ("a.b.foo.co.uk", "foo.co.uk"),
        ],
    )
    def test_registrable(self, domain, expected):
        assert registrable(domain) == expected

    def test_subdomain_is_same_org(self):
        # email.united.com sits UNDER united.com -- one company, two hosts.
        assert domain_relation("email.united.com", "united.com") == "same"

    def test_embedded_brand_is_lookalike(self):
        # The shape real invoice fraud takes: a sibling registrable domain
        # that merely contains the target's name.
        assert domain_relation("klaviyo-billing.com", "klaviyo.com") == "embedded_brand"
        assert domain_relation("harborlinebank-support.com", "harborlinebank.com") == "embedded_brand"
        assert domain_relation("kettlehq-billing.com", "kettlehq.com") == "embedded_brand"

    def test_typosquat_is_lookalike(self):
        assert domain_relation("solentmachining-ap.com", "solentmachining.com") in (
            "embedded_brand", "typosquat",
        )

    def test_sibling_tld_flagged_but_weaker(self):
        # docusign.com vs docusign.net is genuinely ambiguous; it must still
        # block the merge, but be distinguishable from brand embedding.
        assert domain_relation("docusign.net", "docusign.com") == "sibling_tld"

    def test_unrelated_domains_may_merge(self):
        # A personal and a work address for one human.
        assert domain_relation("gmail.com", "pembertonwells.com") == "unrelated"
        ok, _ = may_merge("nadia.haddad.cpa@gmail.com", "nhaddad@pembertonwells.com")
        assert ok

    def test_impersonator_never_merges(self):
        ok, relation = may_merge("billing@klaviyo.com", "billing@klaviyo-billing.com")
        assert not ok
        assert relation == "embedded_brand"


# ── the anti-fabrication guard ───────────────────────────────────────


class TestQuoteVerification:
    BODY = "Hi Maya,\n\nCan you get the signed addendum back by August 5th?\n\nDana"

    def test_exact_quote_found(self):
        ok, offset = find_quote("Can you get the signed addendum back by August 5th?", [self.BODY])
        assert ok
        assert self.BODY[offset:].startswith("Can you get")

    def test_line_wrapping_tolerated(self):
        # Wrapping is a formatting artefact, not a fabrication.
        wrapped = "Can you get the signed addendum\nback by August 5th?"
        ok, _ = find_quote(wrapped, [self.BODY])
        assert ok

    def test_altered_words_rejected(self):
        # THE test that matters: changing what was said must fail, however
        # plausible the result reads.
        ok, _ = find_quote("Can you get the signed addendum back by August 6th?", [self.BODY])
        assert not ok

    def test_invented_quote_rejected(self):
        ok, _ = find_quote("I approve the budget increase of $50,000.", [self.BODY])
        assert not ok

    def test_trivially_short_quote_rejected(self):
        # A two-word "quote" matches everywhere and proves nothing.
        ok, _ = find_quote("the", [self.BODY])
        assert not ok

    def test_quote_found_in_attachment(self):
        att = "ACTION: submit the Q3 forecast by Friday."
        ok, _ = find_quote("submit the Q3 forecast by Friday", ["", att])
        assert ok


# ── the noise gate: what reaches the model ───────────────────────────


class TestNoiseGate:
    def test_mailing_list_is_noise(self):
        is_noise, reason = noise({"List-ID": "<x.list>"}, None, "INBOX", True)
        assert is_noise and reason == "list_archive"

    def test_automated_promotion_is_noise(self):
        is_noise, _ = noise(
            {"List-Unsubscribe": "<u>"}, "CATEGORY_PROMOTIONS", "INBOX", True
        )
        assert is_noise

    def test_category_alone_does_not_gate(self):
        # Gmail files real speaking-slot confirmations under Promotions.
        # Without a machine-sent marker the category is not enough.
        is_noise, _ = noise({}, "CATEGORY_PROMOTIONS", "INBOX", False)
        assert not is_noise

    def test_transactional_subject_overrides_category(self):
        # "your plan renews in 5 days" is a real obligation regardless of
        # which tab the provider filed it under.
        is_noise, _ = noise(
            {"List-Unsubscribe": "<u>", "Precedence": "bulk"},
            "CATEGORY_PROMOTIONS", "INBOX", True,
            subject="Your Harvest plan renews in 5 days - update your payment method",
        )
        assert not is_noise

    def test_spam_folder_is_not_gated(self):
        # Phishing and lookalike invoice fraud land in SPAM, and those are
        # exactly what the owner needs told about.
        is_noise, _ = noise({}, None, "SPAM", False)
        assert not is_noise

    def test_out_of_office_is_not_gated(self):
        # Auto-replies defer and close real obligations.
        is_noise, _ = noise({"Auto-Submitted": "auto-replied"}, None, "INBOX", True)
        assert not is_noise

    def test_noreply_is_automated_but_not_noise(self):
        is_auto, reason = automation({}, "no-reply@stripe.com")
        assert is_auto and reason == "noreply_local"
        assert not noise({}, None, "INBOX", is_auto)[0]


# ── normalisation ────────────────────────────────────────────────────


class TestNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Re: Q3 Budget", "q3 budget"),
            ("RE: Fwd: Re: Q3 Budget", "q3 budget"),
            ("FW: [EXTERNAL] Q3  Budget ", "[external] q3 budget"),
            (None, ""),
        ],
    )
    def test_normalize_subject(self, raw, expected):
        assert normalize_subject(raw) == expected

    def test_split_novel_strips_quoted_tail(self):
        body = "My reply.\n\nOn Mon, Jun 2, 2026 at 9:00 AM Dana wrote:\n> original"
        novel, full = split_novel(body)
        assert novel == "My reply."
        # The full text is retained: quoted_text_only_task plants a real
        # obligation inside quoted material.
        assert "original" in full

    def test_split_novel_keeps_unquoted_body(self):
        novel, full = split_novel("Just one message.")
        assert novel == full == "Just one message."


# ── the fold ─────────────────────────────────────────────────────────


class TestReducer:
    def test_normalize_title_is_order_insensitive(self):
        assert normalize_title("send the Q3 deck") == normalize_title("Q3 deck sent")

    def test_match_key_separates_owners(self):
        a = match_key("request", "person-1", "sign the DPA")
        b = match_key("request", "person-2", "sign the DPA")
        assert a != b

    def test_match_key_separates_speech_acts(self):
        a = match_key("request", "p1", "sign the DPA")
        b = match_key("commitment", "p1", "sign the DPA")
        assert a != b

    def test_resolve_due_relative_weekday(self):
        # Anchored to the message that said it, not to "now".
        due, conf = resolve_due("by Friday", "2026-06-15T09:00:00-07:00")
        assert due == "2026-06-19"
        assert conf > 0

    def test_resolve_due_absolute(self):
        due, conf = resolve_due("2026-08-05", "2026-07-30T09:00:00-07:00")
        assert due == "2026-08-05"
        assert conf >= 0.9

    def test_resolve_due_none_when_absent(self):
        assert resolve_due(None, "2026-06-15T09:00:00-07:00") == (None, 0.0)

    def test_date_mentions_anchor_recorded(self):
        hits = date_mentions("let's do it tomorrow", "2026-06-15T09:00:00-07:00")
        assert hits and hits[0]["resolved"] == "2026-06-16"
        assert hits[0]["anchor_date"] == "2026-06-15"


# ── the six questions ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def db():
    if not config.DB_PATH.exists():
        pytest.skip("no built database; run `python -m src.pipeline.run all` first")
    conn = connect()
    yield conn
    conn.close()


class TestQuestions:
    def test_all_six_defined(self):
        assert len(QUESTIONS) == 6

    def test_every_question_is_valid_sql(self, db):
        user_id = db.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
        for key in QUESTIONS:
            rows = ask(db, key, user_id, limit=5)
            assert isinstance(rows, list)

    def test_questions_are_scoped_to_one_user(self, db):
        users = [r["id"] for r in db.execute("SELECT id FROM users")]
        assert len(users) > 1
        a = {r["work_item_id"] for r in ask(db, "q3_waiting_on_me", users[0], limit=50)}
        b = {r["work_item_id"] for r in ask(db, "q3_waiting_on_me", users[1], limit=50)}
        assert not (a & b), "one mailbox's items leaked into another's answer"

    def test_waiting_on_me_is_only_mine(self, db):
        user_id = db.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
        for r in ask(db, "q3_waiting_on_me", user_id, limit=50):
            assert r["owner_is_self"] == 1

    def test_waiting_on_others_is_never_mine(self, db):
        user_id = db.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
        for r in ask(db, "q4_waiting_on_others", user_id, limit=50):
            assert r["owner_is_self"] != 1

    def test_unknown_question_raises(self, db):
        with pytest.raises(KeyError):
            ask(db, "q7_does_not_exist", "x")


# ── ledger invariants on the built database ──────────────────────────


class TestLedger:
    def test_every_shipped_claim_is_quote_verified(self, db):
        bad = db.execute(
            "SELECT count(*) FROM evidence WHERE quote_verified = 0"
        ).fetchone()[0]
        assert bad == 0, "unverified evidence must never enter the ledger"

    def test_evidence_quotes_appear_in_their_source(self, db):
        """Spot-check the guard end to end against real stored rows."""
        rows = db.execute(
            "SELECT ev.evidence_quote AS q, e.body_text_full AS full, "
            "       e.body_text_novel AS novel, e.subject AS subj, e.attachments AS att "
            "FROM evidence ev JOIN emails e ON e.id = ev.source_email_id "
            "LIMIT 200"
        ).fetchall()
        assert rows
        import json as _json

        for r in rows:
            hay = [r["full"] or "", r["novel"] or "", r["subj"] or ""]
            for a in _json.loads(r["att"] or "[]"):
                if a.get("extracted_text"):
                    hay.append(a["extracted_text"])
            ok, _ = find_quote(r["q"], hay)
            assert ok, f"stored quote not found in its source: {r['q'][:70]!r}"

    def test_exactly_one_self_person_per_mailbox(self, db):
        for r in db.execute(
            "SELECT user_id, count(*) n FROM people WHERE is_self = 1 GROUP BY user_id"
        ):
            assert r["n"] == 1

    def test_impersonators_are_separate_people(self, db):
        """The Klaviyo case: fraud must not share a person row with its target."""
        rows = db.execute(
            "SELECT address, person_id FROM person_identities "
            "WHERE address IN ('billing@klaviyo.com', 'billing@klaviyo-billing.com')"
        ).fetchall()
        if len(rows) == 2:
            assert rows[0]["person_id"] != rows[1]["person_id"]

    def test_changes_precede_or_match_their_items(self, db):
        orphans = db.execute(
            "SELECT count(*) FROM work_item_changes c "
            "LEFT JOIN work_items w ON w.id = c.work_item_id WHERE w.id IS NULL"
        ).fetchone()[0]
        assert orphans == 0

    def test_open_items_have_no_resolved_at(self, db):
        bad = db.execute(
            "SELECT count(*) FROM work_items WHERE status = 'OPEN' AND resolved_at IS NOT NULL"
        ).fetchone()[0]
        assert bad == 0
