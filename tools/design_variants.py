"""Build three real HTML variants of the scorecards screen, side by side.

The gstack design-shotgun binary is not installed on this machine, so this is
its documented fallback path: real HTML wireframes instead of generated PNG
mockups. For an HTML portal that is the better artefact anyway — these use the
live database, the real Inter/violet tokens, and the actual remarks, so what
you judge is what you would ship.

Nothing here touches the running portal. Output is standalone files under
data/exports/design-variants/.

    python tools/design_variants.py
"""

from __future__ import annotations

import html
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cag101.db import session_scope  # noqa: E402
from cag101.reporting import (  # noqa: E402
    CRITERION_HUE,
    CRITERION_SHORT,
    FORMATION_LABELS,
    STAGE_LABELS,
    all_scorecards,
    rank_scorecards,
)
from cag101.rubric import load_rubric  # noqa: E402

OUT = ROOT / "data" / "exports" / "design-variants"
STATIC = ROOT / "src" / "cag101" / "web" / "static"


def esc(text: str) -> str:
    return html.escape(text or "")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def collect():
    rubric = load_rubric()
    with session_scope() as session:
        cards = all_scorecards(session, rubric, include_identity=False, include_evidence=False)
        rank_scorecards(cards)
    cards.sort(key=lambda c: (-(c.total if c.evaluated else -1), c.ref))
    rows = []
    for c in cards:
        scores = []
        for spec in rubric.criteria:
            v = c.criterion(spec.key)
            scores.append({
                "key": spec.key,
                "short": CRITERION_SHORT.get(spec.key, spec.name),
                "name": spec.name,
                "hue": CRITERION_HUE.get(spec.key, "innovation"),
                "max": spec.weight,
                "value": v.effective_score if (c.evaluated and v) else None,
            })
        badges = []
        if c.needs_review:
            badges.append(("warn", "review"))
        if c.status == "no_files_submitted":
            badges.append(("critical", "no files"))
        elif c.flags.get("completeness") == "incomplete":
            badges.append(("critical", "incomplete"))
        if c.flags.get("evidence_strength") in {"piloted_with_results", "deployed_with_data"}:
            badges.append(("good", "evidenced"))
        if c.flags.get("naeg_candidate_hint") == "true":
            badges.append(("grey", "NAeG?"))
        rows.append({
            "ref": c.ref,
            "title": c.title or c.folder_name,
            "stream": FORMATION_LABELS.get(c.formation_category, c.formation_category),
            "theme": c.thematic_area,
            "stage": STAGE_LABELS.get(c.development_stage, c.development_stage),
            "total": c.total if c.evaluated else None,
            "rank": getattr(c, "rank_overall", None),
            "remarks": c.remarks,
            "badges": badges,
            "flagged": c.needs_review,
            "scores": scores,
            "evaluated": c.evaluated,
        })
    return rubric, rows


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------
def page(title: str, note: str, variant_css: str, body: str, base_css: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en-IN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
{base_css}
/* ---- variant overrides ---- */
{variant_css}
</style></head>
<body>
<main style="padding: 1rem 1.1rem 2rem; max-width: none">
  <div class="vhead">
    <strong>{esc(title)}</strong>
    <span>{esc(note)}</span>
  </div>
  {body}
</main>
</body></html>
"""


VHEAD = """
.vhead {
  display: flex; align-items: baseline; gap: 0.6rem; flex-wrap: wrap;
  padding: 0.5rem 0.75rem; margin-bottom: 0.9rem;
  border-radius: var(--r-md); background: var(--p-100);
  border: 1px solid var(--p-200); font-size: var(--t-tiny);
}
.vhead strong { color: var(--p-700); font-size: var(--t-sm); }
.vhead span { color: var(--ink-2); }
body { background: var(--surface); }
"""


# ---------------------------------------------------------------------------
# Variant A — Ledger
# ---------------------------------------------------------------------------
A_CSS = VHEAD + """
/* Audit-register density: no card chrome, hairline rules, colour reduced to a
   column edge so the numbers carry the page. */
.table-wrap { border: 1px solid var(--line); border-radius: 4px; box-shadow: none; }
table { font-size: 12px; }
th, td { padding: 0.3rem 0.5rem; }
thead th { font-size: 10px; letter-spacing: 0.05em; background: #fff;
           border-bottom: 1.5px solid var(--ink-2); color: var(--ink-1); }
tbody tr { border-bottom: 1px solid var(--grid); }
tbody td { vertical-align: middle; }
.lg-title { font-weight: 550; }
.lg-meta { color: var(--ink-3); font-size: 10.5px; }
td.crit, th.crit { text-align: right; border-top: none;
                   border-left: 2px solid var(--k, var(--line)); padding-right: 0.55rem; }
td.crit .sv { font-size: 12.5px; font-weight: 640; font-variant-numeric: tabular-nums; }
.why-mini { border: none; background: none; color: var(--p-500); cursor: pointer;
            font-size: 10px; padding: 0 0 0 0.2rem; text-decoration: underline; }
td.total-cell { font-size: 13px; }
.lg-remark { color: var(--ink-2); font-size: 11px; line-height: 1.4;
             display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
             overflow: hidden; max-width: 340px; }
.badge { font-size: 9.5px; padding: 0.02rem 0.32rem; }
"""


def variant_a(rubric, rows) -> str:
    head = ['<th class="col-ref">Ref</th>', "<th>Submission</th>"]
    for spec in rubric.criteria:
        hue = CRITERION_HUE.get(spec.key, "innovation")
        head.append(
            f'<th class="crit" style="--k: var(--c-{hue})">'
            f'{esc(CRITERION_SHORT.get(spec.key, spec.name))}<br>'
            f'<span style="font-weight:400;color:var(--ink-3)">/{spec.weight}</span></th>'
        )
    head += ['<th class="num">Total</th>', "<th>Remarks</th>"]

    body = []
    for r in rows:
        cells = [
            f'<td class="col-ref nowrap"><a href="#">{esc(r["ref"])}</a></td>',
            f'<td><span class="lg-title">{esc(r["title"])}</span>'
            f'<div class="lg-meta">{esc(r["stream"])}'
            + (f' · {esc(r["theme"])}' if r["theme"] != "unclear" else "")
            + "".join(
                f' <span class="badge {c}">{esc(t)}</span>' for c, t in r["badges"]
            )
            + "</div></td>",
        ]
        for s in r["scores"]:
            v = f'{s["value"]:.1f}' if s["value"] is not None else "&mdash;"
            cells.append(
                f'<td class="crit" style="--k: var(--c-{s["hue"]})">'
                f'<span class="sv">{v}</span>'
                + ('<button class="why-mini">why</button>' if s["value"] is not None else "")
                + "</td>"
            )
        cells.append(
            f'<td class="num total-cell">{r["total"]:.1f}</td>'
            if r["total"] is not None
            else '<td class="num"><span class="muted">&mdash;</span></td>'
        )
        cells.append(f'<td><div class="lg-remark">{esc(r["remarks"])}</div></td>')
        body.append(
            f'<tr class="{"flagged" if r["flagged"] else ""}">' + "".join(cells) + "</tr>"
        )

    return (
        '<div class="table-wrap"><table><thead><tr>'
        + "".join(head)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


# ---------------------------------------------------------------------------
# Variant B — Dossier
# ---------------------------------------------------------------------------
B_CSS = VHEAD + """
/* One submission per panel. Scores as a small bar group; remarks get room to
   actually be read. */
.dossier { display: grid; gap: 0.9rem; }
.ds {
  border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface);
  box-shadow: var(--sh-1); padding: 0.95rem 1.1rem;
  display: grid; grid-template-columns: 1fr 250px; gap: 1.1rem;
}
.ds.flagged { border-left: 3px solid var(--st-warn); }
.ds .hd { display: flex; justify-content: space-between; gap: 1rem; align-items: baseline; }
.ds .ref { font-size: var(--t-micro); text-transform: uppercase; letter-spacing: 0.07em;
           color: var(--ink-3); font-weight: 620; }
