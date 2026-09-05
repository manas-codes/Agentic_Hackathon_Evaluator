"""Copy the pilot SQLite database into Postgres.

Used once, when the portal moves off a laptop. The schema is created from the
same SQLAlchemy models, so nothing is hand-written and nothing can drift.

Two things this deliberately does NOT do:

  * It does not delete or alter the SQLite file. That file stays the record of
    the pilot until someone is satisfied the copy is good.
  * It does not run unless the target is empty, so it cannot silently overwrite
    a database that a committee has already been working in. Pass --replace to
    override, which drops every table first and says so.

Usage:
    set DATABASE_URL=postgresql+psycopg://user:pass@host/dbname
    python tools/migrate_to_postgres.py            # copy
    python tools/migrate_to_postgres.py --check    # count rows on both sides
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from cag101.config import load_config  # noqa: E402
from cag101.models import Base  # noqa: E402

# Parents before children: every foreign key must already resolve when a row
# lands. Ordering is explicit rather than derived, so a new model that is not
# added here fails loudly in the completeness check below.
ORDER = [
    "users",
    "submissions",
    "files",
    "canonical_records",
    "restricted_identities",
    "flags",
    "runs",
    "evaluations",
    "criterion_scores",
    "evidence",
    "overrides",
    "review_decisions",
    "clusters",
    "cluster_members",
    "audit_log",
    "jobs",
]


def _source_url() -> str:
    cfg = load_config()
    raw = str(cfg.require("paths.database"))
    if "://" in raw:
        raise SystemExit(
            "paths.database is already a URL. This tool copies the local SQLite "
            "pilot file into Postgres; point paths.database back at data/app.db "
            "or pass the file another way."
        )
    return f"sqlite:///{(Path(cfg.root) / raw).resolve()}"


def _target_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit(
            "DATABASE_URL is not set. Example:\n"
            "  set DATABASE_URL=postgresql+psycopg://user:pass@host/dbname"
        )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if not url.startswith("postgresql+psycopg://"):
        raise SystemExit(f"DATABASE_URL is not a Postgres URL: {url.split('@')[0]}...")
    return url


def _tables_covered() -> None:
    known = {t.name for t in Base.metadata.sorted_tables}
    missing = known - set(ORDER)
    unknown = set(ORDER) - known
    if missing:
        raise SystemExit(
            "These tables exist in the models but are not in ORDER, so they "
            f"would be silently skipped: {sorted(missing)}"
        )
    if unknown:
        raise SystemExit(f"ORDER names tables that do not exist: {sorted(unknown)}")


def counts(engine) -> dict[str, int]:
    out: dict[str, int] = {}
    with Session(engine) as s:
        for table in Base.metadata.sorted_tables:
            out[table.name] = s.scalar(select(func.count()).select_from(table)) or 0
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="Compare row counts only")
    ap.add_argument(
        "--replace", action="store_true",
        help="Drop every table in the target first (destructive)",
    )
    args = ap.parse_args()

    _tables_covered()
    src = create_engine(_source_url(), future=True)
    dst = create_engine(_target_url(), future=True)
    print(f"source: {src.url}\ntarget: {dst.url.render_as_string(hide_password=True)}\n")

    if args.check:
        a, b = counts(src), counts(dst)
        width = max(len(t) for t in a)
        bad = 0
        for table in ORDER:
            same = a[table] == b[table]
            bad += 0 if same else 1
            print(f"  {table:<{width}}  sqlite {a[table]:>6}   postgres {b[table]:>6}"
                  f"   {'ok' if same else 'MISMATCH'}")
        print("\nidentical" if not bad else f"\n{bad} table(s) differ")
        return 0 if not bad else 1

    if args.replace:
        print("--replace: dropping every table in the target first.")
        Base.metadata.drop_all(dst)

    Base.metadata.create_all(dst)

    existing = counts(dst)
    if any(existing.values()):
        populated = {t: n for t, n in existing.items() if n}
        raise SystemExit(
            "The target already holds data and would be added to, not replaced:\n"
            f"  {populated}\n"
            "Re-run with --replace if that is what you want."
        )

    moved = 0
    with Session(src) as s_in, Session(dst) as s_out:
        for name in ORDER:
            table = Base.metadata.tables[name]
            rows = [dict(r._mapping) for r in s_in.execute(select(table))]
            if rows:
                s_out.execute(table.insert(), rows)
            moved += len(rows)
            print(f"  {name:<22} {len(rows):>6} row(s)")
        s_out.commit()

    # Postgres sequences do not advance when explicit ids are inserted, so the
    # next insert would collide on the primary key. Reset each one to the
    # highest id actually present.
    with Session(dst) as s_out:
        for name in ORDER:
            table = Base.metadata.tables[name]
            pk = list(table.primary_key.columns)
            if len(pk) != 1 or not pk[0].autoincrement:
                continue
            col = pk[0].name
            s_out.execute(
                func.setval(
                    func.pg_get_serial_sequence(name, col),
                    select(func.coalesce(func.max(table.c[col]), 1)).scalar_subquery(),
                )
            )
        s_out.commit()
    print(f"\nCopied {moved} row(s). Sequences reset.")
    print("Verify with:  python tools/migrate_to_postgres.py --check")
    print("The SQLite file is untouched — keep it until the copy is confirmed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
