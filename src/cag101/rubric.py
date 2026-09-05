"""Rubric loading, validation and prompt rendering.

The rubric is data, not code. Weights come from the published scheme; the
sub-criteria decomposition is ours. Validation here exists to stop a typo in
YAML from silently producing scores that do not add to 100.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import PROJECT_ROOT, load_config

RUBRIC_DIR = PROJECT_ROOT / "rubrics"


@dataclass(frozen=True)
class SubCriterion:
    key: str
    name: str
    max: int
    question: str
    look_for: tuple[str, ...]
    red_flags: tuple[str, ...]
    notes: str | None


@dataclass(frozen=True)
class Criterion:
    key: str
    name: str
    weight: int
    scheme_focus: str
    sub_criteria: tuple[SubCriterion, ...]

    @property
    def max_points(self) -> int:
        return sum(s.max for s in self.sub_criteria)


@dataclass(frozen=True)
class Band:
    name: str
    lo: float
    hi: float
    definition: str


class Rubric:
    def __init__(self, data: dict[str, Any], source_path: Path):
        self._data = data
        self.source_path = source_path
        self.id: str = data["id"]
        self.version: str = data["version"]
        self.name: str = data["name"]
        self.authority: str = data.get("authority", "")
        self.total: int = int(data["total"])
        self.frozen: bool = bool(data.get("frozen", False))
        self.special_category_guidance: str = data.get("special_category_guidance", "")
        self.bands = tuple(
            Band(b["name"], float(b["lo"]), float(b["hi"]), b["definition"].strip())
            for b in data["bands"]
        )
        self.evidence_policy: dict[str, Any] = data.get("evidence_policy", {})
        self.criteria = tuple(
            Criterion(
                key=c["key"],
                name=c["name"],
                weight=int(c["weight"]),
                scheme_focus=c.get("scheme_focus", "").strip(),
                sub_criteria=tuple(
                    SubCriterion(
                        key=s["key"],
                        name=s["name"],
                        max=int(s["max"]),
                        question=s.get("question", "").strip(),
                        look_for=tuple(s.get("look_for", ())),
                        red_flags=tuple(s.get("red_flags", ())),
                        notes=(s.get("notes") or "").strip() or None,
                    )
                    for s in c["sub_criteria"]
                ),
            )
            for c in data["criteria"]
        )
        self.flags: tuple[dict[str, Any], ...] = tuple(data.get("flags", ()))
        self._validate()

    # -- identity -----------------------------------------------------------
    @property
    def slug(self) -> str:
        return f"{self.id}_{self.version}"

    @property
    def content_hash(self) -> str:
        """Hash of the rubric file. Any edit changes every score's provenance."""
        payload = yaml.safe_dump(self._data, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    # -- lookup -------------------------------------------------------------
    def criterion(self, key: str) -> Criterion:
        for c in self.criteria:
            if c.key == key:
                return c
        raise KeyError(f"No criterion {key!r} in rubric {self.slug}")

    @property
    def criterion_keys(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.criteria)

    def flag_spec(self, key: str) -> dict[str, Any] | None:
        for f in self.flags:
            if f.get("key") == key:
                return f
        return None

    # -- validation ---------------------------------------------------------
    def _validate(self) -> None:
        problems: list[str] = []

        weight_sum = sum(c.weight for c in self.criteria)
        if weight_sum != self.total:
            problems.append(
                f"criterion weights sum to {weight_sum}, expected total {self.total}"
            )

        for c in self.criteria:
            if c.max_points != c.weight:
                problems.append(
                    f"criterion {c.key!r}: sub-criteria max points sum to "
                    f"{c.max_points} but the criterion weight is {c.weight}"
                )
            if not c.sub_criteria:
                problems.append(f"criterion {c.key!r} has no sub-criteria")

        keys = [c.key for c in self.criteria]
        if len(set(keys)) != len(keys):
            problems.append("duplicate criterion keys")
        for c in self.criteria:
            sub_keys = [s.key for s in c.sub_criteria]
            if len(set(sub_keys)) != len(sub_keys):
                problems.append(f"duplicate sub-criterion keys in {c.key!r}")

        covered = sorted((b.lo, b.hi) for b in self.bands)
        if covered and (covered[0][0] > 0.0 or covered[-1][1] < 1.0):
            problems.append("bands do not span 0.0 to 1.0")

        if problems:
            raise ValueError(
                f"Invalid rubric {self.source_path.name}:\n  - " + "\n  - ".join(problems)
            )

    # -- prompt rendering ---------------------------------------------------
    def render_bands(self) -> str:
        lines = []
        for b in self.bands:
            lines.append(
                f"- {b.name} ({int(b.lo * 100)}-{int(b.hi * 100)}% of the "
                f"sub-criterion maximum): {b.definition}"
            )
        return "\n".join(lines)

    def render_criterion(self, key: str) -> str:
        """The rubric slice given to a single criterion agent.

        Each agent sees only its own criterion. This keeps one criterion's
        judgement from bleeding into another and makes disagreement between
        passes meaningful.
        """
        c = self.criterion(key)
        out: list[str] = [
            f"CRITERION: {c.name}",
            f"MAXIMUM FOR THIS CRITERION: {c.weight} points",
            f"SCHEME'S STATED ASSESSMENT FOCUS: {c.scheme_focus}",
            "",
            "SUB-CRITERIA — score each one independently:",
        ]
        for s in c.sub_criteria:
            out.append("")
            out.append(f"  [{s.key}] {s.name}  (0-{s.max} points)")
            out.append(f"    Question: {s.question}")
            if s.look_for:
                out.append("    Score higher when you find:")
                out.extend(f"      + {item}" for item in s.look_for)
            if s.red_flags:
                out.append("    Score lower when you find:")
                out.extend(f"      - {item}" for item in s.red_flags)
            if s.notes:
                out.append(f"    Note: {s.notes}")
        out += [
            "",
            "SCORING BANDS — place each sub-score in a band, then pick a point value "
            "inside that band's range:",
            self.render_bands(),
        ]
        return "\n".join(out)


@lru_cache(maxsize=8)
def load_rubric(slug: str | None = None) -> Rubric:
    """Load a rubric by slug (e.g. 'cag101_v1'). Defaults to the active one."""
    if slug is None:
        slug = load_config().require("rubric.active")
    path = RUBRIC_DIR / f"{slug}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in RUBRIC_DIR.glob("*.yaml"))
        raise FileNotFoundError(
            f"Rubric {slug!r} not found in {RUBRIC_DIR}. Available: {available}"
        )
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Rubric(data, path)


def available_rubrics() -> list[str]:
    return sorted(p.stem for p in RUBRIC_DIR.glob("*.yaml"))
