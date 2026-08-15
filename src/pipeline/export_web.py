"""Stage 6: build the read-only SQLite file the browser downloads.

The pipeline database carries operational tables the demo has no use for. This
copies out the read path only, drops columns nobody queries, and rebuilds the
indexes the six questions actually hit.

The result is the entire backend: the page fetches this file once, runs it in
WASM, and every tool call the agent makes is a local query. Nothing about a
static demo needs a database server for 1,500 read-only emails.
"""

from __future__ import annotations

import gzip
import re
import shutil
import sqlite3
from pathlib import Path

from . import config
from .db import connect

WEB_DB = config.ROOT / "web" / "docs" / "demo" / "data" / "alfred.db"

# Read path only. Build-time tables (dead_letter_queue, backfill_jobs,
# processing_state, sweeper_runs, ingestion_events, eval_*) are deliberately
# absent: they exist to operate a live pipeline, and there isn't one here.
SHIPPED = [
    "users", "accounts", "people", "person_identities", "identity_conflicts",
    "threads", "emails", "email_participants", "email_signals",
    "attribute_vocab", "evidence", "work_items", "work_item_threads",
    "work_item_changes", "attention_candidates",
]

# Indexes the six questions in questions.py actually use. Rebuilt after the
# copy so the shipped file is laid out for reads rather than for writes.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_emails_user_ts ON emails (user_id, provider_ts DESC)",
    "CREATE INDEX IF NOT EXISTS ix_emails_thread ON emails (thread_id, provider_ts)",
    "CREATE INDEX IF NOT EXISTS ix_emails_pm ON emails (provider_message_id)",
    "CREATE INDEX IF NOT EXISTS ix_parts_email ON email_participants (email_id)",
    "CREATE INDEX IF NOT EXISTS ix_parts_person ON email_participants (person_id)",
    "CREATE INDEX IF NOT EXISTS ix_ev_workitem ON evidence (work_item_id)",
    "CREATE INDEX IF NOT EXISTS ix_ev_source ON evidence (source_email_id)",
    "CREATE INDEX IF NOT EXISTS ix_wi_user_status ON work_items (user_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_wi_owner ON work_items (user_id, owner_is_self, status)",
    "CREATE INDEX IF NOT EXISTS ix_att_score ON attention_candidates (user_id, score DESC)",
    "CREATE INDEX IF NOT EXISTS ix_chg_user_time ON work_item_changes (user_id, changed_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_thr_user ON threads (user_id, last_message_at DESC)",
]


# Foreign keys are dropped in the shipped copy: it is read-only, the
# constraints were already enforced upstream, and they only slow the WASM load.
# Commenting them out with `--` would also comment out the trailing comma and
# break the DDL, so the clause is removed outright.
_FK = re.compile(
    r"\s+REFERENCES\s+\w+\s*\([^)]*\)(\s+ON\s+DELETE\s+\w+)?(\s+ON\s+UPDATE\s+\w+)?",
    re.I,
)


def _strip_fks(ddl: str) -> str:
    return _FK.sub("", ddl)


def build(dest: Path = WEB_DB, compress: bool = True) -> Path:
    src = connect()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    out = sqlite3.connect(dest)
    out.execute("PRAGMA journal_mode = OFF")
    out.execute("PRAGMA synchronous = OFF")

    src_tables = {
        r["name"] for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    for table in SHIPPED:
        if table not in src_tables:
            print(f"  skip {table} (not in source)")
            continue
        ddl = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        out.execute(_strip_fks(ddl))

        cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
        rows = src.execute(f"SELECT {','.join(cols)} FROM {table}").fetchall()
        if rows:
            out.executemany(
                f"INSERT INTO {table} ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [tuple(r) for r in rows],
            )
        print(f"  {table:22} {len(rows):>6} rows")

    for stmt in INDEXES:
        out.execute(stmt)

    # Full-text search over mail, so the demo's search box is a real index
    # rather than a LIKE scan across 1,500 bodies.
    out.execute(
        "CREATE VIRTUAL TABLE email_fts USING fts5("
        "  subject, body, sender, email_id UNINDEXED, tokenize='porter unicode61')"
    )
    out.execute(
        "INSERT INTO email_fts (subject, body, sender, email_id) "
        "SELECT e.subject, e.body_text_novel, "
        "  COALESCE((SELECT raw_name || ' ' || raw_address FROM email_participants p "
        "            WHERE p.email_id = e.id AND p.role = 'from' LIMIT 1), ''), "
        "  e.id FROM emails e"
    )

    out.commit()
    out.execute("VACUUM")
    out.commit()
    out.close()
    src.close()

    size_mb = dest.stat().st_size / 1e6
    print(f"\n  {dest.relative_to(config.ROOT)}  {size_mb:.1f} MB")

    if compress:
        gz = dest.with_suffix(".db.gz")
        with open(dest, "rb") as f_in, gzip.open(gz, "wb", compresslevel=9) as f_out:
            shutil.copyfileobj(f_in, f_out)
        gz_mb = gz.stat().st_size / 1e6
        print(f"  {gz.relative_to(config.ROOT)}  {gz_mb:.1f} MB  (what the browser downloads)")

    return dest


def export_questions(dest: Path | None = None) -> Path:
    """Emit the six questions as JSON for the frontend.

    questions.py stays the single source of truth: the scorer and the demo
    agent run byte-identical SQL. Hand-copying these into JavaScript is how
    the thing being scored quietly stops being the thing being demoed.
    """
    import json

    from .questions import QUESTIONS

    dest = dest or (config.ROOT / "web" / "docs" / "demo" / "data" / "questions.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        key: {
            "label": q["label"],
            "why": q["why"],
            "sql": q["sql"],
            # The designed job behind the question, so the demo can show how an
            # answer was produced rather than just asserting it.
            "trigger": q.get("trigger"),
            "reads": q.get("reads", []),
            "pipeline": q.get("pipeline", []),
        }
        for key, q in QUESTIONS.items()
    }
    dest.write_text(json.dumps(payload, indent=2))
    print(f"  {dest.relative_to(config.ROOT)}  {len(payload)} questions")
    return dest


def main() -> None:
    print("exporting read path to browser SQLite:")
    build()
    export_questions()


if __name__ == "__main__":
    main()
