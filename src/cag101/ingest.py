"""Ingestion: the submissions folder becomes rows in the database.

Layout expected (mirrors the Google Drive structure):

    data/submissions/
        <Submission folder 1>/
            Filled Appendix-I form.pdf
            Deck.pptx                  (optional)
            Demo.mp4                   (optional)
        <Submission folder 2>/
            ...

One folder is one submission. A loose file at the top level is also accepted as
a single-file submission, since some offices submit only the form.

Ingestion is idempotent: a submission is keyed on a content hash over its files,
so re-running skips unchanged folders and re-extracts changed ones.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ensure_directories, load_config
from .extract import (
    ExtractedDoc,
    extract_file,
    load_cached_extraction,
    save_extraction,
)
from .models import Flag, Submission, SubmissionFile

IGNORED_NAMES = {
    ".ds_store", "thumbs.db", "desktop.ini", ".gitkeep", "icon\r",
}
IGNORED_PREFIXES = ("~$", "._")   # Office lock files, macOS resource forks


@dataclass
class IngestResult:
    ref: str
    folder_name: str
    files_found: int
    files_extracted: int
    chars_extracted: int
    status: str          # ingested | unchanged | empty
    warnings: list[str]

    def line(self) -> str:
        warn = f"  [{len(self.warnings)} warning(s)]" if self.warnings else ""
        return (
            f"{self.ref}  {self.status:<9}  {self.files_extracted}/{self.files_found} files, "
            f"{self.chars_extracted:>7,} chars  {self.folder_name}{warn}"
        )


def _is_ignored(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in IGNORED_NAMES
        or name.startswith(IGNORED_PREFIXES)
        or path.name.startswith(".")
    )


def _collect_files(root: Path) -> list[Path]:
    """All usable files under a submission folder, recursively and sorted."""
    files = [
        p for p in sorted(root.rglob("*"))
        if p.is_file() and not _is_ignored(p) and p.stat().st_size > 0
    ]
    return files


def compute_content_hash(files: list[tuple[str, str]]) -> str:
    """Hash over (relative path, file digest) pairs — changes if anything changes."""
    h = hashlib.sha256()
    for rel, digest in sorted(files):
        h.update(rel.encode("utf-8"))
        h.update(digest.encode("utf-8"))
    return h.hexdigest()


def discover_submissions() -> tuple[list[tuple[str, Path, list[Path]]], list[str]]:
    """Find submission units in the configured submissions folder.

    Returns (units, empty_folder_names) where a unit is
    (folder_name, root_for_relative_paths, files).

    Empty folders are reported rather than skipped. A submission folder that
    arrived with no files in it is a finding PPG needs to act on — the office
    may have uploaded nothing, or the download may have failed. Dropping it
    silently would make the intake count wrong.
    """
    ensure_directories()
    base = load_config().path("paths.submissions")
    units: list[tuple[str, Path, list[Path]]] = []
    empty: list[str] = []

    for entry in sorted(base.iterdir()):
        if _is_ignored(entry):
            continue
        if entry.is_dir():
            files = _collect_files(entry)
            if files:
                units.append((entry.name, entry, files))
            else:
                empty.append(entry.name)
        elif entry.is_file() and entry.stat().st_size > 0:
            # A single loose file is its own submission; relative paths are
            # taken against the base folder so the filename is preserved.
            units.append((entry.stem, base, [entry]))
    return units, empty


REF_PREFIX = "CAG101-"


def _next_ref(session: Session) -> str:
    """Sequential, stable, human-quotable reference. Assigned once and kept.

    Derived from the highest reference already issued, not from a row count.
    A count breaks the moment any submission is deleted: with rows 0004-0012
    present the count is 9, so the next reference would be 0010 — one that has
    already been given out — and the insert fails on the unique constraint.
    A reference is an identifier, so it is never reissued and never reused.
    """
    highest = 0
    for ref in session.scalars(select(Submission.ref)):
        if ref and ref.startswith(REF_PREFIX):
            suffix = ref[len(REF_PREFIX):]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"{REF_PREFIX}{highest + 1:04d}"


def ingest_all(session: Session, force: bool = False) -> tuple[list[IngestResult], list[str]]:
    """Ingest every submission folder. Idempotent unless force is set.

    Empty folders are still recorded as submissions, with status
    `no_files_submitted`, so they appear in the intake count and on the
    dashboard as needing PPG follow-up.
    """
    units, empty = discover_submissions()
    results: list[IngestResult] = []
    for folder_name, root, files in units:
        results.append(ingest_one(session, folder_name, root, files, force=force))
    for folder_name in empty:
        results.append(_register_empty(session, folder_name))
    return results, empty


def _register_empty(session: Session, folder_name: str) -> IngestResult:
    """Record a submission folder that contains no files at all."""
    base = load_config().path("paths.submissions")
    submission = session.scalar(
        select(Submission).where(Submission.folder_name == folder_name)
    )
    detail = (
        "The submission folder contains no files. Either the office uploaded "
        "nothing, or the download from Drive was incomplete. This submission "
        "cannot be evaluated until the material is supplied — refer to PPG Wing."
    )
    if submission is None:
        submission = Submission(
            ref=_next_ref(session),
            folder_name=folder_name,
            folder_path=str(base / folder_name),
            content_hash=compute_content_hash([]),
            status="no_files_submitted",
            status_detail=detail,
        )
        session.add(submission)
        session.flush()
    else:
        submission.status = "no_files_submitted"
        submission.status_detail = detail
        for existing in list(submission.files):
            session.delete(existing)
    session.flush()

    _set_ingest_flag(
        session, submission.id, "completeness", "incomplete",
        "No files were submitted at all.",
    )
    _set_ingest_flag(
        session, submission.id, "human_review_required", "true", detail,
    )

    return IngestResult(
        ref=submission.ref,
        folder_name=folder_name,
        files_found=0,
        files_extracted=0,
        chars_extracted=0,
        status="no files",
        warnings=[detail],
    )


def _set_ingest_flag(
    session: Session, submission_id: int, key: str, value: str, detail: str = ""
) -> None:
    existing = session.scalar(
        select(Flag).where(Flag.submission_id == submission_id, Flag.key == key)
    )
    if existing:
        existing.value = value
        existing.detail = detail
    else:
        session.add(
            Flag(submission_id=submission_id, key=key, value=value, detail=detail)
        )


def ingest_one(
    session: Session,
    folder_name: str,
    root: Path,
    files: list[Path],
    force: bool = False,
) -> IngestResult:
    warnings: list[str] = []

    # Hash first, so an unchanged folder costs one stat + digest per file and
    # no parsing at all.
    file_digests: list[tuple[str, str]] = []
    for path in files:
        from .extract import sha256_file

        rel = str(path.relative_to(root)).replace("\\", "/")
        file_digests.append((rel, sha256_file(path)))
    content_hash = compute_content_hash(file_digests)

    submission = session.scalar(
        select(Submission).where(Submission.folder_name == folder_name)
    )

    if submission and submission.content_hash == content_hash and not force:
        cached = load_cached_extraction(submission.ref, content_hash)
        chars = sum(d.char_count for d in cached) if cached else 0
        return IngestResult(
            ref=submission.ref,
            folder_name=folder_name,
            files_found=len(files),
            files_extracted=sum(1 for d in (cached or []) if d.status == "ok"),
            chars_extracted=chars,
            status="unchanged",
            warnings=[],
        )

    if submission is None:
        submission = Submission(
            ref=_next_ref(session),
            folder_name=folder_name,
            folder_path=str(root if root.name == folder_name else root / folder_name),
            content_hash=content_hash,
            status="ingested",
        )
        session.add(submission)
        session.flush()
    else:
        submission.content_hash = content_hash
        submission.status = "ingested"
        submission.status_detail = "Folder contents changed; re-extracted."
        # Replace the file inventory rather than merging — files may be removed.
        for existing in list(submission.files):
            session.delete(existing)
        session.flush()

    # Extract (or reuse a cache that matches this exact hash).
    docs = load_cached_extraction(submission.ref, content_hash)
    if docs is None or force:
        docs = [extract_file(p, root) for p in files]
        save_extraction(submission.ref, content_hash, docs)

    for doc in docs:
        session.add(
            SubmissionFile(
                submission_id=submission.id,
                name=Path(doc.relative_path).name,
                relative_path=doc.relative_path,
                kind=doc.kind,
                mime=doc.mime,
                size_bytes=doc.size_bytes,
                sha256=doc.sha256,
                extraction_status=doc.status,
                extraction_detail=doc.detail,
                char_count=doc.char_count,
                ocr_used=doc.ocr_used,
                media_derived=doc.media_derived,
            )
        )

    warnings.extend(_extraction_warnings(docs))

    chars = sum(d.char_count for d in docs)
    if chars == 0:
        submission.status = "extraction_failed"
        submission.status_detail = (
            "No text could be extracted from any file in this submission. "
            + " ".join(warnings)
        )
        status = "empty"
    else:
        submission.status = "extracted"
        submission.status_detail = "; ".join(warnings)
        status = "ingested"

    session.flush()

    return IngestResult(
        ref=submission.ref,
        folder_name=folder_name,
        files_found=len(files),
        files_extracted=sum(1 for d in docs if d.status == "ok"),
        chars_extracted=chars,
        status=status,
        warnings=warnings,
    )


def _extraction_warnings(docs: list[ExtractedDoc]) -> list[str]:
    warnings: list[str] = []
    for doc in docs:
        if doc.status in {"unsupported", "error"}:
            warnings.append(f"{doc.relative_path}: {doc.detail or doc.status}")
        elif doc.status == "empty":
            warnings.append(f"{doc.relative_path}: no extractable text. {doc.detail}")
        elif doc.status == "skipped" and doc.kind in {"video", "audio"}:
            warnings.append(f"{doc.relative_path}: media not transcribed.")
        elif doc.ocr_used:
            warnings.append(f"{doc.relative_path}: OCR used — verify accuracy.")
    return warnings


def submission_text(docs: list[ExtractedDoc], max_chars: int | None = None) -> str:
    """Concatenate extracted text with clear provenance markers.

    Ordering puts the form first — it is the authoritative document — then
    supporting material. The scorer needs to know which file a quote came from,
    so every block is labelled.
    """
    order = {"form": 0, "note": 1, "doc": 2, "workflow": 3, "deck": 4,
             "sheet": 5, "text": 6, "video": 7, "audio": 8, "image": 9, "other": 10}
    ordered = sorted(docs, key=lambda d: (order.get(d.kind, 99), d.relative_path))

    blocks: list[str] = []
    for doc in ordered:
        if not doc.text.strip():
            continue
        header = f"===== SOURCE: {doc.relative_path} (kind: {doc.kind}"
        if doc.media_derived:
            header += ", machine transcript — may contain transcription errors"
        if doc.ocr_used:
            header += ", OCR text — may contain recognition errors"
        header += ") ====="
        blocks.append(f"{header}\n{doc.text.strip()}")

    text = "\n\n".join(blocks)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... truncated for length ...]"
    return text
