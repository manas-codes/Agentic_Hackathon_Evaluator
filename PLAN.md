# CAG 101 Innovation Ideas — Agentic Evaluation Framework & Scorecard Portal

**Owner:** AI PMU, NeGD (MeitY) · **Client wing:** PPG Wing, O/o CAG of India
**Plan date:** 03-09-2026 · **Status:** Draft for approval

---

## 1. What we are building

A three-part system:

1. **Ingestion + extraction pipeline** — walks the Google Drive tree, pulls each submission folder (filled Appendix-I form + optional PPT / demo video / Word doc), and normalises everything into one canonical JSON record per submission.
2. **Agentic evaluator** — scores each submission out of 100 against the scheme's rubric, one agent per criterion, every sub-score backed by verbatim evidence from the submission, with an adversarial verification pass.
3. **Portal** — login-protected web app: Tab 1 overview dashboard (totals, evaluated vs pending, distributions), Tab 2 scorecards (per-submission breakdown, human override, Excel/CSV export).

**Positioning (important):** the system produces an *indicative pre-assessment* to assist the Filtering Committees. It does not decide. Every score is traceable to evidence, rubric version and model version, and is overridable by a named human reviewer with recorded justification. This framing should be stated on the portal itself.

---

## 2. Rubric profiles

Rubric lives in version-controlled YAML (`rubrics/cag101_v1.yaml`, `rubrics/category1_v1.yaml`), never hard-coded. Changing weights or anchors bumps the version and marks affected scores as stale.

### 2.1 Profile A — CAG 101 Innovation Ideas (default, 100 pts)

| Criterion | Wt | Sub-criteria (points) |
|---|---|---|
| Innovation & Originality | 25 | Novelty vs existing IA&AD practice (10) · Distinctiveness of approach (8) · Creativity of problem–solution fit (7) |
| Institutional Relevance | 25 | Validity & severity of the problem identified (10) · Alignment with mandate, pillars and thematic area (8) · Usability by intended offices/wings (7) |
| Feasibility & Scalability | 25 | Implementation practicality & resource realism (9) · Technical/operational scalability across 130+ offices (9) · Sustainability, data governance & security readiness (7) |
| Potential Impact | 25 | Credibility of quantified impact & assumptions (10) · Breadth of beneficiaries / coverage (8) · Depth of improvement in efficiency, timelines, quality, outreach (7) |

### 2.2 Profile B — Category-I Awards 2026 (100 pts)

| Criterion | Wt | Sub-criteria (points) |
|---|---|---|
| The Solution | 40 | Originality (15) · Problem–solution fit (15) · Quality of design & improvement over earlier process (10) |
| Benefits | 30 | Measurable, evidenced impact (15) · Benefit to Department/stakeholders (10) · Gains in quality/timeliness/cost/governance (5) |
| Sustainability & Replicability | 20 | Durability of improvement (10) · Ease of adoption elsewhere, or context-specific relevance in lieu (10) |
| Change Management | 10 | Planning & implementation quality (4) · Stakeholder engagement & documentation (3) · Risk handling & challenges overcome (3) |

### 2.3 Scoring bands (applied to every sub-criterion, pro-rated)

| Band | % of sub-criterion | Meaning |
|---|---|---|
| Absent | 0–20% | Not addressed, or asserted with no substance |
| Weak | 21–45% | Addressed vaguely; generic claims, no specifics |
| Adequate | 46–70% | Clearly addressed with some specifics |
| Strong | 71–88% | Specific, well-reasoned, partly evidenced |
| Exceptional | 89–100% | Specific, evidenced (pilot data, artefacts, metrics with sound basis) |

**Evidence rule:** every sub-score must carry either ≥1 verbatim quote (with source file + section) or the literal marker `NO_EVIDENCE_FOUND`. A sub-score above the Adequate band with no evidence is rejected by the verifier and re-scored.

### 2.4 Non-scoring flags captured alongside

Completeness (mandatory fields filled), stage of development (Concept → Ready for Scale), evidence strength (piloted / tested / claimed only), thematic area (A–D), solution type, formation category routing (Union / State / Other / Special Category States), tech-centric vs process, data-privacy concern raised, similar-idea cluster ID, NAeG-eligibility hint.

---

## 3. Pipeline architecture

```
Drive walk ──► Fetch ──► Extract ──► Form-map ──► Screen ──► Score (4 agents ‖) ──► Verify ──► Persist ──► Portal / Excel
 (API)        (cache)   (per type)   (LLM)      (rules+LLM)   (LLM, blind)        (LLM)      (DB)
```

**Stage 1 — Discover & fetch.** Recursive Drive listing; one folder = one submission. Each file recorded with name, MIME type, size, Drive ID, SHA-256. Files cached locally so re-scoring never re-downloads.

