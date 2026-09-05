"""Mapping an office name to one of the four evaluation streams.

The Concept Paper routes submissions to different Filtering Committees — Union,
States, Others, and Special Category States (§IV). Getting this wrong routes a
submission to the wrong committee, so the mapping is explicit and conservative:
anything it cannot place with confidence comes back as UNKNOWN for a human to
set, rather than being guessed into a stream.
"""

from __future__ import annotations

import re

from .schema import FormationCategory

# Concept Paper §IV, footnote 1
SPECIAL_CATEGORY_STATES = (
    "arunachal pradesh",
    "jammu & kashmir",
    "jammu and kashmir",
    "j&k",
    "manipur",
    "meghalaya",
    "mizoram",
    "nagaland",
    "sikkim",
    "tripura",
)

OTHER_STATES = (
    "andhra pradesh", "assam", "bihar", "chhattisgarh", "goa", "gujarat",
    "haryana", "himachal pradesh", "jharkhand", "karnataka", "kerala",
    "madhya pradesh", "maharashtra", "odisha", "orissa", "punjab", "rajasthan",
    "tamil nadu", "telangana", "uttar pradesh", "uttarakhand", "west bengal",
    "delhi", "puducherry", "pondicherry", "ladakh", "chandigarh",
    "andaman", "lakshadweep", "dadra", "daman",
)

# Union-side formations: central ministries, railways, defence, revenue, etc.
UNION_MARKERS = (
    "railway", "defence", "defense", "ordnance", "posts", "telecom",
    "direct taxes", "indirect taxes", "customs", "central excise", "gst",
    "dgacr", "dgade", "dgadd", "central receipt", "central expenditure",
    # Directors General of Audit head the Union-side commercial and sectoral
    # audit formations (Steel, Coal, Petroleum, Shipping and others), so a DGA
    # office is Union side even where the city is in a State.
    "dga", "director general of audit", "principal director of audit",
    "steel", "sail", "mining", "shipping", "aviation", "energy",
    "union government", "civil aviation", "petroleum", "coal", "atomic",
    "scientific department", "commercial audit", "psu", "public sector",
    "autonomous bodies", "central board",
)

# "Other formations": training institutes, HQ wings, and support organisations.
OTHER_MARKERS = (
    "iced", "international centre for environment audit",
    "naaa", "national academy of audit and accounts",
    "rti ", "regional training institute", "regional training centre",
    "training institute", "training centre", "academy",
    "headquarters", "hq", "o/o cag", "office of the cag", "office of cag",
    "ppg", "is wing", "information systems wing", "staff wing",
    "sai india hq", "comptroller and auditor general of india",
)


def _normalise(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("&", " & ")
    return re.sub(r"\s+", " ", text).strip()


def classify_formation(office: str) -> tuple[FormationCategory, str]:
    """Return the stream and a one-line reason for the classification."""
    norm = _normalise(office)
    if not norm:
        return FormationCategory.UNKNOWN, "No office recorded."

    for state in SPECIAL_CATEGORY_STATES:
        if state in norm:
            return (
                FormationCategory.SPECIAL_CATEGORY_STATE,
                f"Office names a Special Category State ({state.title()}).",
            )

    # Union markers are checked before generic state names, because an office
    # such as "PDA (Railways), Chennai" is a Union formation despite the city.
    for marker in UNION_MARKERS:
        if marker in norm:
            return FormationCategory.UNION, f"Union-side marker found ({marker!r})."

    for marker in OTHER_MARKERS:
        if marker in norm:
            return FormationCategory.OTHER, f"Other-formation marker found ({marker!r})."

    for state in OTHER_STATES:
        if state in norm:
            return FormationCategory.STATE, f"Office names a State ({state.title()})."

    return (
        FormationCategory.UNKNOWN,
        "Office name did not match any known formation pattern — set manually.",
    )


def is_special_category(office: str) -> bool:
    return classify_formation(office)[0] is FormationCategory.SPECIAL_CATEGORY_STATE
