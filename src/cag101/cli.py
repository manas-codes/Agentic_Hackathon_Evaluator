"""Command line interface.

    python cag.py init-db
    python cag.py ingest [--force]
    python cag.py status
    python cag.py map [--refs CAG101-0001 ...] [--force]
    python cag.py score [--refs ...] [--limit N] [--dry-run] [--force]
    python cag.py cluster
    python cag.py export [--out FILE]
    python cag.py users add <username> --role evaluator
    python cag.py serve
    python cag.py make-samples [--count 3]
    python cag.py check-rubric [--slug cag101_v1]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


# --- helpers ---------------------------------------------------------------
def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    if not rows:
        print("(nothing to show)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))


# --- commands --------------------------------------------------------------
def cmd_init_db(_args: argparse.Namespace) -> int:
    from .config import ensure_directories
    from .db import database_url, init_db

    ensure_directories()
    init_db()
    print(f"Database ready: {database_url()}")
    return 0


def cmd_check_rubric(args: argparse.Namespace) -> int:
    from .rubric import available_rubrics, load_rubric

    slugs = [args.slug] if args.slug else available_rubrics()
    ok = True
    for slug in slugs:
        try:
            r = load_rubric(slug)
        except Exception as exc:
            ok = False
            print(f"FAIL  {slug}\n      {exc}")
            continue
        print(f"OK    {slug}  '{r.name}'  total={r.total}  hash={r.content_hash}")
        for c in r.criteria:
            subs = ", ".join(f"{s.key}={s.max}" for s in c.sub_criteria)
            print(f"        {c.name} ({c.weight} pts): {subs}")
    return 0 if ok else 1


def cmd_ingest(args: argparse.Namespace) -> int:
    from .config import load_config
    from .db import init_db, session_scope
    from .ingest import ingest_all

    init_db()
    base = load_config().path("paths.submissions")
    print(f"Scanning {base}")
    with session_scope() as session:
        results, empty_folders = ingest_all(session, force=args.force)

    if not results:
        print(
            "\nNo submissions found.\n"
            f"Put one folder per submission inside:\n  {base}\n"
            "Each folder should contain the filled Appendix-I form and any "
            "deck, video or supporting document that came with it."
        )
        return 1

    print()
    for r in results:
        print(r.line())

    if empty_folders:
        print(
            f"\n{len(empty_folders)} submission folder(s) contain no files at all. "
            "They are recorded and flagged for PPG follow-up, but cannot be "
            "evaluated until material is supplied:"
        )
        for name in empty_folders:
            print(f"  - {name}")

    warned = [r for r in results if r.warnings]
    print(
        f"\n{len(results)} submission(s): "
        f"{sum(1 for r in results if r.status == 'ingested')} ingested, "
        f"{sum(1 for r in results if r.status == 'unchanged')} unchanged, "
        f"{sum(1 for r in results if r.status == 'empty')} with no extractable text."
    )
    if warned:
        print(f"\n{len(warned)} submission(s) have extraction warnings:")
        for r in warned:
            for w in r.warnings:
                print(f"  {r.ref}: {w}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import Evaluation, Submission

    init_db()
    with session_scope() as session:
        subs = list(session.scalars(select(Submission).order_by(Submission.ref)))
        if not subs:
            print("No submissions ingested yet. Run: python cag.py ingest")
            return 0
        rows = []
        for s in subs:
            final = session.scalar(
                select(Evaluation).where(
                    Evaluation.submission_id == s.id, Evaluation.is_final.is_(True)
                )
            )
            rows.append([
                s.ref,
                s.status,
                s.thematic_area,
                s.formation_category,
                f"{final.total:.1f}" if final else "-",
                (s.title or s.folder_name)[:60],
            ])
        _print_table(
            rows,
            ["Ref", "Status", "Theme", "Formation", "Score", "Title / folder"],
        )
        print(f"\nTotal {len(subs)} submission(s).")
    return 0


def cmd_map(args: argparse.Namespace) -> int:
    from .formmap import map_submissions

    return map_submissions(refs=args.refs, force=args.force)


def cmd_score(args: argparse.Namespace) -> int:
    from .pipeline import run_scoring

    return run_scoring(
        refs=args.refs, limit=args.limit,
        dry_run=args.dry_run, force=args.force,
    )


def cmd_cluster(_args: argparse.Namespace) -> int:
    from .cluster import build_idea_library

    return build_idea_library()


def cmd_export(args: argparse.Namespace) -> int:
    from .export_excel import export_workbook

    path = export_workbook(out_path=Path(args.out) if args.out else None)
    print(f"Written: {path}")
    return 0


def cmd_users(args: argparse.Namespace) -> int:
    from .auth import add_user, list_users

    if args.user_action == "add":
        password = add_user(args.username, role=args.role, full_name=args.full_name or "")
        print(
            f"User '{args.username}' created with role '{args.role}'.\n"
            f"Temporary password: {password}\n"
            "The user must change it at first login. Share it over a channel "
            "separate from the portal link."
        )
        return 0
    if args.user_action == "list":
        rows = [
            [u.username, u.role, "active" if u.is_active else "disabled",
             u.full_name, u.last_login_at.strftime("%d-%m-%Y %H:%M") if u.last_login_at else "never"]
            for u in list_users()
        ]
        _print_table(rows, ["Username", "Role", "State", "Name", "Last login"])
        return 0
    print("Unknown user action.")
    return 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .config import load_config

    cfg = load_config()
    host = args.host or cfg.get("portal.host", "127.0.0.1")
    port = args.port or int(cfg.get("portal.port", 8000))
    print(f"Portal starting on http://{host}:{port}")
    uvicorn.run("cag101.web.app:app", host=host, port=port, reload=args.reload)
    return 0


def cmd_make_samples(args: argparse.Namespace) -> int:
    from .samples import make_samples

    paths = make_samples(count=args.count)
    print(f"Created {len(paths)} synthetic submission folder(s):")
    for p in paths:
        print(f"  {p}")
    print(
        "\nThese are fabricated test fixtures for verifying the pipeline. "
        "They are clearly marked as synthetic and must not be mixed with real "
        "submissions — delete them before the real run."
    )
    return 0


# --- parser ----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cag",
        description="CAG 101 Innovation Ideas — evaluation framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create the database schema").set_defaults(
        func=cmd_init_db
    )

    p = sub.add_parser("check-rubric", help="Validate rubric YAML files")
    p.add_argument("--slug", help="Rubric slug, e.g. cag101_v1")
    p.set_defaults(func=cmd_check_rubric)

    p = sub.add_parser("ingest", help="Scan the submissions folder and extract text")
    p.add_argument("--force", action="store_true", help="Re-extract even if unchanged")
    p.set_defaults(func=cmd_ingest)

    sub.add_parser("status", help="Show submissions and their state").set_defaults(
        func=cmd_status
    )

    p = sub.add_parser("map", help="Map extracted text onto the Appendix-I schema")
    p.add_argument("--refs", nargs="*", help="Limit to these submission refs")
    p.add_argument("--force", action="store_true", help="Re-map already-mapped submissions")
    p.set_defaults(func=cmd_map)

    p = sub.add_parser("score", help="Run the scoring agents")
    p.add_argument("--refs", nargs="*", help="Limit to these submission refs")
    p.add_argument("--limit", type=int, help="Score at most N submissions")
    p.add_argument(
        "--force", action="store_true",
        help="Re-score submissions already scored against this rubric",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate tokens and cost without calling the model",
    )
    p.set_defaults(func=cmd_score)

    sub.add_parser("cluster", help="Build the similar-idea library").set_defaults(
        func=cmd_cluster
    )

    p = sub.add_parser("export", help="Write the Excel scorecard workbook")
    p.add_argument("--out", help="Output path")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("users", help="Manage portal users")
    usub = p.add_subparsers(dest="user_action", required=True)
    ua = usub.add_parser("add")
    ua.add_argument("username")
    ua.add_argument("--role", default="viewer", choices=["admin", "evaluator", "viewer"])
    ua.add_argument("--full-name", dest="full_name")
    usub.add_parser("list")
    p.set_defaults(func=cmd_users)

    p = sub.add_parser("serve", help="Run the portal")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("make-samples", help="Create synthetic test submissions")
    p.add_argument("--count", type=int, default=3)
    p.set_defaults(func=cmd_make_samples)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except (FileNotFoundError, RuntimeError, KeyError, ValueError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