.ds .ti { font-size: var(--t-md); font-weight: 620; margin: 0.15rem 0 0.3rem; }
.ds .meta { font-size: var(--t-tiny); color: var(--ink-3); }
.ds .rk { margin: 0.65rem 0 0; font-size: var(--t-sm); color: var(--ink-2); line-height: 1.55; }
.ds .tot { text-align: right; }
.ds .tot .n { font-size: 2rem; font-weight: 680; color: var(--p-600); line-height: 1;
              letter-spacing: -0.03em; }
.ds .tot .of { font-size: var(--t-tiny); color: var(--ink-3); }
.sgroup { display: grid; gap: 0.4rem; }
.srow { display: grid; grid-template-columns: 74px 1fr 44px; align-items: center; gap: 0.5rem; }
.srow .sl { font-size: var(--t-micro); color: var(--ink-2); text-align: right; }
.srow .st { background: var(--grid); border-radius: 3px; height: 9px; overflow: hidden; }
.srow .sf { height: 100%; border-radius: 0 3px 3px 0; }
.srow .sn { font-size: var(--t-tiny); font-weight: 620; text-align: right;
            font-variant-numeric: tabular-nums; }
@media (max-width: 900px) { .ds { grid-template-columns: 1fr; } }
"""


def variant_b(rubric, rows) -> str:
    out = ['<div class="dossier">']
    for r in rows:
        bars = []
        for s in r["scores"]:
            pct = (s["value"] / s["max"] * 100) if s["value"] is not None else 0
            val = f'{s["value"]:.1f}' if s["value"] is not None else "&mdash;"
            bars.append(
                f'<div class="srow"><div class="sl">{esc(s["short"])}</div>'
                f'<div class="st"><div class="sf" style="width:{pct:.0f}%;'
                f'background:var(--c-{s["hue"]})"></div></div>'
                f'<div class="sn">{val}</div></div>'
            )
        total = (
            f'<div class="n">{r["total"]:.1f}</div><div class="of">of 100</div>'
            if r["total"] is not None
            else '<div class="of">not scored</div>'
        )
        out.append(
            f'<div class="ds {"flagged" if r["flagged"] else ""}">'
            "<div>"
            f'<div class="ref">{esc(r["ref"])}'
            + (f' · rank {r["rank"]}' if r["rank"] else "")
            + "</div>"
            f'<div class="ti">{esc(r["title"])}</div>'
            f'<div class="meta">{esc(r["stream"])} · {esc(r["stage"])}'
            + "".join(f' <span class="badge {c}">{esc(t)}</span>' for c, t in r["badges"])
            + "</div>"
            f'<p class="rk">{esc(r["remarks"])}</p>'
            "</div>"
            f'<div><div class="tot">{total}</div>'
            f'<div class="sgroup" style="margin-top:0.7rem">' + "".join(bars) + "</div></div>"
            "</div>"
        )
    out.append("</div>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Variant C — Cockpit
# ---------------------------------------------------------------------------
C_CSS = VHEAD + """
/* Table kept, but the four scores collapse into one stacked meter and the
   prose leaves the grid. */
