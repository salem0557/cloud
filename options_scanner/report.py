"""Self-contained, sortable/searchable HTML report - a spreadsheet-like
view of scan results that opens directly in any browser, no server needed."""

import html
from datetime import datetime
from typing import List

from .filters import OptionContract

_COLUMNS = [
    ("ticker", "Ticker", "text"),
    ("option_type", "Type", "text"),
    ("expiry", "Expiry", "text"),
    ("dte", "DTE", "num"),
    ("strike", "Strike", "num"),
    ("bid", "Bid", "num"),
    ("ask", "Ask", "num"),
    ("spread_pct", "Spread %", "num"),
    ("volume", "Volume", "num"),
    ("open_interest", "Open Int.", "num"),
    ("iv", "IV %", "num"),
    ("delta", "Delta", "num"),
    ("theta", "Theta", "num"),
]

_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Options Scan Results</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #ffffff; --fg: #1a1a1a; --border: #d8dce1; --head-bg: #f3f4f6;
    --stripe: #fafafa; --accent: #2563eb; --muted: #6b7280;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #16181d; --fg: #e6e8eb; --border: #33383f; --head-bg: #20232a;
             --stripe: #1b1e24; --accent: #6ea8fe; --muted: #9aa2ad; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 1.25rem; background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  h1 {{ font-size: 1.15rem; margin: 0 0 0.15rem; }}
  .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 0.9rem; }}
  .toolbar {{ display: flex; gap: 0.6rem; margin-bottom: 0.75rem; flex-wrap: wrap; }}
  #search {{
    flex: 1; min-width: 200px; padding: 0.45rem 0.7rem; border: 1px solid var(--border);
    border-radius: 6px; background: var(--bg); color: var(--fg); font-size: 0.9rem;
  }}
  .table-wrap {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; white-space: nowrap; }}
  thead th {{
    position: sticky; top: 0; background: var(--head-bg); cursor: pointer;
    padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border);
    user-select: none;
  }}
  thead th:hover {{ color: var(--accent); }}
  thead th.num, td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tbody td {{ padding: 0.4rem 0.75rem; border-bottom: 1px solid var(--border); }}
  tbody tr:nth-child(even) {{ background: var(--stripe); }}
  tbody tr:hover {{ outline: 1px solid var(--accent); outline-offset: -1px; }}
  .arrow {{ font-size: 0.7em; opacity: 0.6; margin-left: 0.25rem; }}
  #count {{ color: var(--muted); font-size: 0.85rem; margin-top: 0.6rem; }}
</style>
</head>
<body>
<h1>Options Scan Results</h1>
<div class="meta">Generated {generated_at} &middot; {total} contract(s) matched all filters</div>
<div class="toolbar">
  <input id="search" type="text" placeholder="Filter by ticker, type...">
</div>
<div class="table-wrap">
<table id="results">
<thead><tr>{header_html}</tr></thead>
<tbody>{rows_html}</tbody>
</table>
</div>
<div id="count"></div>
<script>
const table = document.getElementById('results');
const tbody = table.tBodies[0];
const rows = Array.from(tbody.rows);
let sortState = {{ col: -1, dir: 1 }};

function sortBy(colIdx, type) {{
  sortState.dir = sortState.col === colIdx ? -sortState.dir : 1;
  sortState.col = colIdx;
  rows.sort((a, b) => {{
    let av = a.cells[colIdx].dataset.v, bv = b.cells[colIdx].dataset.v;
    if (type === 'num') {{ av = parseFloat(av); bv = parseFloat(bv); return (av - bv) * sortState.dir; }}
    return av.localeCompare(bv) * sortState.dir;
  }});
  rows.forEach(r => tbody.appendChild(r));
  document.querySelectorAll('.arrow').forEach(a => a.textContent = '');
  const arrow = table.tHead.rows[0].cells[colIdx].querySelector('.arrow');
  if (arrow) arrow.textContent = sortState.dir === 1 ? '\\u25B2' : '\\u25BC';
}}

table.tHead.rows[0].querySelectorAll('th').forEach((th, i) => {{
  th.addEventListener('click', () => sortBy(i, th.dataset.type));
}});

function applyFilter() {{
  const q = document.getElementById('search').value.trim().toLowerCase();
  let visible = 0;
  rows.forEach(r => {{
    const show = !q || r.textContent.toLowerCase().includes(q);
    r.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  document.getElementById('count').textContent = visible + ' row(s) shown';
}}
document.getElementById('search').addEventListener('input', applyFilter);
applyFilter();
</script>
</body>
</html>
"""


def _cell(value, kind: str) -> str:
    escaped = html.escape(str(value))
    cls = ' class="num"' if kind == "num" else ""
    return f'<td{cls} data-v="{escaped}">{escaped}</td>'


def render_html(contracts: List[OptionContract]) -> str:
    header_html = "".join(
        f'<th class="{kind}" data-type="{kind}">{label}<span class="arrow"></span></th>'
        for _, label, kind in _COLUMNS
    )

    row_values = []
    for c in contracts:
        row_values.append({
            "ticker": c.ticker,
            "option_type": c.option_type,
            "expiry": c.expiry.isoformat(),
            "dte": c.dte,
            "strike": f"{c.strike:g}",
            "bid": f"{c.bid:.2f}",
            "ask": f"{c.ask:.2f}",
            "spread_pct": f"{(c.spread_pct or 0) * 100:.1f}",
            "volume": c.volume,
            "open_interest": c.open_interest,
            "iv": f"{c.iv * 100:.1f}",
            "delta": f"{c.delta:.3f}",
            "theta": f"{c.theta:.3f}",
        })

    rows_html = "".join(
        "<tr>" + "".join(_cell(values[key], kind) for key, _, kind in _COLUMNS) + "</tr>"
        for values in row_values
    )

    return _PAGE_TEMPLATE.format(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        total=len(contracts),
        header_html=header_html,
        rows_html=rows_html,
    )


def write_html(contracts: List[OptionContract], path: str) -> None:
    with open(path, "w") as f:
        f.write(render_html(contracts))
