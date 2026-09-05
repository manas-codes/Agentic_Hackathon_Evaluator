"""Copy the local SQLite pilot database into Postgres.

Run once, when moving from the local pilot to a hosted deployment:

    python tools/migrate_to_postgres.py                 # dry run, shows counts
    python tools/migrate_to_postgres.py --apply         # copies
    python tools/migrate_to_postgres.py --apply --replace   # wipes target first

Two details that a naive copy gets wrong, and that matter here:

1. **Insert order.** Evidence rows reference criterion scores, which reference
   evaluations, which reference submissions. `Base.metadata.sorted_tables` is
   already in foreign-key dependency order, so it is used rather than any
   hand-maintained list - a table added later is picked up automatically.

2. **Sequences.** SQLite hands out integer primary keys itself; copying those
   ids into Postgres leaves every sequence sitting at 1, and the next insert
   through the portal collides on the primary key. Every sequence is advanced
   past the highest copied id at the end. Skipping this step produces a
   database that reads correctly and fails on the first write - which, for
   this application, means the first time an evaluator approves something.

The SQLite file is only read. Nothing here writes to it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import create_engine, func, insert, select, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from cag101.config import load_config  # noqa: E402
from cag101.db import database_url  # noqa: E402
from cag101.models import Base  # noqa: E402


def sqlite_engine() -> Engine:
    path = load_config().path("paths.database")
    if not path.exists():
        raise SystemExit(f"No local database at {path} - nothing to migrate.")
    return create_engine(f"sqlite:///{path}", future=True)


def target_engine() -> Engine:
    url = database_url()
    if url.startswith("sqlite"):
        raise SystemExit(
            "DATABASE_URL is not set to a Postgres URL.\n"
            "Put it in .env, for example:\n"
            "  DATABASE_URL=postgresql://user:pass@host/db?sslmode=require"
        )
    return create_engine(url, future=True, pool_pre_ping=True)


def counts(engine: Engine) -> dict[str, int]:
    out: dict[str, int] = {}
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            try:
                out[table.name] = conn.execute(
                    select(func.count()).select_from(table)
                ).scalar_one()
            except Exception:
                out[table.name] = -1          # table absent on the target yet
    return out


def reset_sequences(engine: Engine) -> list[str]:
    """Advance each identity sequence past the highest copied primary key."""
    done: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            pk = list(table.primary_key.columns)
            if len(pk) != 1 or not pk[0].autoincrement:
                continue
            col = pk[0].name
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, :c)"),
                {"t": table.name, "c": col},
            ).scalar()
            if not seq:
                continue
            highest = conn.execute(
                text(f'SELECT COALESCE(MAX("{col}"), 0) FROM "{table.name}"')
            ).scalar_one()
            # is_called=true means the NEXT value is highest+1
            conn.execute(
                text("SELECT setval(:s, :v, true)"),
                {"s": seq, "v": max(int(highest), 1)},
            )
            done.append(f"{table.name}.{col} -> {highest}")
    return done


def migrate(apply: bool, replace: bool) -> int:
    src, dst = sqlite_engine(), target_engine()

    print(f"source : {src.url}")
    print(f"target : {dst.url.render_as_string(hide_password=True)}\n")

    Base.metadata.create_all(dst)

    before = counts(dst)
    populated = {t: n for t, n in before.items() if n > 0}
    if populated and not replace:
        print("The target already holds rows:")
        for t, n in sorted(populated.items()):
            print(f"  {t:<24} {n}")
        print(
            "\nRefusing to copy into a populated database - the result would be "
            "duplicated rows or primary-key collisions.\nUse --replace to wipe "
            "the target first, or point DATABASE_URL at an empty database."
        )
        return 1

    src_counts = counts(src)
    total = sum(n for n in src_counts.values() if n > 0)
    print(f"{'table':<24}{'rows':>8}")
    for t, n in src_counts.items():
        if n:
            print(f"  {t:<22}{n:>8}")
    print(f"  {'TOTAL':<22}{total:>8}\n")

    if not apply:
        print("Dry run. Re-run with --apply to copy.")
        return 0

    if replace and populated:
        with dst.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())
        print("Target emptied.\n")

    copied = 0
    with src.connect() as s_conn, dst.begin() as d_conn:
        for table in Base.metadata.sorted_tables:      # FK dependency order
            rows = [dict(r) for r in s_conn.execute(select(table)).mappings()]
            if not rows:
                continue
            # chunked: one 5,000-row statement is fine, 400 submissions of
            # evidence rows in a single statement is not
            for i in range(0, len(rows), 500):
                d_conn.execute(insert(table), rows[i : i + 500])
            copied += len(rows)
            print(f"  copied {len(rows):>6}  {table.name}")

    print(f"\n{copied} rows copied. Advancing sequences:")
    for line in reset_sequences(dst):
        print(f"  {line}")

    after = counts(dst)
    mismatch = [
        (t, src_counts.get(t, 0), after.get(t, 0))
        for t in src_counts
        if src_counts.get(t, 0) != after.get(t, 0)
    ]
    if mismatch:
        print("\nROW COUNTS DO NOT MATCH:")
        for t, a, b in mismatch:
            print(f"  {t}: source {a}, target {b}")
        return 1

    print("\nRow counts match on every table. Migration complete.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Actually copy the rows")
    ap.add_argument(
        "--replace", action="store_true",
        help="Delete everything in the target first (destructive)",
    )
    args = ap.parse_args()
    raise SystemExit(migrate(args.apply, args.replace))
