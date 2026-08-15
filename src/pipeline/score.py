"""Stage 5: score pipeline output against the gold sets.

Matching is by message id, not by text similarity. A gold item names the
messages that attest it; a pipeline answer traces back through evidence to the
emails it was extracted from. An answer counts as covering a gold item when
those sets intersect. That keeps scoring honest -- no LLM judge, no fuzzy
string match that could be tuned until the numbers look good.

Reports three things per question:
    recall     gold must_include items the pipeline surfaced
    precision  answers that map to a gold item rather than a distractor
    leakage    answers that hit the gold set's must_not_include list
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from . import config
from .db import connect, run_record
from .questions import QUESTIONS, ask

TOP_N = 25


def _load_gold() -> dict:
    return json.loads((config.DATA / "gold_set.json").read_text())


def _messages_for_items(conn: sqlite3.Connection, user_id: str) -> dict[str, set[str]]:
    """work_item_id -> every provider_message_id that fed it."""
    out: dict[str, set[str]] = defaultdict(set)
    for r in conn.execute(
        "SELECT ev.work_item_id AS wid, em.provider_message_id AS pm "
        "FROM evidence ev JOIN emails em ON em.id = ev.source_email_id "
        "WHERE ev.work_item_id IS NOT NULL AND ev.user_id = ?",
        (user_id,),
    ):
        out[r["wid"]].add(r["pm"])
    # The anchor email counts even if its evidence was later superseded.
    for r in conn.execute(
        "SELECT wi.id AS wid, em.provider_message_id AS pm "
        "FROM work_items wi JOIN emails em ON em.id = wi.first_seen_email_id "
        "WHERE wi.user_id = ?",
        (user_id,),
    ):
        out[r["wid"]].add(r["pm"])
    return out


def _gold_message_ids(item: dict) -> set[str]:
    ids = set(item.get("message_ids") or [])
    if item.get("anchor_message_id"):
        ids.add(item["anchor_message_id"])
    return ids


def score_profile(conn, gold_mb: dict, user_id: str) -> dict:
    item_messages = _messages_for_items(conn, user_id)
    report: dict[str, dict] = {}

    for question, gold_answer in gold_mb["answers"].items():
        if question not in QUESTIONS:
            continue

        answers = ask(conn, question, user_id, limit=TOP_N)
        answer_msgs = [
            (a["work_item_id"], item_messages.get(a["work_item_id"], set()))
            for a in answers
        ]

        gold_items = [
            g for g in gold_answer.get("items", [])
            if g.get("tier", "must_include") == "must_include"
        ]
        must_not = gold_answer.get("must_not_include") or []
        if isinstance(must_not, int):  # some questions record only a count
            must_not = []

        covered, missed = [], []
        matched_answer_ids: set[str] = set()
        for g in gold_items:
            gmsgs = _gold_message_ids(g)
            hit = next((wid for wid, msgs in answer_msgs if msgs & gmsgs), None)
            if hit:
                covered.append(g.get("item_id"))
                matched_answer_ids.add(hit)
            else:
                missed.append(
                    {
                        "item_id": g.get("item_id"),
                        "claim": (g.get("claim") or "")[:120],
                        "tricky_tags": g.get("tricky_tags") or [],
                    }
                )

        leaked = []
        for bad in must_not:
            bmsgs = _gold_message_ids(bad) if isinstance(bad, dict) else set()
            if not bmsgs:
                continue
            hit = next((wid for wid, msgs in answer_msgs if msgs & bmsgs), None)
            if hit:
                leaked.append(bad.get("item_id") or bad.get("claim", "")[:60])

        n_gold = len(gold_items)
        recall = len(covered) / n_gold if n_gold else None
        precision = len(matched_answer_ids) / len(answers) if answers else None

        report[question] = {
            "gold_items": n_gold,
            "answers_returned": len(answers),
            "covered": len(covered),
            "recall": recall,
            "precision": precision,
            "leaked": leaked,
            "missed": missed,
        }
    return report


def main() -> None:
    conn = connect()
    gold = _load_gold()
    users = {
        r["role_profile"]: r["id"]
        for r in conn.execute("SELECT id, role_profile FROM users")
    }
    # map profile ids to user rows via accounts, which carry the profile id
    profile_users = {
        r["provider_account_id"]: r["user_id"]
        for r in conn.execute("SELECT provider_account_id, user_id FROM accounts")
    }

    with run_record(conn, "score") as stats:
        overall = defaultdict(lambda: {"gold": 0, "covered": 0, "answers": 0, "matched": 0})
        tag_misses: dict[str, int] = defaultdict(int)
        all_reports = {}

        for profile_id in config.PROFILE_IDS:
            user_id = profile_users.get(profile_id)
            if not user_id or profile_id not in gold["mailboxes"]:
                continue
            rep = score_profile(conn, gold["mailboxes"][profile_id], user_id)
            all_reports[profile_id] = rep
            for question, r in rep.items():
                overall[question]["gold"] += r["gold_items"]
                overall[question]["covered"] += r["covered"]
                overall[question]["answers"] += r["answers_returned"]
                for miss in r["missed"]:
                    for tag in miss["tricky_tags"]:
                        tag_misses[tag] += 1

        stats["items_in"] = sum(v["gold"] for v in overall.values())
        stats["items_out"] = sum(v["covered"] for v in overall.values())

        print(f"\n{'question':30} {'gold':>5} {'found':>6} {'recall':>8}")
        print("-" * 52)
        for question in QUESTIONS:
            v = overall.get(question)
            if not v or not v["gold"]:
                continue
            rec = v["covered"] / v["gold"]
            print(f"{question:30} {v['gold']:5} {v['covered']:6} {rec:7.1%}")

        total_gold = sum(v["gold"] for v in overall.values())
        total_cov = sum(v["covered"] for v in overall.values())
        if total_gold:
            print("-" * 52)
            print(f"{'TOTAL':30} {total_gold:5} {total_cov:6} {total_cov / total_gold:7.1%}")

        if tag_misses:
            print("\nmisses by adversarial tag (where the cheap model breaks):")
            for tag, n in sorted(tag_misses.items(), key=lambda kv: -kv[1]):
                print(f"  {tag:32} {n}")

        out = config.RESULTS / "score_report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(all_reports, indent=2))
        print(f"\nfull report -> {out}")

    conn.close()


if __name__ == "__main__":
    main()
