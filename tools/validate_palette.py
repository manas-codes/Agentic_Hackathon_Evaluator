"""Palette validator for the portal's charts.

Colour choices in a scorecard dashboard are not decoration: if two adjacent
segments of a chart are indistinguishable to a colour-blind reader, the chart
has silently lost information for roughly one man in twelve. So the palette is
checked arithmetically rather than judged by eye.

Checks applied
--------------
1. Categorical series (the four criteria): every pair must separate by
   CIEDE2000 dE >= 15 under normal vision and dE >= 8 under each of the three
   dichromacies (protanopia, deuteranopia, tritanopia).
2. Ordinal / diverging ramps (the score bands): lightness must be monotone,
   adjacent steps must differ by dL >= 0.06 in relative luminance terms, and
   the lightest step must still reach 2:1 contrast against the surface it is
   drawn on, so a pale segment is not invisible.
3. Any colour carrying text or sitting on white gets a WCAG contrast figure
   reported, so a "relief rule" decision (add a visible label) is taken on
   evidence rather than assumed.

Run:  python tools/validate_palette.py
Exit code 0 = every declared check passed.
"""

from __future__ import annotations

import math
import sys

# ---------------------------------------------------------------------------
# Colour conversion
# ---------------------------------------------------------------------------


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _linearise(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(hexcolour: str) -> float:
    r, g, b = (_linearise(c) for c in hex_to_rgb(hexcolour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


# sRGB -> XYZ (D65) -> CIELAB
_M = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)
_WHITE = (0.95047, 1.00000, 1.08883)


def to_lab(hexcolour: str) -> tuple[float, float, float]:
    rgb = [_linearise(c) for c in hex_to_rgb(hexcolour)]
    xyz = [sum(_M[i][j] * rgb[j] for j in range(3)) / _WHITE[i] for i in range(3)]

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + (16 / 116)

    fx, fy, fz = (f(v) for v in xyz)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e_2000(c1: str, c2: str) -> float:
    """CIEDE2000. Perceptual difference; 1.0 is roughly a just-noticeable step."""
    l1, a1, b1 = to_lab(c1)
    l2, a2, b2 = to_lab(c2)

    kl = kc = kh = 1.0
    c1v = math.hypot(a1, b1)
    c2v = math.hypot(a2, b2)
    c_bar = (c1v + c2v) / 2
    g = 0.5 * (1 - math.sqrt(c_bar**7 / (c_bar**7 + 25**7))) if c_bar else 0.0

    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (a1p or b1) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (a2p or b2) else 0.0

    dlp = l2 - l1
    dcp = c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    else:
        dhp = h2p - h1p - 360 if h2p > h1p else h2p - h1p + 360
    dhp_term = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp) / 2)

    lp_bar = (l1 + l2) / 2
    cp_bar = (c1p + c2p) / 2
    if c1p * c2p == 0:
        hp_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hp_bar = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hp_bar = (h1p + h2p + 360) / 2
    else:
        hp_bar = (h1p + h2p - 360) / 2

    t = (
        1
        - 0.17 * math.cos(math.radians(hp_bar - 30))
        + 0.24 * math.cos(math.radians(2 * hp_bar))
        + 0.32 * math.cos(math.radians(3 * hp_bar + 6))
        - 0.20 * math.cos(math.radians(4 * hp_bar - 63))
    )
    d_theta = 30 * math.exp(-(((hp_bar - 275) / 25) ** 2))
    rc = 2 * math.sqrt(cp_bar**7 / (cp_bar**7 + 25**7)) if cp_bar else 0.0
    sl = 1 + (0.015 * (lp_bar - 50) ** 2) / math.sqrt(20 + (lp_bar - 50) ** 2)
    sc = 1 + 0.045 * cp_bar
    sh = 1 + 0.015 * cp_bar * t
    rt = -math.sin(math.radians(2 * d_theta)) * rc

    return math.sqrt(
        (dlp / (kl * sl)) ** 2
        + (dcp / (kc * sc)) ** 2
        + (dhp_term / (kh * sh)) ** 2
        + rt * (dcp / (kc * sc)) * (dhp_term / (kh * sh))
    )