**Stage 2 — Extract.** Deterministic parsers, no LLM:
- PDF → PyMuPDF text; if a page yields < 50 chars, treat as scanned → OCR (Tesseract) and mark `ocr_used`.
- DOCX → python-docx (paragraphs + tables). PPTX → python-pptx (slide text + speaker notes).
- Video → ffmpeg audio extract → local Whisper transcript + 1 keyframe / 30 s described by the vision model. Marked `media_derived`.
- Images / diagrams → vision model description.
- Drive/YouTube links inside the form → recorded, fetched only if reachable and permitted; otherwise flagged `unverifiable_link`.

**Stage 3 — Form-map.** LLM maps extracted text onto the canonical Appendix-I schema (fields 1.1–7). Handles the reality that people type outside the boxes, merge sections or attach a free-form note. Output includes a `field_confidence` and `missing_fields` list. This is the only place where document layout variance is absorbed — everything downstream sees one clean schema.

**Stage 4 — Screen.** Rules first: mandatory fields present, word-limit compliance, at least one primary attachment (6.1), declaration signed. Then a light LLM check for "is this a substantive submission or a placeholder". Output: `PASS` / `INCOMPLETE(reasons)`. Incomplete submissions are still scored but tagged, since the scheme allows rejection at screening — that call stays with PPG.

**Stage 5 — Score (parallel).** One agent per criterion. Each receives: the rubric slice for its criterion only, the canonical record, and the attachment digests — **with submitter names, office and state stripped** (blind review, to keep evaluation defensible on fairness grounds). Temperature 0. Structured output: sub-scores, evidence quotes, one-line rationale per sub-criterion, and a confidence value.

**Stage 6 — Verify & aggregate.** An adversarial reviewer agent re-reads each criterion's output against the evidence and can only *lower* unsupported scores or demand a re-score. Then:
- Two independent scoring passes; if totals differ by > 8 points, run a third and flag `human_review_required`.
- Aggregate to /100, compute rank overall and rank within formation category and thematic area.
- Similar-idea clustering via embeddings + agglomerative clustering (cosine ≥ 0.82) — this directly produces the "idea library" the Concept Paper requires (§IV.e).
- QA sampling: auto-flag the top and bottom deciles plus all high-variance cases for mandatory human eyes.

**Stage 7 — Persist.** Every score row stores: rubric version, model ID, prompt version, evidence, timestamp, token cost, and any human override with reviewer identity and justification. Full audit trail.

---

## 4. Data model (core tables)

- `submissions` — id, drive_folder_id, title, theme, solution_type, stage, formation_category, office (restricted view), submitted_on, content_hash, status
- `files` — submission_id, name, mime, size, drive_id, sha256, extraction_status, ocr_used
- `extractions` — submission_id, file_id, text, transcript, derived_notes
- `canonical_records` — submission_id, JSON of Appendix-I fields, field_confidence, missing_fields
- `evaluations` — id, submission_id, rubric_version, model, pass_no, total, created_at, cost_tokens
- `criterion_scores` — evaluation_id, criterion, sub_criterion, score, max, rationale, confidence
- `evidence` — criterion_score_id, quote, source_file, locator
- `flags` — submission_id, type, value
- `overrides` — submission_id, criterion, human_score, reviewer_id, justification, created_at
- `clusters` / `cluster_members` — idea library
- `users`, `sessions`, `audit_log`
- `jobs` — per-submission pipeline state for resume/retry

---

## 5. Portal specification

**Auth.** Username + password (Argon2id), server-side sessions, HTTP-only cookies, CSRF tokens, lockout after 5 failed attempts, full audit log. Roles:
- `admin` — manage users, trigger ingestion/scoring, edit rubric version in use
- `evaluator` — view all, override scores with justification
- `viewer` — read-only, export

**Tab 1 — Overview.** Total submissions · Evaluated · In progress · Failed/needs attention · Average and median score · Score distribution histogram · Counts by thematic area (A–D), by formation category, by development stage · Top-10 leaderboard · Similar-idea cluster count · Ingestion & job health panel · Last run timestamp and cost meter.

**Tab 2 — Scorecards.** Sortable, filterable, paginated table: ID, title, theme, formation, stage, four criterion scores, total, rank, flags, status. Row click → scorecard detail drawer:
- Criterion-by-criterion breakdown with sub-scores and the evidence quotes behind each
- Attachment inventory with links back to the Drive source
- Flags and completeness reasons
- Override panel (per criterion, justification mandatory)
- Re-run button (single submission)

