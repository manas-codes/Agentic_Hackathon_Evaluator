# CAG 101 Innovation Ideas — Evaluation Framework and Scorecard Portal

Decision-support tooling for the PPG Wing, Office of the Comptroller and Auditor
General of India. It reads submissions to the CAG 101 Innovation Ideas
Initiative, scores each one out of 100 against the rubric published in the
scheme, and presents the results as scorecards with a downloadable workbook.

**What it is:** an evidence-backed pre-assessment that helps the Filtering
Committees work through a large intake consistently.
**What it is not:** a decision. Every sub-score is traceable to a quoted passage
from the submission, and any score can be overridden by a named evaluator with
the reason recorded.

Built by the AI PMU, National e-Governance Division (NeGD), MeitY.

---

## 1. Quick start

```bash
python -m pip install -r requirements.txt -r requirements-pipeline.txt
```

Copy `.env.example` to `.env` and fill in:

```
ANTHROPIC_API_KEY=sk-ant-...
SESSION_SECRET=<generate with the command below>
```

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then:

```bash
python cag.py init-db
python cag.py check-rubric
```

Place the submissions — one folder per submission, mirroring the Drive layout:

```
data/submissions/
    Submission from AG Karnataka/
        Filled Appendix-I form.pdf
        Deck.pptx
        Demo.mp4
    Submission from AG Odisha/
        Filled Appendix-I form.docx
```

A single loose file is also accepted as a one-file submission, since some
offices send only the form.

```bash
python cag.py ingest      # find files, extract text, cache it
python cag.py map         # map the text onto the Appendix-I schema
python cag.py score       # run the scoring agents
python cag.py cluster     # build the similar-idea library
python cag.py export      # write the Excel workbook
python cag.py serve       # start the portal on http://127.0.0.1:8000
```

On first start the portal prints a temporary administrator password. You will
be required to change it at first sign-in.

**Estimate the cost before a large run:**

```bash
python cag.py score --dry-run
```

This measures the real token count of one submission through the token-counting
endpoint and projects the cost for 10, 50, 100 and 400 submissions.

---

## 2. How a submission is scored

```
folders ─► extract ─► map ─► screen ─► score (4 agents ‖) ─► verify ─► store ─► portal / Excel
```

| Stage | What happens | Model used |
|---|---|---|
| Extract | PDF, DOCX, PPTX, XLSX, images, video are read; text cached | none — deterministic parsers |
| Map | Text mapped onto the Appendix-I fields; identity separated | utility model |
| Screen | Completeness, stage, evidence strength, formation routing | rules |
| Score | One agent per criterion, in parallel, on blinded text | scoring model |
| Verify | Adversarial audit of each sub-score against its evidence | verification model |
| Consolidate | Per-sub-criterion mean of the passes; evidence unioned | none |

### The controls that make the output defensible

**Blind review.** Submitter names and the submitting office are masked before
scoring. If a committee asks whether the machine favoured particular offices,
the answer is that it could not see them.

**Evidence or nothing.** Every sub-score carries verbatim quotes with source and
locator, or the marker `NO_EVIDENCE_FOUND`. A *deterministic* guardrail — code,
not a model instruction — caps an unevidenced sub-score at the top of the Weak
band. Scores above the maximum are clamped. A sub-criterion the model omits
becomes an explicit zero rather than silently vanishing from the total.

**Criterion isolation.** Each agent sees only its own slice of the rubric, so a
strong solution cannot inflate the impact score by halo effect.

**Two passes, disagreement surfaced.** Each submission is scored twice. The
recorded score is the per-sub-criterion mean. Where the passes disagree by more
than the configured threshold, a third pass runs and the submission is flagged
for human review. Disagreement is reported, not smoothed away.

**Lowering-only verification.** A second reader audits each sub-score against
its quoted evidence and can only lower it. A verifier able to raise scores would
be a second scorer, and two scorers average towards generosity.

**Special Category States.** The scheme requires that the geographical,
logistical, connectivity, infrastructural and human-resource environment be
taken into account. Submissions routed to that stream receive an explicit
instruction not to be marked down for constraints imposed by their operating
environment. This adjusts judgement, not weights.

**Full provenance.** Every stored score carries the rubric version and content
hash, prompt version, model ID, token count and cost.

---

## 3. Privacy and data handling