# ---------------------------------------------------------------------------
# Colour-vision-deficiency simulation (Viénot, Brettel & Mollon 1999)
# ---------------------------------------------------------------------------
_CVD = {
    "protanopia": (
        (0.170556992, 0.829443014, 0.0),
        (0.170556991, 0.829443008, 0.0),
        (-0.004517144, 0.004517144, 1.0),
    ),
    "deuteranopia": (
        (0.33066007, 0.66933993, 0.0),
        (0.33066007, 0.66933993, 0.0),
        (-0.02785538, 0.02785538, 1.0),
    ),
    "tritanopia": (
        (1.0, 0.1273989, -0.1273989),
        (0.0, 0.8739093, 0.1260907),
        (0.0, 0.8739093, 0.1260907),
    ),
}
# LMS <-> linear sRGB (Hunt-Pointer-Estevez, D65 normalised)
_RGB2LMS = (
    (0.31399022, 0.63951294, 0.04649755),
    (0.15537241, 0.75789446, 0.08670142),
    (0.01775239, 0.10944209, 0.87256922),
)
_LMS2RGB = (
    (5.47221206, -4.6419601, 0.16963708),
    (-1.1252419, 2.29317094, -0.1678952),
    (0.02980165, -0.19318073, 1.16364789),
)


def _mul(matrix, vec):
    return [sum(matrix[i][j] * vec[j] for j in range(3)) for i in range(3)]


def simulate_cvd(hexcolour: str, kind: str) -> str:
    rgb = [_linearise(c) for c in hex_to_rgb(hexcolour)]
    lms = _mul(_RGB2LMS, rgb)
    lms = _mul(_CVD[kind], lms)
    out = _mul(_LMS2RGB, lms)

    def gamma(c: float) -> int:
        c = max(0.0, min(1.0, c))
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return int(round(max(0.0, min(1.0, c)) * 255))

    return "#%02x%02x%02x" % tuple(gamma(c) for c in out)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
CAT_MIN_NORMAL = 15.0
CAT_MIN_CVD = 8.0
RAMP_MIN_DL = 0.06
RAMP_MIN_CONTRAST = 2.0

failures: list[str] = []
notes: list[str] = []


def check_categorical(name: str, series: dict[str, str]) -> None:
    print(f"\n== categorical: {name} ==")
    keys = list(series)
    worst_normal = (999.0, "")
    worst_cvd = (999.0, "", "")
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = series[keys[i]], series[keys[j]]
            d = delta_e_2000(a, b)
            if d < worst_normal[0]:
                worst_normal = (d, f"{keys[i]}/{keys[j]}")
            if d < CAT_MIN_NORMAL:
                failures.append(
                    f"{name}: {keys[i]} vs {keys[j]} dE {d:.1f} < {CAT_MIN_NORMAL}"
                )
            for kind in _CVD:
                dc = delta_e_2000(simulate_cvd(a, kind), simulate_cvd(b, kind))
                if dc < worst_cvd[0]:
                    worst_cvd = (dc, f"{keys[i]}/{keys[j]}", kind)
                if dc < CAT_MIN_CVD:
                    failures.append(
                        f"{name}: {keys[i]} vs {keys[j]} dE {dc:.1f} < "
                        f"{CAT_MIN_CVD} under {kind}"
                    )
    print(f"  worst normal-vision dE  {worst_normal[0]:6.1f}  ({worst_normal[1]})")
    print(
        f"  worst dichromat dE      {worst_cvd[0]:6.1f}  "
        f"({worst_cvd[1]}, {worst_cvd[2]})"
    )
    for key, colour in series.items():
        ratio = contrast_ratio(colour, "#ffffff")
        flag = "" if ratio >= 3.0 else "  <- relief rule: needs a visible label"
        print(f"  {key:<12} {colour}  on white {ratio:4.2f}:1{flag}")
        if ratio < 3.0:
            notes.append(f"{name}/{key} at {ratio:.2f}:1 requires a visible number")