.table-wrap { box-shadow: var(--sh-1); }
th, td { padding: 0.5rem 0.7rem; }
.ck-meter { display: flex; gap: 2px; height: 13px; width: 210px; }
.ck-seg { border-radius: 2px; background: var(--grid); position: relative;
          overflow: hidden; flex: 1; }
.ck-seg > i { position: absolute; inset: 0 auto 0 0; display: block; border-radius: 2px; }
.ck-nums { display: flex; gap: 2px; width: 210px; margin-top: 0.22rem; }
.ck-nums span { flex: 1; text-align: center; font-size: 10.5px; font-weight: 620;
                font-variant-numeric: tabular-nums; color: var(--ink-2); }
.ck-legend { display: flex; gap: 2px; width: 210px; }
.ck-legend span { flex: 1; text-align: center; font-size: 9px; text-transform: uppercase;
                  letter-spacing: 0.04em; color: var(--ink-3); font-weight: 620; }
.rk-btn { border: 1px solid var(--line); background: var(--surface); color: var(--p-500);
          border-radius: var(--r-full); padding: 0.14rem 0.6rem; font-size: 10.5px;
          font-weight: 620; cursor: pointer; white-space: nowrap; }
.rk-btn:hover { background: var(--p-500); color: #fff; border-color: var(--p-500); }
.drawer { margin-top: 0.4rem; font-size: var(--t-sm); color: var(--ink-2);
          line-height: 1.55; max-width: 460px; display: none; }
.drawer.open { display: block; }
"""


def variant_c(rubric, rows) -> str:
    legend = "".join(
        f'<span>{esc(CRITERION_SHORT.get(s.key, s.name))[:4]}</span>' for s in rubric.criteria
    )
    body = []
    for r in rows:
        segs, nums = [], []
        for s in r["scores"]:
            pct = (s["value"] / s["max"] * 100) if s["value"] is not None else 0
            segs.append(
                f'<div class="ck-seg"><i style="width:{pct:.0f}%;'
                f'background:var(--c-{s["hue"]})"></i></div>'
            )
            nums.append(
                f'<span>{s["value"]:.1f}</span>' if s["value"] is not None else "<span>—</span>"
            )
        total = f'{r["total"]:.1f}' if r["total"] is not None else "&mdash;"
        body.append(
            f'<tr class="{"flagged" if r["flagged"] else ""}">'
            f'<td class="col-ref nowrap"><a href="#">{esc(r["ref"])}</a>'
            + (f'<div class="tiny muted">rank {r["rank"]}</div>' if r["rank"] else "")
            + "</td>"
            f"<td><strong>{esc(r['title'])}</strong>"
            f'<div class="tiny muted" style="margin-top:0.15rem">{esc(r["stream"])} · {esc(r["stage"])}'
            + "".join(f' <span class="badge {c}">{esc(t)}</span>' for c, t in r["badges"])
            + "</div>"
            f'<div class="drawer">{esc(r["remarks"])}</div></td>'
            f'<td><div class="ck-meter">{"".join(segs)}</div>'
            f'<div class="ck-nums">{"".join(nums)}</div>'
            f'<div class="ck-legend">{legend}</div></td>'
            f'<td class="num total-cell">{total}</td>'
            '<td><button class="rk-btn" onclick="this.closest(\'tr\')'
            ".querySelector('.drawer').classList.toggle('open')\">Remarks</button></td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        '<th class="col-ref">Ref</th><th>Submission</th>'
        "<th>Four criteria</th><th class=\"num\">Total</th><th></th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
    )


# ---------------------------------------------------------------------------
# Comparison board
# ---------------------------------------------------------------------------
BOARD = """<!DOCTYPE html>
<html lang="en-IN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scorecards — design comparison</title>
<style>
  @font-face {{ font-family:"InterVar"; src:url("./inter.woff2") format("woff2");
               font-weight:100 900; font-display:swap; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:"InterVar",system-ui,sans-serif; background:#f6f4fb;
          color:#171320; font-size:14px; }}
  header {{ background:linear-gradient(104deg,#4c1d95,#6321c4 42%,#c026d3);
            color:#fff; padding:0.9rem 1.4rem; }}
  header h1 {{ margin:0; font-size:1rem; font-weight:640; }}
  header p {{ margin:0.2rem 0 0; font-size:0.75rem; opacity:0.8; }}
  .note {{ margin:1rem 1.4rem; padding:0.55rem 0.85rem; border-radius:10px;
           background:#fdf3dd; border:1px solid #f2ddb0; color:#8a5a00;
           font-size:0.78rem; }}
  .grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:1rem;
           padding:0 1.4rem 1.4rem; }}
  @media (max-width:1400px) {{ .grid {{ grid-template-columns:1fr; }} }}
  .col {{ background:#fff; border:1px solid #e5e1ee; border-radius:14px;
          overflow:hidden; box-shadow:0 1px 3px rgba(23,19,32,.07); }}
  .col > h2 {{ margin:0; padding:0.7rem 0.9rem 0.5rem; font-size:0.9rem; font-weight:650; }}
  .col > p {{ margin:0; padding:0 0.9rem 0.7rem; font-size:0.74rem; color:#4a4458;
              line-height:1.5; border-bottom:1px solid #e5e1ee; }}
  .col .verdict {{ padding:0.5rem 0.9rem; font-size:0.72rem; color:#4a4458;
                   background:#faf8fd; border-top:1px solid #e5e1ee; }}
  iframe {{ width:100%; height:760px; border:none; display:block; }}
  a.open {{ display:inline-block; margin:0 0 0 0.9rem; font-size:0.72rem; color:#7c3aed; }}
</style></head>
<body>
<header>
  <h1>Scorecards — three directions, same nine submissions</h1>
  <p>CAG 101 Evaluation Portal · design comparison · nothing here is live</p>
</header>
<div class="note">
  <strong>These are throwaway files.</strong> The running portal at 127.0.0.1:8137 is
  untouched. Each panel is real HTML using the live database, the real Inter/violet
  tokens and the actual remarks — so what you pick is what ships, not a picture of it.
  Scroll inside a panel; open one full-width with the link under it.
</div>
<div class="grid">
  {cols}
</div>
</body></html>
"""

COL = """
  <div class="col">
    <h2>{letter} &mdash; {name}</h2>
    <p>{blurb}</p>
    <iframe src="./{file}" title="{name}"></iframe>
    <div class="verdict">{verdict}</div>
    <a class="open" href="./{file}" target="_blank">Open full width &rarr;</a>
  </div>
"""


def main() -> int:
    rubric, rows = collect()
    OUT.mkdir(parents=True, exist_ok=True)

    base_css = (STATIC / "style.css").read_text(encoding="utf-8")
    # standalone files: point the font at a sibling copy
    base_css = base_css.replace(
        'url("/static/fonts/inter-latin-wght-normal.woff2")', 'url("./inter.woff2")'
    )
    shutil.copyfile(STATIC / "fonts" / "inter-latin-wght-normal.woff2", OUT / "inter.woff2")

    specs = [
        ("A", "Ledger", "variant-a-ledger.html", A_CSS, variant_a,
         "Audit-register density. No card chrome, 12px tabular type, criterion colour "
         "cut to a 2px column edge.",
         "Roughly twice the rows per screen. Remarks clamp to two lines and the colour "
         "system goes almost silent."),
        ("B", "Dossier", "variant-b-dossier.html", B_CSS, variant_b,
         "One submission per panel. Four scores as a bar group, remarks as body copy "
         "with room to breathe.",
         "Most legible per submission by a distance. About four per screen, so ranking "
         "400 would be slow."),
        ("C", "Cockpit", "variant-c-cockpit.html", C_CSS, variant_c,
         "Your table, refined. The four scores collapse into one stacked meter; remarks "
         "move to a per-row drawer.",
         "Keeps what people already know and recovers about a third of the row height. "
         "Least adventurous of the three."),
    ]

    cols = []
    for letter, name, fname, css, builder, blurb, verdict in specs:
        note = {
            "A": "maximum rows on screen",
            "B": "maximum legibility per submission",
            "C": "current layout, prose removed from the grid",
        }[letter]
        (OUT / fname).write_text(
            page(f"Variant {letter} — {name}", note, css, builder(rubric, rows), base_css),
            encoding="utf-8",
        )
        cols.append(COL.format(letter=letter, name=name, file=fname,
                               blurb=blurb, verdict=verdict))
        print(f"  {letter}  {fname}  ({(OUT / fname).stat().st_size // 1024} KB)")

    board = OUT / "design-board.html"
    board.write_text(BOARD.format(cols="".join(cols)), encoding="utf-8")
    print(f"\nboard: {board}")
    print(f"rows rendered: {len(rows)}  (evaluated: {sum(1 for r in rows if r['evaluated'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
