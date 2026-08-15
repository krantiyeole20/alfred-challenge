"""SQLite helpers: connection, schema init, run bookkeeping."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path=None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(config.SCHEMA_SQL.read_text())
    conn.commit()


def reset(path=None) -> None:
    """Drop the database file so a load starts clean."""
    path = path or config.DB_PATH
    for suffix in ("", "-wal", "-shm"):
        p = path.with_name(path.name + suffix)
        if p.exists():
            p.unlink()


def j(value: Any) -> str:
    """JSON-encode for a TEXT column, deterministically.

    sort_keys matters: these strings end up in prompt-cache prefixes and in
    content hashes, and unsorted dict ordering would make identical data
    serialize differently between runs.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# --------------------------------------------------------------------------- #
# Run bookkeeping
# --------------------------------------------------------------------------- #


@contextmanager
def run_record(
    conn: sqlite3.Connection, stage: str, model: str | None = None
) -> Iterator[dict]:
    """Record a pipeline stage in pipeline_runs, capturing failures.

    Yields a mutable dict the caller updates with counters; it is written back
    on exit whether the stage succeeds or raises.
    """
    run_id = new_id()
    stats = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "items_in": 0,
        "items_out": 0,
        "notes": None,
    }
    conn.execute(
        "INSERT INTO pipeline_runs (id, stage, started_at, status, model) "
        "VALUES (?, ?, ?, 'running', ?)",
        (run_id, stage, now_iso(), model),
    )
    conn.commit()
    try:
        yield stats
    except BaseException as exc:  # noqa: BLE001 - recorded then re-raised
        conn.execute(
            "UPDATE pipeline_runs SET finished_at=?, status='failed', error=?, "
            "input_tokens=?, output_tokens=?, cost_usd=?, items_in=?, items_out=? "
            "WHERE id=?",
            (
                now_iso(),
                f"{type(exc).__name__}: {exc}",
                stats["input_tokens"],
                stats["output_tokens"],
                stats["cost_usd"],
                stats["items_in"],
                stats["items_out"],
                run_id,
            ),
        )
        conn.commit()
        raise
    else:
        conn.execute(
            "UPDATE pipeline_runs SET finished_at=?, status='ok', "
            "input_tokens=?, output_tokens=?, cost_usd=?, items_in=?, items_out=?, notes=? "
            "WHERE id=?",
            (
                now_iso(),
                stats["input_tokens"],
                stats["output_tokens"],
                stats["cost_usd"],
                stats["items_in"],
                stats["items_out"],
                stats["notes"],
                run_id,
            ),
        )
        conn.commit()
