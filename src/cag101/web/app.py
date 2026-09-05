"""The evaluation portal.

Two tabs, as specified: an overview of the whole intake, and the scorecards.
Plus what a portal used by committees actually needs — login with roles, human
override with recorded justification, and export of whatever the current filter
shows.

Access rules:
  viewer     read scorecards and export; office and submitter identity hidden
  evaluator  as viewer, plus identity visible and score override permitted
  admin      as evaluator, plus user management
"""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import (
    LoginError,
    authenticate,
    bootstrap_admin,
    get_user,
    has_role,
    log_action,
    set_password,
    verify_password,
)
from ..config import load_config, session_secret
from ..db import init_db, session_scope
from ..models import Evaluation, Override, ReviewDecision, Submission, User
from ..reporting import (
    ARC_C,
    ARC_R,
    CRITERION_HUE,
    CRITERION_SHORT,
    DONUT_C,
    DONUT_R,
    QUALIFY_AT,
    FORMATION_LABELS,
    STAGE_LABELS,
    THEME_LABELS,
    ScorecardView,
    all_scorecards,
    as_bullets,
    build_overview,
    build_scorecard,
    PUBLIC_EVALUATOR_LABEL,
    public_flags,
    rank_scorecards,
)
from ..rubric import load_rubric

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
# A rationale reads as bullets on screen and as prose in the record; the split
# happens at render time so nothing is rewritten on the way into the database.
templates.env.filters["bullets"] = as_bullets
templates.env.globals["public_flags"] = public_flags


# Timestamps are stored in UTC, which is right for a record and wrong for a
# screen: a decision taken at 02:00 IST would otherwise be dated the previous
# day. Everything on screen is shown in IST, in DD-MM-YYYY.
IST = timezone(timedelta(hours=5, minutes=30))


def in_ist(value: datetime | None, fmt: str = "%d-%m-%Y") -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).strftime(fmt)


templates.env.filters["ist"] = in_ist

app = FastAPI(title="CAG 101 Innovation Ideas — Evaluation Portal", docs_url=None, redoc_url=None)
# Behind a TLS-terminating platform the app itself sees plain HTTP, but the
# browser is talking HTTPS, so the Secure cookie flag is correct there and only
# there. Local development over http://127.0.0.1 must not set it or the session
# cookie is never returned.
_SECURE_COOKIES = bool(
    os.environ.get("VERCEL")
    or os.environ.get("FORCE_HTTPS", "").strip().lower() in {"1", "true", "yes"}
)