Contact details never reach a model. Emails, mobile numbers, PAN-shaped and
Aadhaar-shaped numbers are redacted from all text *before the first model call*.
An Aadhaar-shaped number, if found, is redacted **and flagged** — submission
material should not contain citizen identifiers, and PPG should know if it does.

Names are visible only to the form-mapper, which needs them to build the
restricted identity record and to know what to mask. They are masked before the
scoring stage, where judgement happens. Identity lives in a separate database
table, is excluded from every model prompt, and is hidden from the `viewer`
role.

Audit figures survive redaction — amounts, percentages, year ranges and
transaction counts are preserved, because destroying them would destroy the
evidence the rubric depends on. This is covered by tests.

**Before any committee-facing deployment**, the following need confirming with
the relevant NeGD/CAG authority:

- sign-off from PPG Wing and IS Wing for sending de-identified submission
  content to a cloud model API;
- a security review (VAPT through a CERT-In empanelled agency) and a GIGW
  compliance check on the portal;
- agreement on the "AI-assisted, human-decided" framing shown in the portal
  banner and the workbook Notes sheet.

---

## 4. Configuration

Everything that affects scoring lives in `config.yaml` and `rubrics/*.yaml`.

| Setting | Meaning |
|---|---|
| `rubric.active` | Which rubric profile is in force (`cag101_v1` or `category1_v1`) |
| `llm.scoring_model` | Default `claude-opus-5`. After calibration, `claude-sonnet-5` cuts cost — measure agreement on the gold set first |
| `llm.effort` | Thinking depth: `low` … `max` |
| `llm.enable_prompt_caching` | Caches the rubric prefix across submissions — a large saving at 400 |
| `llm.use_batch_api` | Batch API halves the cost; suits an overnight bulk run |
| `pipeline.scoring_passes` | Independent passes per submission (default 2) |
| `pipeline.disagreement_threshold` | Points of disagreement that trigger a third pass and a review flag |
| `pipeline.concurrency` | Submissions in flight |
| `cost.max_run_cost_inr` | Hard ceiling — the run halts rather than overspending |
| `cost.usd_to_inr` | Conversion used for reporting only. **An assumption — set it before quoting figures** |
| `extraction.enable_ocr` | Needs the Tesseract binary on PATH |
| `extraction.enable_video_transcription` | Needs ffmpeg and faster-whisper |

There is deliberately **no `temperature` setting**: it is rejected with HTTP 400
on Opus 5 and Sonnet 5. Repeatability comes from the fixed rubric, anchored
bands, schema-constrained output and multiple passes.

### Changing the rubric

The rubric is data, not code. Validation refuses to load a rubric whose
criterion weights do not sum to the total, or whose sub-criteria points do not
sum to their criterion's weight — so a typo cannot silently produce scores that
do not add up. Any edit changes the content hash, which is recorded against
every score, so a rubric change is always visible in the audit trail. After
calibration, set `frozen: true`.

---

## 5. Both rubric profiles

**`cag101_v1`** — CAG 101 Innovation Ideas Initiative (Concept Paper §IV.d):
Innovation and Originality 25 · Institutional Relevance 25 · Feasibility and
Scalability 25 · Potential Impact 25.

**`category1_v1`** — CAG's Awards for Innovation and Excellence, Category-I
(Scheme §10): The Solution 40 · Benefits 30 · Sustainability and Replicability
20 · Change Management 10.

The weights in both are those prescribed by the scheme documents and are
asserted by the test suite. The sub-criteria within each are an internal
decomposition for consistency and do not alter the prescribed weights.

Category-I recognises work **already implemented** between 01-04-2025 and
31-03-2026, so its profile flags a submission that is still an idea or prototype
as `belongs_under_cag101` rather than scoring it generously.

---

## 6. The portal

Two tabs, plus user administration.

**Overview** — total submissions, evaluated, awaiting evaluation, flagged for
review, overrides; score distribution and mean by criterion; composition of the
intake by thematic area, formation stream, development stage, completeness and
evidence strength; highest-scoring list; a "needs attention" list; idea-library
summary; and run provenance including model cost.

**Scorecards** — filterable, sortable table with every criterion score, total,
and rank both overall and within the formation stream. Opening a row shows each
sub-score with its band, rationale and the verbatim evidence behind it, the file
inventory, all flags, the individual pass totals, and the override panel.

