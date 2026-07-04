"""Hand-rolled SVG line chart generator.

Pure and deterministic: same inputs → byte-identical output (pass `now` for the
"updated" stamp), which makes golden-file snapshot tests possible. No chart
library, no client-side JS — the output is a plain <svg> a browser or README
renders as-is, targeted at < 5KB.

Every piece of chrome (label row, value, gridlines, baseline, border, dot,
date row) can be switched off independently, and the layout reclaims the
space, so the same renderer scales from a full 400×120 card down to a bare
~80×20 sparkline that melts into a portfolio tile.
"""
from __future__ import annotations

import math
from datetime import date
from xml.sax.saxutils import escape

from render.themes import DEFAULT_THEME, THEMES

FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)
MAX_POINTS = 200  # downsample beyond this to keep output tiny
TRANSPARENT = "transparent"


def _nice_step(raw: float) -> float:
    """Round up to a 'nice' 1/2/5 × 10ⁿ step."""
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 5, 10):
        if mult * mag >= raw:
            return mult * mag
    return 10 * mag


def _fmt_tick(v: float) -> str:
    """Abbreviated numerals for gridline labels: 950, 1.2k, 3M."""
    for threshold, suffix in ((1_000_000, "M"), (1_000, "k")):
        if abs(v) >= threshold:
            s = f"{v / threshold:.1f}".rstrip("0").rstrip(".")
            return f"{s}{suffix}"
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def _fmt_value(v: float) -> str:
    """Full current value with thousands separators: 1,234."""
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.1f}"


def _downsample(points: list, limit: int = MAX_POINTS) -> list:
    if len(points) <= limit:
        return points
    stride = math.ceil(len(points) / limit)
    kept = points[::stride]
    if kept[-1] != points[-1]:
        kept.append(points[-1])
    return kept


