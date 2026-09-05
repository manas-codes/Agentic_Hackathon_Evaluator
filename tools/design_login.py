"""Three login-screen directions for the CAG 101 portal, side by side.

Reference supplied by the user: a centred white card on a soft photographic
field, serif wordmark, filled inputs, solid dark primary button, remember-me
and forgot-password affordances.

The gstack design binary is not installed, so this is the skill's documented
HTML-wireframe fallback. Each file is standalone and offline: no CDN, no
external image, the typeface is the self-hosted Inter copy.

The emblem loads from ./cag-emblem.png next to the output files. Until that
file exists a placeholder mark renders in its place, so layout and spacing are
judged correctly either way.

    python tools/design_login.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "exports" / "design-variants" / "login"
STATIC = ROOT / "src" / "cag101" / "web" / "static"

# The emblem slot. If the PNG is absent the <object> falls through to the mark.
EMBLEM = """
<span class="emblem" style="--sz: {size}px">
  <img src="./cag-emblem.png" alt="Indian Audit and Accounts Department"
       onerror="this.style.display='none';this.nextElementSibling.style.display='grid'">
  <span class="emblem-fallback" style="display:none">IA&amp;AD</span>
</span>
"""

EMBLEM_CSS = """
.emblem { display: inline-block; width: var(--sz); height: var(--sz); }
.emblem img { width: 100%; height: 100%; object-fit: contain; display: block; }
.emblem-fallback {
  width: 100%; height: 100%; border-radius: 50%;
  place-items: center; font-size: calc(var(--sz) / 4.6); font-weight: 700;
  letter-spacing: 0.02em; border: 2px solid currentColor; opacity: 0.55;
}
"""

SHELL = """<!DOCTYPE html>
<html lang="en-IN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
@font-face {{ font-family:"InterVar"; src:url("../inter.woff2") format("woff2");
             font-weight:100 900; font-display:swap; }}
* {{ box-sizing:border-box; }}
html,body {{ height:100%; }}
body {{ margin:0; font-family:"InterVar",system-ui,-apple-system,"Segoe UI",sans-serif;
        -webkit-font-smoothing:antialiased; }}
.vtag {{ position:fixed; top:0; left:0; right:0; z-index:99;
         background:#171320; color:#fff; font-size:11px; padding:5px 12px;
         letter-spacing:0.04em; }}
