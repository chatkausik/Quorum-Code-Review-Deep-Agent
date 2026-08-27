"""Presentation layer for the Streamlit UI: palette, CSS, and inline artwork."""

from __future__ import annotations

import base64
import html

SEVERITY_STYLE: dict[str, dict[str, str]] = {
    "critical": {"color": "#F4606C", "soft": "rgba(244,96,108,.14)", "label": "CRITICAL"},
    "high": {"color": "#F0913E", "soft": "rgba(240,145,62,.14)", "label": "HIGH"},
    "medium": {"color": "#E3BC3F", "soft": "rgba(227,188,63,.14)", "label": "MEDIUM"},
    "low": {"color": "#8A94A6", "soft": "rgba(138,148,166,.14)", "label": "LOW"},
}

CATEGORY_META = {
    "security": {"icon": "🔒", "label": "security"},
    "correctness": {"icon": "⚙️", "label": "correctness"},
    "tests": {"icon": "🧪", "label": "tests"},
}


def _svg_data_uri(svg: str) -> str:
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


# Inline artwork — a magnifier sweeping a code window, echoing what the agent
# does. Embedded as a data URI so the page stays self-contained.
_HERO_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 170" width="280" height="170">
  <defs>
    <linearGradient id="win" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#2B3648"/><stop offset="100%" stop-color="#1B2233"/>
    </linearGradient>
    <linearGradient id="glass" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#7FD4C1" stop-opacity=".95"/>
      <stop offset="100%" stop-color="#4FA3C7" stop-opacity=".95"/>
    </linearGradient>
  </defs>
  <rect x="28" y="22" width="196" height="124" rx="11" fill="url(#win)"
        stroke="rgba(255,255,255,.14)"/>
  <circle cx="44" cy="37" r="4" fill="#F4606C"/>
  <circle cx="58" cy="37" r="4" fill="#E3BC3F"/>
  <circle cx="72" cy="37" r="4" fill="#7FD4C1"/>
  <g fill="rgba(255,255,255,.30)">
    <rect x="44" y="56" width="74" height="6" rx="3"/>
    <rect x="126" y="56" width="44" height="6" rx="3"/>
    <rect x="44" y="72" width="52" height="6" rx="3"/>
    <rect x="44" y="104" width="62" height="6" rx="3"/>
    <rect x="114" y="104" width="38" height="6" rx="3"/>
    <rect x="44" y="120" width="88" height="6" rx="3"/>
  </g>
  <rect x="40" y="84" width="150" height="12" rx="4" fill="rgba(244,96,108,.22)"/>
  <rect x="40" y="84" width="3" height="12" rx="1.5" fill="#F4606C"/>
  <rect x="52" y="87" width="96" height="6" rx="3" fill="rgba(244,96,108,.75)"/>
  <g transform="translate(163,86)">
    <circle r="35" fill="url(#glass)" fill-opacity=".18" stroke="url(#glass)"
            stroke-width="5"/>
    <line x1="25" y1="25" x2="49" y2="49" stroke="url(#glass)" stroke-width="8"
          stroke-linecap="round"/>
  </g>
</svg>
"""

HERO_IMAGE = _svg_data_uri(_HERO_SVG)

_EMPTY_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 190 120" width="190" height="120">
  <rect x="30" y="18" width="130" height="84" rx="9" fill="none"
        stroke="rgba(140,150,170,.42)" stroke-width="2" stroke-dasharray="7 6"/>
  <g fill="rgba(140,150,170,.34)">
    <rect x="46" y="40" width="60" height="6" rx="3"/>
    <rect x="46" y="56" width="88" height="6" rx="3"/>
    <rect x="46" y="72" width="42" height="6" rx="3"/>
  </g>
  <g transform="translate(126,74)">
    <circle r="20" fill="none" stroke="rgba(140,150,170,.55)" stroke-width="4"/>
    <line x1="14" y1="14" x2="28" y2="28" stroke="rgba(140,150,170,.55)"
          stroke-width="5" stroke-linecap="round"/>
  </g>
</svg>
"""