| Role | Can |
|---|---|
| `viewer` | Read scorecards and evidence, export. Office and submitter details hidden |
| `evaluator` | As viewer, plus restricted identity visible and score override permitted |
| `admin` | As evaluator, plus user management |

Sign-ins, failed sign-ins, overrides and exports are all written to
`audit_log` with user, timestamp and IP.

```bash
python cag.py users add <username> --role evaluator --full-name "Name"
python cag.py users list
```

Passwords are Argon2id hashed. An account locks for 15 minutes after 5 failed
attempts. Login gives the same message for an unknown user and a wrong password,
so the form cannot be used to enumerate usernames.

---

## 7. The Excel workbook

| Sheet | Contents |
|---|---|
| Summary | Run provenance, rubric hash, counts, distribution, composition |
| Scorecards | One row per submission: every sub-score, criterion totals, machine total, override, final total, ranks, flags |
| Evidence | Long format — one row per sub-score with its quote and source, so any figure can be verified without the portal |
| Idea Library | Groups of similar ideas (Concept Paper §IV.e) |
| Notes | How to read the workbook, the controls applied, and its limits |

Rows flagged for human review are shaded orange; overridden rows yellow.
Unevidenced sub-scores appear on the Evidence sheet marked `NO_EVIDENCE_FOUND`
— their absence from that sheet would itself be the finding.

The portal's **Export this view (.csv)** honours the current filter; **Full
workbook (.xlsx)** always exports everything.

---

## 8. Scaling to 400 submissions

- **Idempotent.** Ingestion keys on a content hash over the submission's files.
  Re-running skips unchanged folders.
- **Extraction cached separately from scoring**, so a rubric change re-scores
  without re-parsing or re-transcribing. Transcription is the expensive step.
- **Resumable.** A killed run leaves completed work stored; re-running continues.
- **Cost ceiling.** The run halts rather than overspending, with a warning at 70%.
- **Prompt caching** on the rubric prefix, which is byte-identical across
  submissions for a given criterion.
- **Batch API** (`llm.use_batch_api`) halves the cost for an overnight run.
- **Postgres.** Set `paths.database` to a `postgresql+psycopg://` URL. Nothing
  else changes.

Fairness at scale depends on one rule: **a batch is scored under one rubric
version.** If the rubric changes mid-way, re-score the whole batch. Never leave
a batch partly scored under two versions.

---

## 9. Calibration — do this before trusting the numbers

Before the 400-submission run, have an experienced evaluator score 5–10
submissions by hand, blind to the machine scores, then compare. The purpose is
not to prove the machine right; it is to find out where it is systematically
generous or harsh and to tune the band anchors in the rubric YAML accordingly.

Until that comparison exists, no claim can be made about how closely the engine
tracks committee judgement — and that claim is the whole basis on which a
committee would rely on it. Freeze the rubric (`frozen: true`) once calibrated.

---

## 10. Tests

```bash
python tests/test_core.py
```

Covers the parts whose correctness must not depend on a model: privacy
redaction (including that audit figures survive it), blind-review masking,
formation routing, rubric arithmetic against both published schemes, and the
scoring guardrails.

```bash
python cag.py make-samples
```

Creates three synthetic submissions spanning the quality range — one strong and
evidenced, one middling process redesign, one thin idea — so scoring can be
checked for discrimination, not just for running. They are clearly marked
synthetic. Delete them before the real run.

---

## 11. Known limitations

- **Video and audio are not transcribed** unless ffmpeg and faster-whisper are
  installed and `extraction.enable_video_transcription` is set. Until then a
  submission with a demo video is scored on its written material only, and is
  flagged for review. Since Appendix-I offers a demo video as a primary
  attachment format, this matters — install ffmpeg before the real run.
- **Scanned PDFs need OCR**, which needs the Tesseract binary. Without it, a
  scanned form yields no text and the submission is flagged.
- **Legacy `.doc` and `.ppt` are not parsed.** Convert to `.docx`/`.pptx` or PDF.
- **A score reads the submission, not the initiative.** A strong initiative
  described poorly will score poorly. Where a committee has other knowledge of
  an initiative, that knowledge should prevail.
- **Formation routing** returns `unknown` rather than guessing when an office
  name matches no known pattern. Set those manually before relying on stream
  ranks.
- **The NAeG marker is a filter suggestion**, not a nomination recommendation.
