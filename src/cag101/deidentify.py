"""De-identification applied before any content reaches a model.

Two separate jobs, deliberately not conflated:

1. **Privacy redaction** — emails, phone numbers, and ID-shaped numbers are
   removed from text wherever they appear, including inside attachments. If an
   Aadhaar-shaped number turns up it is redacted and flagged, because CAG
   submission material should not contain citizen identifiers at all and PPG
   should know if it does.

2. **Blind review** — submitter names and office/state are masked so that
   scoring cannot favour a person or a formation. This is a fairness control,
   not a privacy one, and it is what makes the eventual scores defensible if a
   committee asks whether the machine preferred certain offices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- privacy patterns ------------------------------------------------------
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# Indian mobile: optional +91/0 prefix, then 6-9 followed by 9 digits.
MOBILE_RE = re.compile(r"(?<!\d)(?:\+?91[-\s]?|0)?[6-9]\d{9}(?!\d)")

# Aadhaar-shaped: 12 digits, commonly written in 4-4-4 groups.
AADHAAR_RE = re.compile(r"(?<!\d)[2-9]\d{3}[-\s]?\d{4}[-\s]?\d{4}(?!\d)")

# PAN: AAAAA9999A
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")

# Bank account-ish: 11-18 consecutive digits.
LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{11,18}(?!\d)")

EMAIL_TOKEN = "[EMAIL REDACTED]"
MOBILE_TOKEN = "[MOBILE REDACTED]"
AADHAAR_TOKEN = "[ID NUMBER REDACTED]"
PAN_TOKEN = "[PAN REDACTED]"
LONG_NUMBER_TOKEN = "[LONG NUMBER REDACTED]"
NAME_TOKEN = "[NAME WITHHELD]"
OFFICE_TOKEN = "[OFFICE WITHHELD]"

# Honorifics and rank words that precede names in departmental writing. Used
# only to avoid masking a rank when the name itself is masked.
_TITLE_WORDS = {
    "shri", "smt", "sh", "ms", "mr", "mrs", "dr", "sri",
    "ao", "sao", "aao", "dag", "ag", "pag", "pd", "dg", "adai", "dai",
}


@dataclass
class RedactionReport:
    emails: int = 0
    mobiles: int = 0
    id_numbers: int = 0
    pans: int = 0
    long_numbers: int = 0
    names_masked: int = 0
    offices_masked: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def id_number_detected(self) -> bool:
        return self.id_numbers > 0

    @property
    def total(self) -> int:
        return (
            self.emails
            + self.mobiles
            + self.id_numbers
            + self.pans
            + self.long_numbers
            + self.names_masked
            + self.offices_masked
        )

    def merge(self, other: "RedactionReport") -> None:
        self.emails += other.emails
        self.mobiles += other.mobiles
        self.id_numbers += other.id_numbers
        self.pans += other.pans
        self.long_numbers += other.long_numbers
        self.names_masked += other.names_masked
        self.offices_masked += other.offices_masked
        self.notes.extend(other.notes)

    def summary(self) -> str:
        parts = []
        for label, count in (
            ("email", self.emails),
            ("mobile", self.mobiles),
            ("ID number", self.id_numbers),
            ("PAN", self.pans),
            ("long number", self.long_numbers),
            ("name", self.names_masked),
            ("office mention", self.offices_masked),
        ):
            if count:
                parts.append(f"{count} {label}{'s' if count != 1 else ''}")
        return ", ".join(parts) if parts else "nothing redacted"


def redact_privacy(text: str) -> tuple[str, RedactionReport]:
    """Strip contact details and ID-shaped numbers. Order matters: Aadhaar
    before the generic long-number rule, mobile after Aadhaar so a 12-digit
    string is not mistaken for a 10-digit one."""
    report = RedactionReport()
    if not text:
        return text, report

    def _sub(pattern: re.Pattern[str], token: str, body: str) -> tuple[str, int]:
        matches = pattern.findall(body)
        return (pattern.sub(token, body), len(matches))

    text, report.emails = _sub(EMAIL_RE, EMAIL_TOKEN, text)
    text, report.pans = _sub(PAN_RE, PAN_TOKEN, text)
    text, report.id_numbers = _sub(AADHAAR_RE, AADHAAR_TOKEN, text)
    text, report.mobiles = _sub(MOBILE_RE, MOBILE_TOKEN, text)
    text, report.long_numbers = _sub(LONG_NUMBER_RE, LONG_NUMBER_TOKEN, text)

    if report.id_numbers:
        report.notes.append(
            "An Aadhaar-shaped number was found and redacted. Submission material "
            "should not contain citizen identifiers — flag to PPG Wing."
        )
    return text, report


def _name_variants(full_name: str) -> list[str]:
    """Match a person's name as written and by surname alone, since departmental
    text refers to the same person both ways."""
    name = (full_name or "").strip()
    if len(name) < 3:
        return []
    variants = {name}
    parts = [p for p in re.split(r"\s+", name) if p.lower().strip(".") not in _TITLE_WORDS]
    parts = [p for p in parts if len(p) >= 3]
    if len(parts) > 1:
        variants.add(" ".join(parts))
        variants.add(parts[-1])          # surname
        variants.add(parts[0])           # given name
    elif parts:
        variants.add(parts[0])
    # Longest first, so "Rajesh Kumar Singh" masks before "Singh".
    return sorted(variants, key=len, reverse=True)


def mask_names(text: str, names: list[str]) -> tuple[str, int]:
    if not text:
        return text, 0
    masked = 0
    for name in names:
        for variant in _name_variants(name):
            pattern = re.compile(rf"\b{re.escape(variant)}\b", re.IGNORECASE)
            text, count = pattern.subn(NAME_TOKEN, text)
            masked += count
    return text, masked


def mask_office(text: str, office: str) -> tuple[str, int]:
    """Mask the office string and its distinctive fragments.

    Deliberately conservative: only fragments of 4+ characters that are not
    generic departmental words, so masking "O/o the AG (Audit), Kerala" does not
    also delete every occurrence of the word "Audit".
    """
    if not text or not office or len(office.strip()) < 4:
        return text, 0

    generic = {
        "office", "o/o", "the", "and", "audit", "accounts", "account", "general",
        "principal", "director", "deputy", "wing", "department", "india", "state",
        "union", "government", "govt", "cag", "iaad", "ia&ad", "sai", "branch",
    }
    masked = 0
    text, count = re.compile(re.escape(office.strip()), re.IGNORECASE).subn(
        OFFICE_TOKEN, text
    )
    masked += count

    fragments = [
        f.strip()
        for f in re.split(r"[,/()\-–—]|\s{2,}", office)
        if len(f.strip()) >= 4 and f.strip().lower() not in generic
    ]
    for frag in sorted(fragments, key=len, reverse=True):
        if frag.lower() in generic:
            continue
        text, count = re.compile(rf"\b{re.escape(frag)}\b", re.IGNORECASE).subn(
            OFFICE_TOKEN, text
        )
        masked += count
    return text, masked


def deidentify(
    text: str,
    names: list[str] | None = None,
    office: str | None = None,
    blind_office: bool = True,
) -> tuple[str, RedactionReport]:
    """Full pass: privacy redaction, then blind-review masking."""
    text, report = redact_privacy(text)
    if names:
        text, report.names_masked = mask_names(text, names)
    if blind_office and office:
        text, report.offices_masked = mask_office(text, office)
    return text, report