EMPTY_IMAGE = _svg_data_uri(_EMPTY_SVG)

_CLEAN_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 150 110" width="150" height="110">
  <circle cx="75" cy="55" r="38" fill="rgba(127,212,193,.13)"
          stroke="rgba(127,212,193,.55)" stroke-width="3"/>
  <path d="M56 56 l13 14 l26 -30" fill="none" stroke="#7FD4C1" stroke-width="7"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

CLEAN_IMAGE = _svg_data_uri(_CLEAN_SVG)

CSS = """
<style>
  .block-container { padding-top: 1.6rem; max-width: 1200px; }

  /* ---------- hero ---------- */
  .cra-hero {
    display: flex; align-items: center; gap: 1.6rem;
    background:
      radial-gradient(1000px 220px at 12% -40%, rgba(127,212,193,.20), transparent 65%),
      linear-gradient(115deg, #171E2C 0%, #222C40 52%, #31405C 100%);
    background-color: #1B2233;
    border: 1px solid rgba(255,255,255,.09);
    border-radius: 16px; padding: 1.5rem 1.8rem; margin-bottom: 1.5rem;
  }
  .cra-hero-text { flex: 1 1 auto; min-width: 0; }
  .cra-hero h1 {
    margin: 0; font-size: 1.72rem; letter-spacing: -.025em; color: #F4F7FB;
    font-weight: 750;
  }
  .cra-hero p {
    margin: .45rem 0 0; color: rgba(226,233,244,.74); font-size: .93rem;
    line-height: 1.55; max-width: 62ch;
  }
  .cra-hero img { flex: 0 0 auto; width: 200px; opacity: .96; }
  .cra-chips { margin-top: .85rem; display: flex; gap: .45rem; flex-wrap: wrap; }
  .cra-chip {
    font-size: .7rem; font-weight: 600; letter-spacing: .02em;
    padding: .24rem .6rem; border-radius: 999px;
    background: rgba(255,255,255,.09); color: rgba(232,238,247,.86);
    border: 1px solid rgba(255,255,255,.10);
  }

  /* ---------- stat tiles ---------- */
  .cra-stats { display: flex; gap: .8rem; flex-wrap: wrap; margin: 0 0 1.15rem; }
  .cra-stat {
    flex: 1 1 160px; border: 1px solid rgba(140,150,170,.24);
    border-radius: 13px; padding: .85rem 1rem;
    background: linear-gradient(160deg, rgba(140,150,170,.09), rgba(140,150,170,.03));
  }
  .cra-stat .k {
    font-size: .67rem; text-transform: uppercase; letter-spacing: .09em;
    opacity: .58; font-weight: 700;
  }
  .cra-stat .v {
    font-size: 1.62rem; font-weight: 760; line-height: 1.22; letter-spacing: -.03em;
    margin-top: .1rem;
  }
  .cra-stat .s { font-size: .73rem; opacity: .55; margin-top: .05rem; }

  /* ---------- severity distribution ---------- */
  .cra-dist {
    display: flex; height: 10px; border-radius: 6px; overflow: hidden;
    margin: .1rem 0 .55rem; background: rgba(140,150,170,.16);
  }
  .cra-dist span { display: block; height: 100%; }
  .cra-legend {
    display: flex; gap: 1.1rem; flex-wrap: wrap; font-size: .78rem;
    opacity: .82; margin-bottom: 1rem;
  }
  .cra-legend i {
    font-style: normal; display: inline-block; width: 9px; height: 9px;
    border-radius: 3px; margin-right: .38rem;
  }

  /* ---------- finding card ---------- */
  .cra-card {
    border: 1px solid rgba(140,150,170,.22); border-left: 4px solid var(--sev);
    border-radius: 12px; overflow: hidden;
    background: linear-gradient(180deg, rgba(140,150,170,.07), transparent 60%);
  }
  .cra-card-head {
    display: flex; align-items: center; gap: .6rem; flex-wrap: wrap;
    padding: .72rem .95rem; background: var(--sev-soft);
    border-bottom: 1px solid rgba(140,150,170,.16);
  }
  .cra-badge {
    font-size: .65rem; font-weight: 800; letter-spacing: .07em;
    padding: .2rem .55rem; border-radius: 6px;
    background: var(--sev); color: #14181F;
  }
  .cra-path {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .82rem; font-weight: 650; letter-spacing: -.01em;
  }
  .cra-cat { font-size: .77rem; opacity: .72; }
  .cra-conf {
    margin-left: auto; display: inline-flex; align-items: center; gap: .45rem;
    font-size: .73rem; opacity: .9;
  }
  .cra-bar {
    width: 58px; height: 6px; border-radius: 4px;
    background: rgba(140,150,170,.28); overflow: hidden;
  }
  .cra-bar > span { display: block; height: 100%; border-radius: 4px; }

  .cra-body { padding: .78rem .95rem .2rem; font-size: .9rem; line-height: 1.62; }
  .cra-body code {
    background: rgba(140,150,170,.16); padding: .08em .35em; border-radius: 4px;
    font-size: .86em;
  }

  /* ---------- empty / clean states ---------- */
  .cra-empty {
    display: flex; flex-direction: column; align-items: center; gap: .6rem;
    border: 1px dashed rgba(140,150,170,.32); border-radius: 14px;
    padding: 2rem 1.4rem; text-align: center;
  }
  .cra-empty .t { font-weight: 650; font-size: .98rem; }
  .cra-empty .d { font-size: .85rem; opacity: .64; max-width: 46ch; }

  /* ---------- live progress ---------- */
  .cra-live {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .8rem; line-height: 1.75; max-height: 260px; overflow-y: auto;
  }
  .cra-live div { opacity: .78; }
  .cra-live div:last-child { opacity: 1; font-weight: 600; }

  /* ---------- observability strip ---------- */
  .cra-obs {
    display: flex; align-items: center; gap: .7rem; flex-wrap: wrap;
    border: 1px solid rgba(127,212,193,.28); background: rgba(127,212,193,.07);
    border-radius: 11px; padding: .6rem .9rem; margin-bottom: 1.1rem;
    font-size: .84rem;
  }
  .cra-obs a { font-weight: 600; text-decoration: none; }
  .cra-obs a:hover { text-decoration: underline; }

  /* ---------- live run panel ---------- */
  .cra-phases { display: flex; gap: .4rem; flex-wrap: wrap; margin-bottom: .7rem; }
  .cra-phase {
    font-size: .7rem; font-weight: 650; padding: .26rem .6rem; border-radius: 999px;
    border: 1px solid rgba(140,150,170,.28); opacity: .42; white-space: nowrap;
  }
  .cra-phase.done { opacity: 1; border-color: rgba(95,191,151,.55);
                    background: rgba(95,191,151,.12); color: #5FBF97; }
  .cra-phase.active { opacity: 1; border-color: rgba(227,188,63,.65);
                      background: rgba(227,188,63,.14); color: #E3BC3F; }

  .cra-meters { display: flex; gap: .55rem; flex-wrap: wrap; margin-bottom: .75rem; }
  .cra-meter {
    flex: 1 1 108px; border: 1px solid rgba(140,150,170,.22); border-radius: 9px;
    padding: .45rem .65rem; background: rgba(140,150,170,.05);
  }
  .cra-meter .k { font-size: .62rem; text-transform: uppercase; letter-spacing: .08em;
                  opacity: .55; font-weight: 700; }
  .cra-meter .v { font-size: 1.02rem; font-weight: 720; letter-spacing: -.02em; }

  .cra-budget {
    height: 6px; border-radius: 4px; background: rgba(140,150,170,.2);
    overflow: hidden; margin-bottom: .8rem;
  }
  .cra-budget > span { display: block; height: 100%; border-radius: 4px; }

  .cra-feed {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .78rem; line-height: 1.6; max-height: 300px; overflow-y: auto;
    border: 1px solid rgba(140,150,170,.18); border-radius: 9px; padding: .55rem .7rem;
    background: rgba(10,14,22,.28);
  }
  .cra-feed .row { display: flex; gap: .5rem; padding: .11rem 0; opacity: .8; }
  .cra-feed .row:last-child { opacity: 1; }
  .cra-feed .ic { flex: 0 0 auto; }
  .cra-feed .tx { flex: 0 0 auto; font-weight: 620; }
  .cra-feed .dt {
    flex: 1 1 auto; min-width: 0; opacity: .58; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
  }

  .cra-models { font-size: .76rem; margin-top: .6rem; }
  .cra-models table { width: 100%; border-collapse: collapse; }
  .cra-models td, .cra-models th {
    padding: .22rem .45rem; border-bottom: 1px solid rgba(140,150,170,.14);
    text-align: right;
  }
  .cra-models th { opacity: .55; font-size: .66rem; text-transform: uppercase;
                   letter-spacing: .07em; }
  .cra-models td:first-child, .cra-models th:first-child { text-align: left;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

  /* ---------- master/detail ---------- */
  .cra-row-sel { border-left: 3px solid var(--sev); padding-left: .5rem; }

  .cra-detail-head {
    display: flex; align-items: center; gap: .55rem; flex-wrap: wrap;
    padding: .7rem .9rem; border-radius: 11px 11px 0 0;
    background: var(--sev-soft); border: 1px solid rgba(140,150,170,.2);
    border-bottom: none;
  }

  /* Streamlit rewrites <pre> into its own div and drops the class, so these
     use <div> containers and self-sufficient row classes. */
  .cra-snip, .cra-diff {
    padding: .6rem 0; overflow-x: auto;
    background: rgba(10,14,22,.5);
    border: 1px solid rgba(140,150,170,.2);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .78rem; line-height: 1.62; color: #C6D2E2;
  }
  .cra-snip { border-radius: 0; border-top: none; border-bottom: none; }
  .cra-diff { border-radius: 9px; margin-top: .4rem; }

  .cra-srow, .cra-drow {
    display: flex; gap: .6rem; padding: 0 .85rem; white-space: pre;
  }
  .cra-sln {
    flex: 0 0 2.8em; text-align: right; opacity: .34; user-select: none;
  }
  .cra-stx { flex: 1 1 auto; white-space: pre; }
  .cra-srow.hit {
    background: color-mix(in srgb, var(--hit) 20%, transparent);
    box-shadow: inset 3px 0 0 var(--hit);
  }
  .cra-srow.hit .cra-sln { opacity: .9; color: var(--hit); font-weight: 700; }
  .cra-dsg { flex: 0 0 1.1em; opacity: .75; }
  .cra-drow.del { background: rgba(244,96,108,.14); color: #F09AA2; }
  .cra-drow.add { background: rgba(95,191,151,.14); color: #86D8B4; }

  .cra-explain {
    border: 1px solid rgba(140,150,170,.2); border-top: none;
    border-radius: 0 0 11px 11px; padding: .8rem .9rem .55rem;
    font-size: .9rem; line-height: 1.62;
  }
  .cra-explain code {
    background: rgba(140,150,170,.16); padding: .08em .35em; border-radius: 4px;
  }
  .cra-secheading {
    font-size: .68rem; text-transform: uppercase; letter-spacing: .09em;
    opacity: .55; font-weight: 700; margin: .9rem 0 .3rem;
  }

  /* ---------- findings list row ---------- */
  .cra-lrow {
    border-left: 3px solid transparent; border-radius: 0 9px 9px 0;
    padding: .5rem .65rem .55rem; margin-bottom: .1rem;
  }
  .cra-lrow-top {
    display: flex; align-items: center; gap: .45rem; margin-bottom: .22rem;
  }
  .cra-lchip {
    font-size: .6rem; font-weight: 800; letter-spacing: .07em;
    padding: .12rem .4rem; border-radius: 5px;
  }
  .cra-lcat { font-size: .74rem; opacity: .75; }
  .cra-lconf { margin-left: auto; font-size: .72rem; font-weight: 750; }
  .cra-ltitle {
    font-size: .855rem; font-weight: 620; line-height: 1.36; margin-bottom: .12rem;
  }
  .cra-lpath {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .7rem; opacity: .56;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }

  /* ---------- modal ---------- */
  .cra-modal-sub {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .78rem; opacity: .62; margin: -.3rem 0 .7rem;
  }
  .cra-fixnote {
    border-left: 3px solid #5FBF97; background: rgba(95,191,151,.08);
    border-radius: 0 8px 8px 0; padding: .55rem .75rem; margin-top: .5rem;
    font-size: .87rem; line-height: 1.55;
  }
  .cra-secheading.fix { color: #5FBF97; opacity: .9; }

  section[data-testid="stSidebar"] .stButton button { font-weight: 650; }
</style>
"""


