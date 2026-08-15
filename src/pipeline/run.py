"""Pipeline CLI.

    python -m src.pipeline.run list-models     confirm model ids on your account
    python -m src.pipeline.run load            corpus  -> base tables
    python -m src.pipeline.run signals         deterministic signals, no LLM
    python -m src.pipeline.run extract         emails  -> evidence   (costs money)
    python -m src.pipeline.run reduce          evidence -> work items
    python -m src.pipeline.run score           output vs the gold sets
    python -m src.pipeline.run all             everything, in order
    python -m src.pipeline.run status          what is in the database now

Every stage is idempotent and independently re-runnable. `extract` is the only
one that spends money; it aborts at ALFRED_BUDGET_USD.
"""

from __future__ import annotations

import argparse
import sys

from . import config


def cmd_list_models(args) -> None:
    from .llm import LLM

    llm = LLM()
    models = llm.list_models()
    gemini = [m for m in models if "gemini" in m.lower()]
    print(f"{len(models)} models available on this account.\n")
    print("Gemini models:")
    for m in gemini:
        marks = []
        if m == config.PRIMARY_MODEL:
            marks.append("PRIMARY")
        if m == config.FALLBACK_MODEL:
            marks.append("FALLBACK")
        print(f"  {m}" + (f"   <- {', '.join(marks)}" if marks else ""))
    print(f"\nconfigured primary : {config.PRIMARY_MODEL}")
    print(f"configured fallback: {config.FALLBACK_MODEL}")
    for label, want in (("primary", config.PRIMARY_MODEL), ("fallback", config.FALLBACK_MODEL)):
        if want not in models:
            print(
                f"\n  {want!r} ({label}) is NOT on this account. "
                "Set GEMINI_MODEL / GEMINI_FALLBACK_MODEL in .env to ids above."
            )


def cmd_load(args) -> None:
    from .load_corpus import main as load_main

    load_main(reset_db=not args.keep)


def cmd_signals(args) -> None:
    from .signals import main as signals_main

    signals_main()


def cmd_extract(args) -> None:
    from .extract import main as extract_main

    extract_main(
        limit=args.limit,
        profile=getattr(args, "profile", None),
        redo=getattr(args, "redo", False),
    )


def cmd_reduce(args) -> None:
    from .reduce import main as reduce_main

    reduce_main()


def cmd_score(args) -> None:
    from .score import main as score_main

    score_main()


def cmd_all(args) -> None:
    cmd_load(args)
    cmd_signals(args)
    cmd_extract(args)
    cmd_reduce(args)
    cmd_score(args)


def cmd_status(args) -> None:
    from .db import connect

    conn = connect()
    tables = [
        "users", "people", "person_identities", "identity_conflicts", "threads",
        "emails", "email_participants", "email_signals", "evidence",
        "evidence_quarantine", "work_items", "work_item_changes",
        "attention_candidates",
    ]
    print(f"database: {config.DB_PATH}\n")
    for t in tables:
        try:
            n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        except Exception:
            n = "-"
        print(f"  {t:22} {n:>7}")
    print("\nruns:")
    for r in conn.execute(
        "SELECT stage, status, items_in, items_out, cost_usd, started_at, notes "
        "FROM pipeline_runs ORDER BY started_at"
    ):
        note = f"  {r['notes']}" if r["notes"] else ""
        print(
            f"  {r['stage']:9} {r['status']:7} in={r['items_in']:<6} "
            f"out={r['items_out']:<6} ${r['cost_usd']:.3f}{note}"
        )
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="run", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-models").set_defaults(fn=cmd_list_models)

    p_load = sub.add_parser("load")
    p_load.add_argument("--keep", action="store_true", help="do not drop the existing db")
    p_load.set_defaults(fn=cmd_load)

    sub.add_parser("signals").set_defaults(fn=cmd_signals)

    p_ex = sub.add_parser("extract")
    p_ex.add_argument("--limit", type=int, help="extract only the first N emails")
    p_ex.add_argument(
        "--profile", choices=config.PROFILE_IDS, help="extract one mailbox only"
    )
    p_ex.add_argument(
        "--redo", action="store_true",
        help="re-extract emails that already have evidence (default: resume)",
    )
    p_ex.set_defaults(fn=cmd_extract)

    sub.add_parser("reduce").set_defaults(fn=cmd_reduce)
    sub.add_parser("score").set_defaults(fn=cmd_score)
    sub.add_parser("status").set_defaults(fn=cmd_status)

    p_all = sub.add_parser("all")
    p_all.add_argument("--keep", action="store_true")
    p_all.add_argument("--limit", type=int)
    p_all.set_defaults(fn=cmd_all)

    args = parser.parse_args()
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