app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret(),
    max_age=int(load_config().get("portal.session_hours", 8)) * 3600,
    same_site="lax",
    https_only=_SECURE_COOKIES,
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
def current_user(request: Request) -> User | None:
    """Resolve the signed-in user, re-checking that the account is still active.

    The active check has to happen on every request, not just at login: an
    account disabled by an administrator must lose access immediately, not when
    its session cookie happens to expire.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = get_user(int(user_id))
    if user is None or not user.is_active:
        request.session.clear()
        return None
    return user


def require_user(request: Request) -> User:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="login required")
    return user


def require_role(request: Request, role: str) -> User:
    user = require_user(request)
    if not has_role(user.role, role):
        raise HTTPException(
            status_code=403,
            detail=f"This action needs the '{role}' role. You have '{user.role}'.",
        )
    return user


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def asset_version() -> str:
    """Cache-busting token derived from the stylesheet's modification time.

    Without this, a CSS change silently does nothing for anyone whose browser
    has the old file cached — including, in a committee setting, everyone who
    used the portal yesterday.
    """
    try:
        return str(int((HERE / "static" / "style.css").stat().st_mtime))
    except OSError:
        return "0"


def base_context(request: Request, user: User | None) -> dict[str, Any]:
    return {
        "request": request,
        "user": user,
        "now": datetime.now(),
        "asset_version": asset_version(),
        # the login screen states the real session length rather than offering a
        # remember-me checkbox that does nothing
        "session_hours": int(load_config().get("portal.session_hours", 8)),
        "can_override": bool(user and has_role(user.role, "evaluator")),
        "can_see_identity": bool(user and has_role(user.role, "evaluator")),
        "is_admin": bool(user and has_role(user.role, "admin")),
    }


@app.exception_handler(401)
async def unauthorised(request: Request, _exc: HTTPException) -> Response:
    return RedirectResponse(
        f"/login?next={request.url.path}", status_code=HTTP_303_SEE_OTHER
    )


@app.exception_handler(403)
async def forbidden(request: Request, exc: HTTPException) -> Response:
    user = current_user(request)
    context = base_context(request, user)
    context["message"] = exc.detail
    return templates.TemplateResponse(request, "error.html", context, status_code=403)


@app.on_event("startup")
def startup() -> None:
    init_db()
    created = bootstrap_admin()
    if created:
        username, password = created
        print("\n" + "=" * 66)
        print("  No users existed, so an administrator account was created.")
        print(f"  Username: {username}")
        print(f"  Password: {password}")
        print("  You will be asked to change it at first login.")
        print("=" * 66 + "\n")


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/overview") -> Response:
    if current_user(request):
        return RedirectResponse(next, status_code=HTTP_303_SEE_OTHER)
    context = base_context(request, None)
    context.update({"next": next, "error": None})
    return templates.TemplateResponse(request, "login.html", context)


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/overview"),
) -> Response:
    try:
        user = authenticate(username, password, ip=client_ip(request))
    except LoginError as exc:
        context = base_context(request, None)
        context.update({"next": next, "error": str(exc), "username": username})
        return templates.TemplateResponse(
            request, "login.html", context, status_code=401
        )

    request.session["user_id"] = user.id
    request.session["role"] = user.role
    if user.must_change_password:
        return RedirectResponse("/change-password", status_code=HTTP_303_SEE_OTHER)
    return RedirectResponse(next or "/overview", status_code=HTTP_303_SEE_OTHER)


@app.post("/logout")
def logout(request: Request) -> Response:
    user = current_user(request)
    if user:
        log_action(user, "logout", ip=client_ip(request))
    request.session.clear()
    return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)


@app.get("/change-password", response_class=HTMLResponse)
def change_password_form(request: Request) -> Response:
    user = require_user(request)
    context = base_context(request, user)
    context["error"] = None
    return templates.TemplateResponse(request, "change_password.html", context)


@app.post("/change-password", response_class=HTMLResponse)
def change_password(
    request: Request,
    current: str = Form(...),
    new_password: str = Form(...),
    confirm: str = Form(...),
) -> Response:
    user = require_user(request)
    context = base_context(request, user)

    error = None
    if not verify_password(user.password_hash, current):
        error = "Your current password is not correct."
    elif new_password != confirm:
        error = "The new passwords do not match."
    elif len(new_password) < 12:
        error = "Choose a password of at least 12 characters."
    elif new_password == current:
        error = "The new password must differ from the current one."

    if error:
        context["error"] = error
        return templates.TemplateResponse(
            request, "change_password.html", context, status_code=400
        )

    set_password(user.username, new_password)
    log_action(user, "password_changed", ip=client_ip(request))
    return RedirectResponse("/overview", status_code=HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Tab 1 — Overview
# ---------------------------------------------------------------------------
@app.get("/")
def root() -> Response:
    return RedirectResponse("/overview", status_code=HTTP_303_SEE_OTHER)


@app.get("/overview", response_class=HTMLResponse)
def overview(request: Request) -> Response:
    user = require_user(request)
    rubric = load_rubric()
    with session_scope() as session:
        cards = all_scorecards(session, rubric, include_evidence=False)
        rank_scorecards(cards)
        ov = build_overview(session, cards, rubric)

    context = base_context(request, user)
    context.update({
        "tab": "overview",
        "ov": ov,
        "rubric": rubric,
        "CRITERION_SHORT": CRITERION_SHORT,
        "CRITERION_HUE": CRITERION_HUE,
        "max_histogram": max((n for _, n in ov.histogram), default=1) or 1,
        # SVG geometry: the templates draw arcs, they do not compute them
        "DONUT_C": round(DONUT_C, 2),
        "DONUT_R": DONUT_R,
        "ARC_C": round(ARC_C, 2),
        "ARC_R": ARC_R,
        "QUALIFY_AT": QUALIFY_AT,
    })
    return templates.TemplateResponse(request, "overview.html", context)


# ---------------------------------------------------------------------------
# Tab 2 — Scorecards
# ---------------------------------------------------------------------------
def _filter_cards(
    cards: list[ScorecardView],
    q: str = "",
    theme: str = "",
    formation: str = "",
    stage: str = "",
    review: str = "",
    evaluated: str = "",
) -> list[ScorecardView]:
    needle = q.strip().lower()
    out = []
    for card in cards:
        if needle and needle not in (
            f"{card.ref} {card.title} {card.folder_name} {card.office}".lower()
        ):
            continue
        if theme and card.thematic_area != theme:
            continue
        if formation and card.formation_category != formation:
            continue
        if stage and card.development_stage != stage:
            continue
        if review == "yes" and not card.needs_review:
            continue
        if review == "no" and card.needs_review:
            continue
        if evaluated == "yes" and not card.evaluated:
            continue
        if evaluated == "no" and card.evaluated:
            continue
        out.append(card)
    return out


SORT_KEYS = {
    "total": lambda c: (-(c.total if c.evaluated else -1), c.ref),
    "ref": lambda c: c.ref,
    "title": lambda c: (c.title or c.folder_name).lower(),
    "formation": lambda c: (c.formation_category, -(c.total if c.evaluated else -1)),
    "theme": lambda c: (c.thematic_area, -(c.total if c.evaluated else -1)),
}


@app.get("/scorecards", response_class=HTMLResponse)
def scorecards(
    request: Request,
    q: str = "",
    theme: str = "",
    formation: str = "",
    stage: str = "",
    review: str = "",
    evaluated: str = "",
    sort: str = "total",
) -> Response:
    user = require_user(request)
    rubric = load_rubric()
    with session_scope() as session:
        cards = all_scorecards(session, rubric, include_evidence=False)
        rank_scorecards(cards)

    filtered = _filter_cards(cards, q, theme, formation, stage, review, evaluated)
    filtered.sort(key=SORT_KEYS.get(sort, SORT_KEYS["total"]))

    context = base_context(request, user)
    context.update({
        "tab": "scorecards",
        "rubric": rubric,
        "cards": filtered,
        "total_count": len(cards),
        "filters": {
            "q": q, "theme": theme, "formation": formation, "stage": stage,
            "review": review, "evaluated": evaluated, "sort": sort,
        },
        "theme_labels": THEME_LABELS,
        "formation_labels": FORMATION_LABELS,
        "stage_labels": STAGE_LABELS,
        "CRITERION_SHORT": CRITERION_SHORT,
        "CRITERION_HUE": CRITERION_HUE,
        "QUALIFY_AT": QUALIFY_AT,
        "query_string": request.url.query,
    })
    return templates.TemplateResponse(request, "scorecards.html", context)


@app.get("/scorecards/{ref}", response_class=HTMLResponse)
def scorecard_detail(request: Request, ref: str) -> Response:
    user = require_user(request)
    rubric = load_rubric()
    can_see_identity = has_role(user.role, "evaluator")

    with session_scope() as session:
        submission = session.scalar(select(Submission).where(Submission.ref == ref))
        if submission is None:
            raise HTTPException(status_code=404, detail=f"No submission {ref}")
        card = build_scorecard(
            session, submission, rubric,
            include_identity=can_see_identity, include_evidence=True,
        )
        # rank is only meaningful against the size of the evaluated set
        ranked = all_scorecards(session, rubric, include_evidence=False)
        rank_scorecards(ranked)
        total_evaluated = sum(1 for c in ranked if c.evaluated)
        card.rank_overall = next(
            (c.rank_overall for c in ranked if c.ref == card.ref), None
        )
        overrides = {
            o.criterion: {
                "human_score": o.human_score,
                "machine_score": o.machine_score,
                "justification": o.justification,
                "reviewer": o.reviewer.username if o.reviewer else "",
                "created_at": o.created_at,
            }
            for o in session.scalars(
                select(Override).where(Override.submission_id == submission.id)
            )
        }

    context = base_context(request, user)
    context.update({
        "tab": "scorecards",
        "rubric": rubric,
        "card": card,
        "overrides": overrides,
        "CRITERION_HUE": CRITERION_HUE,
        "CRITERION_SHORT": CRITERION_SHORT,
        "theme_labels": THEME_LABELS,
        "formation_labels": FORMATION_LABELS,
        "stage_labels": STAGE_LABELS,
        # "rank 3 of 6" says more than "rank 3"
        "total_evaluated": total_evaluated,
    })
    return templates.TemplateResponse(request, "scorecard_detail.html", context)


@app.get("/scorecards/{ref}/why/{criterion}", response_class=HTMLResponse)
def criterion_rationale(request: Request, ref: str, criterion: str) -> Response:
    """The reasoning behind one criterion score, as an HTML fragment.

    Served on demand rather than embedded in every table row: at 400
    submissions the table would otherwise carry 4,800 rationales and every
    evidence quote with it.
    """
    user = require_user(request)
    rubric = load_rubric()
    try:
        spec = rubric.criterion(criterion)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown criterion {criterion!r}")

    with session_scope() as session:
        submission = session.scalar(select(Submission).where(Submission.ref == ref))
        if submission is None:
            raise HTTPException(status_code=404, detail=f"No submission {ref}")
        card = build_scorecard(
            session, submission, rubric,
            include_identity=has_role(user.role, "evaluator"),
            include_evidence=True,
        )

    view = card.criterion(criterion)
    context = base_context(request, user)
    context.update({
        "card": card,
        "spec": spec,
        "view": view,
        "rubric": rubric,
        "hue": CRITERION_HUE.get(criterion, "innovation"),
        "bands": rubric.bands,
    })
    return templates.TemplateResponse(request, "_rationale.html", context)


@app.post("/scorecards/{ref}/review")
def set_review_decision(
    request: Request,
    ref: str,
    decision: str = Form("approved"),
    note: str = Form(""),
) -> Response:
    """Record, or withdraw, an evaluator's sign-off on a submission.

    Returns JSON: the table updates the one row in place rather than reloading
    a page that may be a hundred rows long and scrolled halfway down.
    """
    user = require_role(request, "evaluator")
    if decision not in ("approved", "pending"):
        raise HTTPException(status_code=400, detail="Unknown review decision.")

    with session_scope() as session:
        submission = session.scalar(select(Submission).where(Submission.ref == ref))
        if submission is None:
            raise HTTPException(status_code=404, detail=f"No submission {ref}")

        existing = session.scalar(
            select(ReviewDecision).where(ReviewDecision.submission_id == submission.id)
        )
        if existing is None:
            session.add(
                ReviewDecision(
                    submission_id=submission.id,
                    decision=decision,
                    note=note.strip(),
                    reviewer_id=user.id,
                )
            )
        else:
            existing.decision = decision
            existing.note = note.strip()
            existing.reviewer_id = user.id

    log_action(
        user, "review_decision", target=ref, detail=decision, ip=client_ip(request)
    )
    return JSONResponse({
        "ref": ref,
        "decision": decision,
        "reviewer": user.full_name or user.username,
    })


@app.post("/scorecards/{ref}/override")
def apply_override(
    request: Request,
    ref: str,
    criterion: str = Form(...),
    human_score: str = Form(...),
    justification: str = Form(...),
) -> Response:
    """Amend one criterion score, against the evaluator's name.

    The machine score is never overwritten — it is kept alongside the amended
    one, so the record shows both what was assessed and what the committee
    decided. Answers JSON when the edit dialog asks for it, so the table can
    update the amended cell and the total without a page reload.
    """
    user = require_role(request, "evaluator")
    rubric = load_rubric()
    wants_json = request.headers.get("x-requested-with") == "fetch"

    try:
        spec = rubric.criterion(criterion)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown criterion {criterion!r}")

    def refuse(message: str) -> Response:
        if wants_json:
            return JSONResponse({"error": message}, status_code=400)
        raise HTTPException(status_code=400, detail=message)

    justification = justification.strip()
    if len(justification) < 20:
        return refuse(
            "A revised score needs a note of at least 20 characters. It is "
            "recorded against your name and forms part of the audit trail."
        )

    try:
        score = float(human_score)
    except ValueError:
        return refuse("The score must be a number.")
    if not 0 <= score <= spec.weight:
        return refuse(f"{spec.name} is scored out of {spec.weight}.")

    with session_scope() as session:
        submission = session.scalar(select(Submission).where(Submission.ref == ref))
        if submission is None:
            raise HTTPException(status_code=404, detail=f"No submission {ref}")

        card = build_scorecard(session, submission, rubric, include_evidence=False)
        view = card.criterion(criterion)
        machine_score = view.machine_score if view else 0.0

        existing = session.scalar(
            select(Override).where(
                Override.submission_id == submission.id,
                Override.criterion == criterion,
            )
        )
        if existing:
            existing.human_score = score
            existing.machine_score = machine_score
            existing.justification = justification
            existing.reviewer_id = user.id
        else:
            session.add(
                Override(
                    submission_id=submission.id,
                    criterion=criterion,
                    machine_score=machine_score,
                    human_score=score,
                    reviewer_id=user.id,
                    justification=justification,
                )
            )

    log_action(
        user, "score_override", target=f"{ref}/{criterion}",
        detail=f"machine={machine_score} human={score}; {justification}",
        ip=client_ip(request),
    )

    if wants_json:
        # rebuild so the returned total reflects this amendment
        with session_scope() as session:
            submission = session.scalar(select(Submission).where(Submission.ref == ref))
            fresh = build_scorecard(session, submission, rubric, include_evidence=False)
        return JSONResponse({
            "ref": ref,
            "criterion": criterion,
            "score": round(score, 1),
            "machine_score": round(machine_score, 1),
            "max": spec.weight,
            "total": fresh.total,
            "machine_total": fresh.machine_total,
            "reviewer": user.full_name or user.username,
        })
    return RedirectResponse(f"/scorecards/{ref}", status_code=HTTP_303_SEE_OTHER)


@app.post("/scorecards/{ref}/override/{criterion}/remove")
def remove_override(request: Request, ref: str, criterion: str) -> Response:
    user = require_role(request, "evaluator")
    with session_scope() as session:
        submission = session.scalar(select(Submission).where(Submission.ref == ref))
        if submission is None:
            raise HTTPException(status_code=404, detail=f"No submission {ref}")
        existing = session.scalar(
            select(Override).where(
                Override.submission_id == submission.id,
                Override.criterion == criterion,
            )
        )
        if existing:
            session.delete(existing)
    log_action(
        user, "score_override_removed", target=f"{ref}/{criterion}", ip=client_ip(request)
    )
    return RedirectResponse(f"/scorecards/{ref}", status_code=HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@app.get("/export.xlsx")
def export_xlsx(request: Request) -> Response:
    user = require_user(request)
    from ..export_excel import export_workbook

    # The workbook must not be a way round the role that governs the screen: a
    # viewer cannot see submitter identity in the portal, so their download does
    # not carry it either.
    path = export_workbook(include_identity=has_role(user.role, "evaluator"))
    log_action(user, "export_xlsx", target=path.name, ip=client_ip(request))
    return Response(
        content=path.read_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


@app.get("/export.csv")
def export_csv(
    request: Request,
    q: str = "",
    theme: str = "",
    formation: str = "",
    stage: str = "",
    review: str = "",
    evaluated: str = "",
) -> Response:
    """CSV of exactly what the current filter shows."""
    user = require_user(request)
    rubric = load_rubric()
    with session_scope() as session:
        cards = all_scorecards(session, rubric, include_evidence=False)
        rank_scorecards(cards)
    filtered = _filter_cards(cards, q, theme, formation, stage, review, evaluated)
    filtered.sort(key=SORT_KEYS["total"])

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    header = ["Ref", "Title", "Office", "Formation", "Theme", "Stage"]
    for criterion in rubric.criteria:
        for sub in criterion.sub_criteria:
            header.append(f"{criterion.name}: {sub.name} (/{sub.max})")
        header.append(f"{criterion.name} total (/{criterion.weight})")
    header += [
        "Machine total", "Final total", "Rank overall", "Rank in stream",
        "Overridden", "Needs review", "Review reason",
        "Committee decision", "Approved by", "Approved on",
        "Rubric", "Assessed by",
    ]
    writer.writerow(header)

    for card in filtered:
        row: list[Any] = [
            card.ref, card.title or card.folder_name, card.office,
            card.formation_category, card.thematic_area, card.development_stage,
        ]
        for criterion in rubric.criteria:
            view = card.criterion(criterion.key)
            for sub in criterion.sub_criteria:
                found = (
                    next((s for s in view.sub_scores if s.sub_criterion == sub.key), None)
                    if view else None
                )
                row.append(found.score if found else "")
            row.append(view.effective_score if view and card.evaluated else "")
        row += [
            card.machine_total if card.evaluated else "",
            card.total if card.evaluated else "",
            getattr(card, "rank_overall", "") or "",
            getattr(card, "rank_in_stream", "") or "",
            "yes" if card.is_overridden else "",
            "yes" if card.needs_review else "",
            next((r["detail"] for r in public_flags(card)
                  if r["key"] == "human review required"), ""),
            card.review_decision,
            card.review_reviewer,
            in_ist(card.review_decided_at),
            card.rubric_slug,
            PUBLIC_EVALUATOR_LABEL if card.evaluated else "",
        ]
        writer.writerow(row)

    log_action(
        user, "export_csv", detail=f"{len(filtered)} rows", ip=client_ip(request)
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="CAG101-scorecards-{stamp}.csv"'
        },
    )


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request) -> Response:
    user = require_role(request, "admin")
    with session_scope() as session:
        users = list(session.scalars(select(User).order_by(User.username)))
    context = base_context(request, user)
    context.update({"tab": "admin", "users": users, "new_password": None})
    return templates.TemplateResponse(request, "admin_users.html", context)


@app.post("/admin/users", response_class=HTMLResponse)
def admin_add_user(
    request: Request,
    username: str = Form(...),
    role: str = Form("viewer"),
    full_name: str = Form(""),
) -> Response:
    admin = require_role(request, "admin")
    from ..auth import add_user

    error = None
    password = None
    try:
        password = add_user(username, role=role, full_name=full_name)
        log_action(admin, "user_created", target=username, detail=f"role={role}",
                   ip=client_ip(request))
    except ValueError as exc:
        error = str(exc)

    with session_scope() as session:
        users = list(session.scalars(select(User).order_by(User.username)))
    context = base_context(request, admin)
    context.update({
        "tab": "admin", "users": users,
        "new_password": password, "new_username": username, "error": error,
    })
    return templates.TemplateResponse(request, "admin_users.html", context)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/healthz")
def healthz() -> dict[str, Any]:
    with session_scope() as session:
        count = session.scalar(select(Submission).limit(1))
        evaluations = session.scalar(select(Evaluation).limit(1))
    return {
        "status": "ok",
        "has_submissions": count is not None,
        "has_evaluations": evaluations is not None,
    }