def severity_style(severity: str) -> dict[str, str]:
    return SEVERITY_STYLE.get(severity, SEVERITY_STYLE["low"])


def confidence_color(confidence: int, threshold: int) -> str:
    """Green above the gate, amber just below, grey when weak."""
    if confidence >= threshold:
        return "#5FBF97"
    if confidence >= threshold - 15:
        return "#E3BC3F"
    return "#8A94A6"


PHASES = [
    ("plan", "Plan"),
    ("memory", "Memory"),
    ("fetch", "Fetch"),
    ("mount", "Mount"),
    ("review", "Review"),
    ("consolidate", "Consolidate"),
]


def phase_strip(seen: set[str], current: str | None) -> str:
    """Pipeline breadcrumb: completed phases solid, the active one highlighted."""
    chips = []
    for key, label in PHASES:
        cls = "done" if key in seen and key != current else ""
        if key == current:
            cls = "active"
        chips.append(f'<span class="cra-phase {cls}">{label}</span>')
    return f'<div class="cra-phases">{"".join(chips)}</div>'


def meters(stats: dict, max_cost: float, max_calls: int) -> str:
    """Live cost/token meters plus a budget-consumption bar."""
    cost = float(stats.get("cost", 0.0))
    calls = int(stats.get("calls", 0))
    used = max(cost / max_cost if max_cost else 0, calls / max_calls if max_calls else 0)
    used = min(used, 1.0)
    bar_color = "#5FBF97" if used < 0.6 else ("#E3BC3F" if used < 0.85 else "#F4606C")
    cached_pct = int(float(stats.get("cached_share", 0)) * 100)

    tiles = [
        ("LLM calls", f"{calls}/{max_calls}"),
        ("Cost", f"${cost:.4f}"),
        ("Input tok", f"{int(stats.get('input_tokens', 0)):,}"),
        ("Output tok", f"{int(stats.get('output_tokens', 0)):,}"),
        ("Cached", f"{cached_pct}%"),
    ]
    cells = "".join(
        f'<div class="cra-meter"><div class="k">{k}</div><div class="v">{v}</div></div>'
        for k, v in tiles
    )
    return (
        f'<div class="cra-meters">{cells}</div>'
        f'<div class="cra-budget"><span style="width:{used * 100:.1f}%;'
        f'background:{bar_color}"></span></div>'
    )