def render_chart(
    points: list[tuple[str, float]],
    *,
    kind: str = "cumulative",
    label: str = "",
    unit: str = "total",
    theme: str = "light",
    w: int = 400,
    h: int = 120,
    now: str | None = None,
    accent: str | None = None,
    bg: str | None = None,
    show_label: bool = True,
    show_value: bool = True,
    show_grid: bool = True,
    show_baseline: bool = True,
    show_dot: bool = True,
    show_dates: bool = True,
    show_border: bool = True,
    value_window_days: int | None = None,
) -> str:
    """Render (date, value) points as a complete SVG document string.

    points must be sorted ascending by date ('YYYY-MM-DD'). `kind='flow'`
    anchors the y-axis at zero (a flow chart not starting at 0 lies).
    `accent`/`bg` override the theme's colours ('#rrggbb'); `bg='transparent'`
    drops the background fill entirely so the graph sits on any surface.
    For flow metrics, `value_window_days` makes the value readout the *sum*
    over that trailing window (unit becomes '/Nd') instead of the last
    single-day figure — a day of quiet shouldn't read as zero traffic.
    """
    t = dict(THEMES.get(theme, THEMES[DEFAULT_THEME]))
    if accent:
        t["accent"] = accent
    transparent = bg == TRANSPARENT
    if bg and not transparent:
        t["bg"] = bg
    updated = now or date.today().isoformat()
    points = sorted(points)
    # Window sum is computed on the full series, before downsampling drops rows.
    window_value = None
    if kind == "flow" and value_window_days and points:
        cutoff = date.fromisoformat(points[-1][0]).toordinal() - (value_window_days - 1)
        window_value = sum(
            v for d, v in points if date.fromisoformat(d).toordinal() >= cutoff
        )
        unit = f"/{value_window_days}d"
    points = _downsample(points)

    # Layout adapts to whatever chrome is enabled and to tiny sizes.
    small_h = h < 60
    pad = 10 if w >= 200 else (6 if w >= 100 else 4)
    top = 26.0 if (show_label or show_value) else (6.0 if not small_h else 3.0)
    bottom = 22.0 if show_dates else (6.0 if not small_h else 3.0)
    stroke_w = 1.75 if not small_h else 1.4
    dot_r, halo_r = (2.5, 5) if not small_h else (1.8, 3.5)
    rx = 6 if min(w, h) >= 60 else 3

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="{escape(label, {chr(34): "&quot;"})}">'
    ]
    if not transparent:
        stroke = t["border"] if show_border else "none"
        parts.append(
            f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="{rx}" '
            f'fill="{t["bg"]}" stroke="{stroke}"/>'
        )
    elif show_border:
        parts.append(
            f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="{rx}" '
            f'fill="none" stroke="{t["border"]}"/>'
        )
    parts.append(
        f'<g font-family="{FONT}" style="font-variant-numeric:tabular-nums">'
    )
    if show_label:
        parts.append(
            f'<text x="{pad}" y="17" font-size="11" fill="{t["muted"]}">{escape(label)}</text>'
        )

    # Plot rect
    px0, px1 = float(pad), float(w - pad)
    py0, py1 = top, h - bottom

    if not points:
        parts += [
            f'<text x="{w / 2:.1f}" y="{(py0 + py1) / 2 + 4:.1f}" '
            f'font-size="{11 if not small_h else 7}" '
            f'fill="{t["muted"]}" text-anchor="middle">no data yet</text>',
        ]
        if show_dates:
            parts.append(_updated_stamp(w, h, pad, t, updated))
        parts.append("</g></svg>")
        return "".join(parts)

    values = [v for _, v in points]
    vmin, vmax = min(values), max(values)
    span = vmax - vmin

    # y domain: flow charts anchor at 0; cumulative charts crop to the data
    lo = 0.0 if kind == "flow" else max(0.0, vmin - span * 0.15)
    hi = vmax + max(span, (vmax - lo)) * 0.1
    if hi <= lo:
        hi = lo + 1.0

    step = _nice_step((hi - lo) / 3)
    ticks = []
    tick = math.ceil(lo / step) * step
    while tick <= hi and len(ticks) < 4:
        if tick > lo:
            ticks.append(tick)
        tick += step

    def sy(v: float) -> float:
        return py1 - (v - lo) / (hi - lo) * (py1 - py0)

    dates = [date.fromisoformat(d).toordinal() for d, _ in points]
    d0, d1 = dates[0], dates[-1]

    def sx(o: int) -> float:
        if d1 == d0:
            return (px0 + px1) / 2
        return px0 + (o - d0) / (d1 - d0) * (px1 - px0)

    # Gridlines now; their labels are deferred until after the line is drawn
    # (with a bg-coloured pad) so the line can never paint over the numerals.
    tick_labels = []
    if show_grid:
        label_room = (py1 - py0) >= 50  # no numerals on short plots
        for tv in ticks:
            y = sy(tv)
            if y < py0 + 6:  # don't collide with the label row
                continue
            parts.append(
                f'<line x1="{px0}" y1="{y:.1f}" x2="{px1}" y2="{y:.1f}" '
                f'stroke="{t["grid"]}" stroke-width="1"/>'
            )
            if not label_room:
                continue
            tick_text = _fmt_tick(tv)
            pad_w = len(tick_text) * 5 + 4
            pad_rect = (
                ""
                if transparent
                else f'<rect x="{px1 - pad_w:.1f}" y="{y - 11:.1f}" width="{pad_w}" '
                f'height="10" fill="{t["bg"]}" opacity="0.85"/>'
            )
            tick_labels.append(
                f"{pad_rect}"
                f'<text x="{px1}" y="{y - 3:.1f}" font-size="8" fill="{t["muted"]}" '
                f'text-anchor="end">{tick_text}</text>'
            )
    if show_baseline:
        parts.append(
            f'<line x1="{px0}" y1="{py1:.1f}" x2="{px1}" y2="{py1:.1f}" '
            f'stroke="{t["baseline"]}" stroke-width="1"/>'
        )

    # The line itself
    coords = [(sx(o), sy(v)) for o, (_, v) in zip(dates, points)]
    if len(coords) > 1:
        d_attr = "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in coords)
        parts.append(
            f'<path d="{d_attr}" fill="none" stroke="{t["accent"]}" '
            f'stroke-width="{stroke_w}" stroke-linejoin="round" stroke-linecap="round"/>'
        )

    parts += tick_labels

    ex, ey = coords[-1]
    if show_dot:
        parts.append(
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="{halo_r}" fill="{t["accent"]}" opacity="0.15"/>'
        )
        parts.append(
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="{dot_r}" fill="{t["accent"]}"/>'
        )
    if show_value:
        current = _fmt_value(window_value if window_value is not None else values[-1])
        parts.append(
            f'<text x="{w - pad}" y="17" font-size="12" font-weight="600" '
            f'fill="{t["text"]}" text-anchor="end">{current}'
            f'<tspan font-size="9" font-weight="400" fill="{t["muted"]}"> {escape(unit)}</tspan></text>'
        )

    if show_dates:
        parts.append(
            f'<text x="{pad}" y="{h - 8}" font-size="8" fill="{t["muted"]}">{points[0][0]}</text>'
        )
        parts.append(_updated_stamp(w, h, pad, t, updated))
    parts.append("</g></svg>")
    return "".join(parts)


def _updated_stamp(w: int, h: int, pad: int, t: dict, updated: str) -> str:
    return (
        f'<text x="{w - pad}" y="{h - 8}" font-size="8" fill="{t["muted"]}" '
        f'text-anchor="end">updated {escape(updated)}</text>'
    )