def check_ramp(name: str, steps: list[str], surface: str = "#ffffff") -> None:
    print(f"\n== ramp: {name} ==")
    lums = [relative_luminance(s) for s in steps]
    direction = 1 if lums[0] > lums[-1] else -1
    for i, (colour, lum) in enumerate(zip(steps, lums)):
        ratio = contrast_ratio(colour, surface)
        line = f"  step {i + 1}  {colour}  L {lum:.4f}  vs surface {ratio:4.2f}:1"
        if i:
            dl = abs(lums[i] - lums[i - 1])
            line += f"  dL {dl:.4f}"
            if dl < RAMP_MIN_DL:
                failures.append(
                    f"{name}: steps {i}-{i + 1} dL {dl:.4f} < {RAMP_MIN_DL}"
                )
            if direction * (lums[i - 1] - lums[i]) <= 0:
                failures.append(f"{name}: lightness not monotone at step {i + 1}")
        if ratio < RAMP_MIN_CONTRAST:
            failures.append(
                f"{name}: step {i + 1} {colour} only {ratio:.2f}:1 against surface"
            )
        print(line)
    # adjacent steps must also survive dichromacy, or the ring reads as one block
    for kind in _CVD:
        worst = min(
            delta_e_2000(simulate_cvd(steps[i], kind), simulate_cvd(steps[i + 1], kind))
            for i in range(len(steps) - 1)
        )
        status = "ok" if worst >= CAT_MIN_CVD else "FAIL"
        print(f"  adjacent under {kind:<13} worst dE {worst:5.1f}  {status}")
        if worst < CAT_MIN_CVD:
            failures.append(f"{name}: adjacent dE {worst:.1f} under {kind}")


# ---------------------------------------------------------------------------
# The portal's actual palette
# ---------------------------------------------------------------------------
# The four criteria are nominal categories, so four distinct hues rather than a
# ramp. This exact set was chosen by exhaustive search over 15 bright candidate
# hues: it is the brightest 4-hue set containing the scheme's emerald that
# clears dE 8 under all three dichromacies (violet + blue, the earlier pair,
# collapses to dE 3.1 under deuteranopia). Red was available and scored higher
# but is reserved here for status, so a criterion is never coloured like a
# failure.
CRITERIA = {
    "innovation": "#059669",   # emerald
    "relevance": "#db2777",    # magenta
    "feasibility": "#f59e0b",  # amber
    "impact": "#2563eb",       # blue
}

# Score bands are ordered, so a sequential ramp — a categorical rainbow would
# imply the bands are unrelated, and a diverging ramp cannot hold monotone
# lightness (both ends dark) which is what makes an ordered scale readable in
# greyscale. This ramp drifts teal -> emerald -> forest as it darkens: the hue
# drift is what carries it past dE 8 under red-green deficiency, where a strict
# single-hue emerald ramp only reached 7.6.
BANDS = [
    "#2ac6b6",  # below 50
    "#1eaa8e",  # 50-59
    "#148d68",  # 60-69
    "#0c6d46",  # 70-79
    "#064b29",  # 80 and above
]

if __name__ == "__main__":
    check_categorical("criteria", CRITERIA)
    check_ramp("score bands", BANDS)

    print("\n" + "=" * 68)
    if notes:
        print("Notes (not failures — handled by showing the number):")
        for note in notes:
            print(f"  - {note}")
    if failures:
        print(f"FAILED {len(failures)} check(s):")
        for failure in failures:
            print(f"  - {failure}")
        sys.exit(1)
    print("All declared checks passed.")