.vtag b {{ font-weight:650; }}
.vtag span {{ opacity:0.66; }}
{emblem_css}
{css}
</style></head>
<body>
<div class="vtag"><b>{letter} &mdash; {name}</b> <span>{note}</span></div>
{body}
</body></html>
"""

# ---------------------------------------------------------------------------
# A — State Emblem: the reference layout, warm and official. No violet.
# ---------------------------------------------------------------------------
A_CSS = """
body {
  display:grid; place-items:center; padding:3rem 1.25rem 2rem;
  background:
    radial-gradient(1100px 520px at 50% -8%, #ffffff 0%, rgba(255,255,255,0) 60%),
    radial-gradient(760px 460px at 88% 108%, #efe6d6 0%, rgba(239,230,214,0) 66%),
    linear-gradient(178deg, #f3efe7 0%, #e9e3d8 100%);
  color:#2b2b33;
}
.card {
  width:100%; max-width:430px; background:#fff; border-radius:22px;
  padding:2.4rem 2.2rem 1.9rem; text-align:center;
  border:1px solid #e6e0d4;
  box-shadow:0 1px 2px rgba(43,43,51,.04), 0 22px 48px rgba(43,43,51,.10);
}
.emblem { color:#8a7f66; }
.wordmark {
  font-family:Georgia,"Times New Roman",serif;
  font-size:1.62rem; font-weight:400; letter-spacing:-0.012em;
  margin:0.85rem 0 0.1rem; color:#1f1f26;
}
.dept {
  font-size:9.5px; text-transform:uppercase; letter-spacing:0.19em;
  color:#8a7f66; font-weight:650; margin-bottom:1.5rem;
}
h1 { font-size:1.12rem; font-weight:620; margin:0 0 0.3rem; letter-spacing:-0.014em; }
.lede { font-size:0.8rem; color:#6d6a63; margin:0 0 1.6rem; line-height:1.55; }
form { text-align:left; }
label { display:block; font-size:0.75rem; font-weight:560; color:#4a463f; margin-bottom:0.3rem; }
label .req { color:#b03030; }
input[type=text],input[type=password] {
  width:100%; padding:0.66rem 0.8rem; font:inherit; font-size:0.86rem;
  background:#f7f5f0; border:1px solid #e2dcd0; border-radius:11px; color:#2b2b33;
}
input::placeholder { color:#a8a294; }
input:focus-visible { outline:none; border-color:#b4a88c; box-shadow:0 0 0 3px rgba(180,168,140,.28); }
.field { margin-bottom:1rem; }
.row { display:flex; justify-content:space-between; align-items:center; margin:0.1rem 0 1.3rem; }
.row label { margin:0; font-size:0.76rem; font-weight:450; color:#565249;
             display:flex; align-items:center; gap:0.4rem; }
.row a { font-size:0.76rem; color:#7a6f56; text-decoration:underline; }
button {
  width:100%; padding:0.76rem; border:none; border-radius:11px;
  background:#2b2b33; color:#fff; font:inherit; font-size:0.9rem; font-weight:600;
  cursor:pointer; transition:background 140ms ease;
}
button:hover { background:#3d3d47; }
.foot { font-size:10.5px; color:#8c887f; margin:1.35rem 0 0; line-height:1.65; }
.scheme { font-size:0.76rem; color:#6d6a63; margin:1.1rem 0 0; padding-top:1rem;
          border-top:1px solid #efeade; }
"""

A_BODY = """
<div class="card">
  {emblem}
  <div class="wordmark">Evaluation Portal</div>
  <div class="dept">Indian Audit &amp; Accounts Department</div>

  <h1>Sign in to continue</h1>
  <p class="lede">CAG 101 Innovation Ideas Initiative &middot; PPG Wing</p>

  <form onsubmit="return false">
    <div class="field">
      <label for="a1">Username<span class="req">*</span></label>
      <input id="a1" type="text" placeholder="e.g. ppg.admin">
    </div>
    <div class="field">
      <label for="a2">Password<span class="req">*</span></label>
      <input id="a2" type="password" placeholder="Type your password">
    </div>
    <div class="row">
      <label><input type="checkbox" style="width:auto"> Remember this device</label>
      <a href="#">Forgot password?</a>
    </div>
    <button type="submit">Sign in</button>
  </form>

  <p class="scheme">Award presentation on Audit Diwas &middot; 16 November 2026</p>
  <p class="foot">For internal departmental use. Access is logged. Submission
     material is confidential.</p>
</div>
"""

# ---------------------------------------------------------------------------
# B — Split Ledger: violet panel + form. Reuses the shipped design system.
# ---------------------------------------------------------------------------
B_CSS = """
body { display:grid; grid-template-columns:56% 44%; min-height:100vh; color:#171320; }
.panel {
  background:linear-gradient(150deg,#4c1d95 0%,#6321c4 46%,#c026d3 100%);
  color:#fff; padding:3.2rem 3rem 2.4rem; display:flex; flex-direction:column;
  justify-content:space-between; position:relative; overflow:hidden;
}
.panel::after {
  content:""; position:absolute; width:520px; height:520px; right:-180px; bottom:-200px;
  border-radius:50%; background:radial-gradient(circle,rgba(255,255,255,.16),transparent 68%);
}
.panel .emblem-chip {
  display:inline-grid; place-items:center; background:#fff; border-radius:14px;
  padding:0.6rem 0.7rem; box-shadow:0 2px 10px rgba(23,19,32,.18);
}
.panel h1 { font-size:2.05rem; font-weight:660; letter-spacing:-0.03em;
            line-height:1.14; margin:1.5rem 0 0.6rem; max-width:12ch; }
.panel .sub { font-size:0.88rem; opacity:0.8; line-height:1.6; max-width:34ch; }
.facts { display:flex; gap:2.4rem; margin-top:2.4rem; position:relative; }
.facts div { }
.facts .n { font-size:1.5rem; font-weight:660; letter-spacing:-0.02em; }
.facts .l { font-size:10px; text-transform:uppercase; letter-spacing:0.09em;
            opacity:0.7; font-weight:620; margin-top:0.2rem; }
.panel .adv { font-size:11px; opacity:0.68; line-height:1.6; max-width:46ch;
              position:relative; }
.formside { background:#fff; display:grid; place-items:center; padding:2.5rem 2rem; }
.inner { width:100%; max-width:340px; }
.kicker { font-size:10px; text-transform:uppercase; letter-spacing:0.11em;
          color:#827b91; font-weight:660; }
.inner h2 { font-size:1.35rem; font-weight:650; letter-spacing:-0.022em;
            margin:0.35rem 0 0.4rem; }
.inner p.l { font-size:0.8rem; color:#4a4458; margin:0 0 1.7rem; }
label { display:block; font-size:10px; text-transform:uppercase; letter-spacing:0.07em;
        color:#827b91; font-weight:660; margin-bottom:0.32rem; }
input { width:100%; padding:0.62rem 0.72rem; font:inherit; font-size:0.86rem;
        border:1px solid #e5e1ee; border-radius:8px; background:#fff; color:#171320; }
input:focus-visible { outline:none; border-color:#9d7cf8; box-shadow:0 0 0 3px rgba(124,58,237,.22); }
.field { margin-bottom:0.95rem; }
button { width:100%; padding:0.66rem; border:1px solid #7c3aed; border-radius:8px;
         background:#7c3aed; color:#fff; font:inherit; font-size:0.88rem; font-weight:620;
         cursor:pointer; margin-top:0.35rem; transition:background 130ms ease, transform 130ms ease; }
button:hover { background:#8149f2; transform:translateY(-1px); }
.help { display:flex; justify-content:space-between; align-items:center; margin-top:0.9rem; }
.help a { font-size:0.76rem; color:#7c3aed; }
.foot { font-size:10.5px; color:#827b91; margin-top:1.7rem; line-height:1.6; }
@media (max-width:900px) {
  body { grid-template-columns:1fr; }
  .panel { padding:2rem 1.5rem; }
  .panel h1 { font-size:1.6rem; }
  .facts { gap:1.5rem; }
}
"""

B_BODY = """
<div class="panel">
  <div>
    <span class="emblem-chip">{emblem}</span>
    <h1>CAG 101 Innovation Ideas</h1>
    <p class="sub">Structured evaluation of innovation submissions from across the
       offices of SAI India, for the Filtering and Empowered Committees.</p>
    <div class="facts">
      <div><div class="n">9</div><div class="l">Submissions in</div></div>
      <div><div class="n">4</div><div class="l">Evaluation streams</div></div>
      <div><div class="n">16 Nov</div><div class="l">Audit Diwas 2026</div></div>
    </div>
  </div>
  <p class="adv">Scores in this portal are an AI-assisted pre-assessment against
     the rubric published in the scheme. They are decision-support for the
     Committees, not a decision.</p>
</div>

<div class="formside">
  <div class="inner">
    <div class="kicker">PPG Wing &middot; O/o CAG of India</div>
    <h2>Sign in</h2>
    <p class="l">Use the credentials issued to you by the portal administrator.</p>
    <form onsubmit="return false">
      <div class="field">
        <label for="b1">Username</label>
        <input id="b1" type="text" placeholder="e.g. ppg.admin">
      </div>
      <div class="field">
        <label for="b2">Password</label>
        <input id="b2" type="password" placeholder="&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;">
      </div>
      <button type="submit">Sign in</button>
      <div class="help">
        <label style="text-transform:none;letter-spacing:0;font-weight:450;
                      color:#4a4458;font-size:0.78rem;display:flex;
                      align-items:center;gap:0.4rem;margin:0">
          <input type="checkbox" style="width:auto"> Keep me signed in
        </label>
        <a href="#">Need access?</a>
      </div>
    </form>
    <p class="foot">Access is logged. Do not share exports outside the
       evaluation process.</p>
  </div>
</div>
"""

# ---------------------------------------------------------------------------
# C — Seal: no card, print-restrained, one amber accent.
# ---------------------------------------------------------------------------
C_CSS = """
body { display:grid; place-items:center; min-height:100vh; background:#fbfaf8;
       color:#1c1a17; padding:3rem 1.25rem; }
.wrap { width:100%; max-width:352px; text-align:center; }
.emblem { color:#3d3a34; }
.wordmark { font-size:11px; text-transform:uppercase; letter-spacing:0.3em;
            font-weight:680; margin:1.4rem 0 0.4rem; line-height:1.9; }
.wordmark span { display:block; font-size:10px; letter-spacing:0.22em;
                 font-weight:560; color:#8c877d; }
.rule { height:2px; width:44px; background:#c8951f; margin:1.35rem auto 1.9rem; }
h1 { font-size:0.94rem; font-weight:620; margin:0 0 2rem; letter-spacing:-0.008em; }
form { text-align:left; }
.field { margin-bottom:1.55rem; }
label { display:block; font-size:9.5px; text-transform:uppercase; letter-spacing:0.13em;
        color:#8c877d; font-weight:680; margin-bottom:0.45rem; }
input { width:100%; padding:0.3rem 0 0.5rem; font:inherit; font-size:0.95rem;
        border:none; border-bottom:1.5px solid #ddd8cd; background:transparent;
        color:#1c1a17; border-radius:0; }
input::placeholder { color:#c2bcaf; }
input:focus { outline:none; border-bottom-color:#c8951f; }
button { width:100%; padding:0.68rem; margin-top:0.6rem; border:1.5px solid #1c1a17;
         background:transparent; color:#1c1a17; font:inherit; font-size:0.8rem;
         font-weight:680; letter-spacing:0.09em; text-transform:uppercase;
         cursor:pointer; border-radius:2px; transition:background 140ms ease, color 140ms ease; }
button:hover { background:#1c1a17; color:#fbfaf8; }
.meta { display:flex; justify-content:center; gap:1.1rem; margin-top:1.5rem; }
.meta a { font-size:0.74rem; color:#8c877d; text-decoration:none;
          border-bottom:1px solid #ddd8cd; padding-bottom:1px; }
.meta a:hover { color:#1c1a17; border-bottom-color:#c8951f; }
.foot { font-size:10px; color:#a39d92; margin:2.4rem 0 0; line-height:1.75;
        letter-spacing:0.01em; }
"""

C_BODY = """
<div class="wrap">
  {emblem}
  <div class="wordmark">
    Evaluation Portal
    <span>Comptroller and Auditor General of India</span>
  </div>
  <div class="rule"></div>
  <h1>CAG 101 Innovation Ideas Initiative</h1>

  <form onsubmit="return false">
    <div class="field">
      <label for="c1">Username</label>
      <input id="c1" type="text" placeholder="">
    </div>
    <div class="field">
      <label for="c2">Password</label>
      <input id="c2" type="password" placeholder="">
    </div>
    <button type="submit">Sign in</button>
  </form>

  <div class="meta">
    <a href="#">Need access</a>
    <a href="#">Contact PPG Wing</a>
  </div>

  <p class="foot">For internal departmental use.<br>
     Access is logged &middot; Submission material is confidential.</p>
</div>
"""

BOARD = """<!DOCTYPE html>
<html lang="en-IN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Login — design comparison</title>
<style>
  @font-face {{ font-family:"InterVar"; src:url("../inter.woff2") format("woff2");
               font-weight:100 900; font-display:swap; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:"InterVar",system-ui,sans-serif; background:#f6f4fb;
          color:#171320; font-size:14px; }}
  header {{ background:linear-gradient(104deg,#4c1d95,#6321c4 42%,#c026d3);
            color:#fff; padding:0.95rem 1.4rem; }}
  header h1 {{ margin:0; font-size:1rem; font-weight:640; }}
  header p {{ margin:0.22rem 0 0; font-size:0.76rem; opacity:0.82; }}
  .note {{ margin:1rem 1.4rem; padding:0.6rem 0.9rem; border-radius:10px;
           background:#fdf3dd; border:1px solid #f2ddb0; color:#8a5a00;
           font-size:0.79rem; line-height:1.6; }}
  .note code {{ background:#fff; padding:0.05rem 0.3rem; border-radius:4px; }}
  .grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:1rem;
           padding:0 1.4rem 1.6rem; }}
  @media (max-width:1500px) {{ .grid {{ grid-template-columns:1fr; }} }}
  .col {{ background:#fff; border:1px solid #e5e1ee; border-radius:14px;
          overflow:hidden; box-shadow:0 1px 3px rgba(23,19,32,.07); }}
  .col h2 {{ margin:0; padding:0.75rem 0.95rem 0.4rem; font-size:0.92rem; font-weight:650; }}
  .col .why {{ margin:0; padding:0 0.95rem 0.75rem; font-size:0.76rem; color:#4a4458;
               line-height:1.55; border-bottom:1px solid #e5e1ee; }}
  .col .verdict {{ padding:0.6rem 0.95rem; font-size:0.74rem; color:#4a4458;
                   background:#faf8fd; border-top:1px solid #e5e1ee; line-height:1.55; }}
  iframe {{ width:100%; height:720px; border:none; display:block; background:#fff; }}
  a.open {{ display:inline-block; margin:0.5rem 0 0.7rem 0.95rem; font-size:0.74rem;
            color:#7c3aed; }}
</style></head>
<body>
<header>
  <h1>Login screen &mdash; three directions</h1>
  <p>CAG 101 Evaluation Portal &middot; built from your reference layout &middot; nothing here is live</p>
</header>
<div class="note">
  <strong>Emblem slot.</strong> Save the CAG emblem PNG as
  <code>data/exports/design-variants/login/cag-emblem.png</code> and refresh &mdash;
  all three pick it up. Until then a placeholder ring renders, so spacing still reads true.
  <br><strong>Emblem resolution.</strong> The official file on cag.gov.in is only 176&times;222, so the rim lettering is illegible much above 110px and soft on a high-DPI screen. Worth asking PPG Wing for a vector or high-resolution master.
  <br><strong>Note on two controls</strong> from your reference: &ldquo;Forgot password&rdquo;
  and &ldquo;Remember me&rdquo; do not exist in the portal yet &mdash; an admin resets
  passwords and sessions run 8 hours. They are drawn in A and B so you can judge the
  layout, but each needs building before it can ship as a live link.
</div>
<div class="grid">
{cols}
</div>
</body></html>
"""

COL = """
  <div class="col">
    <h2>{letter} &mdash; {name}</h2>
    <p class="why">{why}</p>
    <iframe src="./{file}" title="{name}"></iframe>
    <div class="verdict">{verdict}</div>
    <a class="open" href="./{file}" target="_blank">Open full width &rarr;</a>
  </div>
"""

SPECS = [
    dict(
        letter="A", name="State Emblem", file="login-a-state-emblem.html",
        note="closest to your reference · warm, official, no violet",
        css=A_CSS, body=A_BODY, emblem_size=108,
        why="Your reference layout, translated: centred white card on a warm stone "
            "field, emblem large at the top, a serif wordmark, filled inputs and a "
            "solid dark button.",
        verdict="Reads unmistakably as a government institution and needs no new "
                "typeface — the serif is the system Georgia stack, so it works "
                "offline. Cost: it abandons the violet system you just approved, so "
                "login and the rest of the portal would feel like two products.",
    ),
    dict(
        letter="B", name="Split Ledger", file="login-b-split-ledger.html",
        note="reuses the shipped design system · violet",
        css=B_CSS, body=B_BODY, emblem_size=62,
        why="Two panels. Left carries the emblem, the scheme and three quiet facts "
            "on the portal's violet gradient; right is a plain white form.",
        verdict="Continuous with the portal behind it, and the left panel does real "
                "work — it tells a first-time evaluator what this is before they "
                "sign in. The emblem sits on a white chip rather than being inverted, "
                "so it stays as issued. Cost: the most conventional of the three.",
    ),
    dict(
        letter="C", name="Seal", file="login-c-seal.html",
        note="no card · print-restrained · one amber accent",
        css=C_CSS, body=C_BODY, emblem_size=118,
        why="No card at all. Emblem centred on near-white, letterspaced wordmark, "
            "underlined fields, an outlined button and a single amber rule.",
        verdict="The most dignified and the closest to departmental print. The emblem "
                "carries the page rather than competing with a card. Cost: underlined "
                "fields are a weaker click target than filled boxes, and the restraint "
                "may read as unfinished to anyone expecting a modern portal.",
    ),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    font_src = STATIC / "fonts" / "inter-latin-wght-normal.woff2"
    shutil.copyfile(font_src, OUT.parent / "inter.woff2")

    cols = []
    for s in SPECS:
        emblem = EMBLEM.format(size=s["emblem_size"])
        html_out = SHELL.format(
            title=f'{s["letter"]} — {s["name"]}',
            letter=s["letter"], name=s["name"], note=s["note"],
            emblem_css=EMBLEM_CSS, css=s["css"],
            body=s["body"].format(emblem=emblem),
        )
        (OUT / s["file"]).write_text(html_out, encoding="utf-8")
        cols.append(COL.format(letter=s["letter"], name=s["name"], file=s["file"],
                               why=s["why"], verdict=s["verdict"]))
        print(f'  {s["letter"]}  {s["file"]}  ({(OUT / s["file"]).stat().st_size // 1024} KB)')

    board = OUT / "login-board.html"
    board.write_text(BOARD.format(cols="".join(cols)), encoding="utf-8")
    print(f"\nboard: {board}")
    emblem = OUT / "cag-emblem.png"
    if emblem.exists():
        print(f"emblem: {emblem.name} present ({emblem.stat().st_size // 1024} KB)")
    else:
        print(f"emblem: {emblem} MISSING — placeholder mark renders instead")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
