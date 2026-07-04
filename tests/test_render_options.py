"""New renderer capabilities: themes, colour overrides, chrome toggles, tiny sizes."""
import xml.etree.ElementTree as ET

from render.svg import render_chart
from render.themes import THEMES

NOW = "2026-07-01"
POINTS = [
    ("2026-06-01", 100),
    ("2026-06-10", 140),
    ("2026-06-20", 210),
    ("2026-06-30", 305),
]


def render(**kw):
    defaults = dict(
        kind="cumulative", label="zubrafex/braggraphs · stars", unit="total", now=NOW
    )
    defaults.update(kw)
    return render_chart(POINTS, **defaults)


def test_theme_roles_complete():
    roles = {"bg", "border", "text", "muted", "grid", "baseline", "accent"}
    for name, palette in THEMES.items():
        assert set(palette) == roles, f"theme {name} missing roles"


def test_every_theme_renders_valid_svg():
    for name, palette in THEMES.items():
        svg = render(theme=name)
        ET.fromstring(svg)
        assert palette["bg"] in svg
        assert palette["accent"] in svg


def test_accent_and_bg_overrides():
    svg = render(theme="light", accent="#b15a2b", bg="#e8e2d4")
    assert 'stroke="#b15a2b"' in svg
    assert 'fill="#e8e2d4"' in svg
    assert THEMES["light"]["accent"] not in svg


def test_transparent_background():
    svg = render(bg="transparent", show_border=False)
    assert "<rect" not in svg  # no frame, no tick pads
    assert THEMES["light"]["bg"] not in svg


def test_toggles_remove_elements():
    full = render()
    assert "<line" in full and "<circle" in full and "<text" in full

    no_lines = render(show_grid=False, show_baseline=False, show_border=False)
    assert "<line" not in no_lines
    assert 'stroke="#f0f2f4"' not in no_lines  # grid colour gone

    no_dot = render(show_dot=False)
    assert "<circle" not in no_dot

    bare = render(
        show_label=False, show_value=False, show_grid=False, show_baseline=False,
        show_dates=False, show_border=False, show_dot=False,
    )
    assert "<text" not in bare
    assert "<path" in bare  # the line survives
    ET.fromstring(bare)


def test_chrome_off_reclaims_space():
    """Without label/date rows the line should use nearly the full height."""
    def path_ys(svg):
        d = ET.fromstring(svg).find(".//{http://www.w3.org/2000/svg}path").get("d")
        return [float(seg.split()[-1]) for seg in d[1:].split("L")]

    full = path_ys(render(h=120))
    bare = path_ys(
        render(h=120, show_label=False, show_value=False, show_dates=False)
    )
    assert min(bare) < min(full)  # can climb higher
    assert max(bare) > max(full)  # and reach lower


def test_tiny_sparkline():
    svg = render(
        w=100, h=24,
        show_label=False, show_value=False, show_grid=False, show_baseline=False,
        show_dates=False, show_border=False, bg="transparent",
    )
    ET.fromstring(svg)
    assert 'width="100" height="24"' in svg
    assert len(svg.encode()) < 2_000
    assert 'stroke-width="1.4"' in svg  # thinner line at tiny sizes
    assert 'r="1.8"' in svg  # smaller dot


def test_short_plot_drops_tick_numerals_keeps_gridlines():
    svg = render(h=48, show_label=False, show_value=False, show_dates=False)
    assert "<line" in svg
    # no 8px tick numerals on a short plot
    root = ET.fromstring(svg)
    texts = root.findall(".//{http://www.w3.org/2000/svg}text")
    assert texts == []


def test_empty_state_tiny():
    svg = render_chart([], w=100, h=24, show_dates=False, now=NOW)
    assert "no data yet" in svg
    assert 'font-size="7"' in svg


def test_value_window_sums_flow_series():
    from render.svg import render_chart

    points = [(f"2026-06-{d:02d}", 10.0) for d in range(1, 31)]  # 30 days × 10
    svg = render_chart(points, kind="flow", unit="/day", value_window_days=7, now="2026-07-01")
    assert ">70<" in svg.replace("</text>", "<")  # 7-day sum replaces last-day value
    assert "/7d" in svg


def test_value_window_ignored_for_cumulative():
    from render.svg import render_chart

    points = [("2026-06-29", 100.0), ("2026-06-30", 120.0)]
    svg = render_chart(points, kind="cumulative", unit="total", value_window_days=30, now="2026-07-01")
    assert "120" in svg and "total" in svg and "/30d" not in svg