def model_table(by_model: dict) -> str:
    """Per-model spend breakdown."""
    if not by_model:
        return ""
    rows = "".join(
        f"<tr><td>{name}</td><td>{v['calls']}</td>"
        f"<td>{v['input']:,}</td><td>{v['output']:,}</td>"
        f"<td>${v['cost']:.4f}</td></tr>"
        for name, v in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"])
    )
    return (
        '<div class="cra-models"><table><tr><th>Model</th><th>Calls</th>'
        "<th>In</th><th>Out</th><th>Cost</th></tr>"
        f"{rows}</table></div>"
    )


def feed(events: list[dict], limit: int = 14) -> str:
    """Scrolling activity log."""
    rows = "".join(
        f'<div class="row"><span class="ic">{e.get("icon", "›")}</span>'
        f'<span class="tx">{html.escape(str(e.get("text", "")))}</span>'
        f'<span class="dt">{html.escape(str(e.get("detail", "")))}</span></div>'
        for e in events[-limit:]
    )
    return f'<div class="cra-feed">{rows}</div>'


def code_window(
    lines: tuple[str, ...] | list[str],
    focus: int,
    fallback: str,
    context: int = 5,
    color: str = "#F4606C",
) -> str:
    """Render a code excerpt around `focus`, with that line highlighted.

    Falls back to the single anchored line when the file could not be fetched,
    so the panel still shows the offending code offline.
    """

    def row(number: int, text: str, hit: bool) -> str:
        style = f' style="--hit:{color}"' if hit else ""
        return (
            f'<div class="cra-srow{" hit" if hit else ""}"{style}>'
            f'<span class="cra-sln">{number}</span>'
            f'<span class="cra-stx">{html.escape(text) or " "}</span></div>'
        )

    if not lines or focus > len(lines):
        body = row(focus, fallback, True)
    else:
        start = max(1, focus - context)
        end = min(len(lines), focus + context)
        body = "".join(
            row(n, lines[n - 1], n == focus) for n in range(start, end + 1)
        )
    return f'<div class="cra-snip">{body}</div>'