**Export.** Any filtered view → `.xlsx` via openpyxl, multi-sheet:
1. `Summary` — run metadata, rubric version, counts, averages
2. `Scorecards` — one row per submission: all sub-scores, criterion totals, final /100, rank, overall rank, flags, override flag, human score. Frozen header, autofilter, colour scale on the total column.
3. `Evidence` — long format, one row per sub-score with its quote and source, for committee verification
4. `Idea Library` — cluster ID, members, offices
Also `.csv` for the plain scorecard sheet.

---

## 6. Technology

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11 | Team standard; best PDF/media tooling |
| API + app | FastAPI | Async, typed, single-process deploy |
| UI | Jinja2 + HTMX + Tailwind (recommended) | No build step, one artefact to hand over for on-prem/NIC hosting; React SPA is the alternative if a richer UI is wanted |
| DB | SQLite for the 10-submission pilot → PostgreSQL for the 400 run | Zero-config start, clean migration path via SQLAlchemy |
| LLM | Claude Sonnet 5 for scoring, Opus 5 for verification and tie-breaks | Cost/quality split (subject to §8 data decision) |
| Extraction | PyMuPDF, python-docx, python-pptx, Tesseract, ffmpeg, Whisper | Covers every format seen in the Drive |
| Excel | openpyxl | Formatting + multi-sheet |
| Drive | Google Drive API v3, read-only scope, service account | Auditable, no personal credentials |
| Jobs | SQLite/Postgres-backed job table + asyncio workers | 400 items does not justify Redis/Celery |

---

## 7. Scaling from 10 → 400

- **Idempotent jobs** keyed on submission content hash; re-running skips completed work.
- **Extraction cache** separate from scoring, so a rubric change re-scores without re-parsing or re-transcribing (transcription is the expensive step).
- **Concurrency** 6–8 submissions in flight, exponential backoff on rate limits, per-run cost ceiling that halts the run rather than overspending.
- **Checkpointing** — a killed run resumes exactly where it stopped.
- **Cost control** — attachment digests are summarised once and reused across all four criterion agents rather than re-sending full transcripts four times. Estimated 40k–70k input tokens per submission with a video; I'll compute rupee cost against the current published rate card before the 400 run and put a hard ceiling in config.
- **Fairness at scale** — same rubric version, temperature 0, blind inputs, and a fixed prompt hash for the entire batch. If the rubric changes mid-way, the whole batch is re-scored, never partially.

---

## 8. Data handling — needs a decision before build (flagged)

Submissions are unpublished internal CAG material, some naming officials and offices. Two consequences:

1. **Sending submission content to a cloud LLM API is a data-transfer decision that PPG Wing (and likely CAG's IS Wing) should sign off on**, not one we should assume. Options: (a) Claude API with a no-training commercial agreement, (b) an on-prem/self-hosted open model, (c) hybrid — cloud for scoring the de-identified text only, nothing else leaves. Option (c) is the most defensible and is what the blind-review design already enables.
2. **PII minimisation** is built in regardless: names, emails and mobile numbers from Section 1 are stored in a restricted table, never sent to the model, and never included in exports below `evaluator` role. No Aadhaar or citizen data should be present; if the pipeline detects an Aadhaar-shaped number it flags and redacts.

Also to note for official use: the portal, before it is used by committees, will need a security review (VAPT/CERT-In empanelled) and GIGW compliance check, and the "AI-assisted, human-decided" framing should be confirmed with PPG Wing. Recommend confirming with the relevant NeGD/CAG authority before any committee-facing deployment.

---

## 9. Milestones

| # | Milestone | Output | Est. |
|---|---|---|---|
| M1 | Scaffold + ingestion + extraction | Repo, rubric YAMLs, canonical schema, 10 submissions parsed to JSON incl. video transcripts | 1–2 days |
| M2 | Agentic scoring engine | All 10 scored /100 with evidence, verifier live, first Excel export from CLI | 2 days |
| M3 | Portal | Login, Tab 1 dashboard, Tab 2 scorecards + detail + override, Excel export from UI | 2–3 days |
| M4 | Calibration | 3–5 submissions scored by a human evaluator, agreement measured, anchors tuned, rubric frozen at v1 | 1 day |
| M5 | Scale + handover | 400-submission run with monitoring, PostgreSQL migration, ops runbook, handover note | 2 days + run time |

Calibration (M4) is not optional. Without a human-scored gold set we cannot state how closely the engine tracks committee judgement, and that claim is the whole basis for the committees trusting it.

---

## 10. Immediate next actions

1. Confirm the four decisions (LLM/data route, Drive access method, rubric profile, UI stack).
2. Pick a permanent project folder — the code currently has nowhere to live but a temporary scratch workspace.
3. Provide either the Drive folder ID + service-account access, or a local copy of the 10 submission folders, so M1 can start against real files.