def diff_block(before: str, after: str) -> str:
    """Before/after fix rendered as a minimal diff."""
    rows = [
        f'<div class="cra-drow del"><span class="cra-dsg">-</span>'
        f'<span class="cra-stx">{html.escape(before)}</span></div>'
    ]
    for line in (after.splitlines() or [after]):
        rows.append(
            f'<div class="cra-drow add"><span class="cra-dsg">+</span>'
            f'<span class="cra-stx">{html.escape(line)}</span></div>'
        )
    return f'<div class="cra-diff">{"".join(rows)}</div>'


def code_block(anchor_text: str, line: int) -> str:
    """Render the anchored line with its line number, escaped for HTML."""
    return (
        f'<div class="cra-snip"><div class="cra-srow">'
        f'<span class="cra-sln">{line}</span>'
        f'<span class="cra-stx">{html.escape(anchor_text)}</span></div></div>'
    )


def list_row(
    severity_color: str,
    severity_label: str,
    title: str,
    path: str,
    line: int,
    confidence: int,
    category_icon: str,
    conf_color: str,
    selected: bool,
) -> str:
    """One scannable row: what the issue is, where it is, and how sure we are."""
    bg = "rgba(140,150,170,.13)" if selected else "transparent"
    ring = "box-shadow:inset 0 0 0 1px rgba(140,150,170,.28);" if selected else ""
    chip_bg = f"color-mix(in srgb, {severity_color} 16%, transparent)"
    return (
        f'<div class="cra-lrow" style="border-left-color:{severity_color};'
        f'background:{bg};{ring}">'
        f'<div class="cra-lrow-top">'
        f'<span class="cra-lchip" style="color:{severity_color};'
        f'background:{chip_bg}">{severity_label}</span>'
        f'<span class="cra-lcat">{category_icon}</span>'
        f'<span class="cra-lconf" style="color:{conf_color}">{confidence}</span>'
        f"</div>"
        f'<div class="cra-ltitle">{html.escape(title)}</div>'
        f'<div class="cra-lpath">{html.escape(path.rsplit("/", 1)[-1])}:{line}</div>'
        f"</div>"
    )
