"""SVG renderer tests: structural asserts + golden-file snapshots.

Snapshots live in tests/render_snapshots/. A missing golden is written on
first run; set UPDATE_SNAPSHOTS=1 to regenerate after intentional changes.
"""
import os
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

from render.svg import render_chart

SNAP_DIR = Path(__file__).parent / "render_snapshots"
NOW = "2026-07-01"

CUMULATIVE = [
    ("2026-06-01", 100),
    ("2026-06-08", 130),
    ("2026-06-15", 180),
    ("2026-06-22", 260),
    ("2026-06-29", 305),
]
FLOW = [
    ("2026-06-25", 820),
    ("2026-06-26", 940),
    ("2026-06-27", 660),
    ("2026-06-28", 700),
    ("2026-06-29", 1240),
    ("2026-06-30", 1105),
]


def assert_snapshot(name: str, svg: str):
    SNAP_DIR.mkdir(exist_ok=True)
    path = SNAP_DIR / name
    if os.environ.get("UPDATE_SNAPSHOTS") or not path.exists():
        path.write_text(svg)
    assert svg == path.read_text(), f"snapshot mismatch: {path} (UPDATE_SNAPSHOTS=1 to accept)"


def render(points=CUMULATIVE, **kw):
    defaults = dict(
        kind="cumulative", label="zubrafex/braggraphs · stars", unit="total",
        theme="light", now=NOW,
    )
    defaults.update(kw)
    return render_chart(points, **defaults)


def test_valid_xml_and_size():
    svg = render()
    ET.fromstring(svg)  # raises on malformed XML
    assert len(svg.encode()) < 5_000


def test_snapshot_default():
    assert_snapshot("cumulative_light_400x120.svg", render())


def test_snapshot_dark():
    assert_snapshot("cumulative_dark_400x120.svg", render(theme="dark"))


def test_snapshot_flow():
    svg = render(FLOW, kind="flow", label="zubrafex.com · pageviews", unit="/day")
    assert_snapshot("flow_light_400x120.svg", svg)
    assert "/day" in svg


def test_snapshot_custom_size():
    assert_snapshot("cumulative_light_600x200.svg", render(w=600, h=200))


def test_snapshot_empty():
    svg = render([])
    assert_snapshot("empty_light_400x120.svg", svg)
    assert "no data yet" in svg


def test_snapshot_single_point():
    svg = render([("2026-06-30", 42)])
    assert_snapshot("single_point_light_400x120.svg", svg)
    assert "<path" not in svg  # dot only, no line
    assert "42" in svg


def test_current_value_and_stamp():
    svg = render()
    assert "305" in svg
    assert f"updated {NOW}" in svg
    assert "zubrafex/braggraphs · stars" in svg


def test_flow_anchors_at_zero():
    # All values are far from zero; a flow chart must still include 0 in its
    # domain, so the line sits well above the baseline.
    svg = render(FLOW, kind="flow", unit="/day")
    root = ET.fromstring(svg)
    path = root.find(".//{http://www.w3.org/2000/svg}path")
    ys = [float(seg.split()[-1]) for seg in path.get("d")[1:].split("L")]
    assert max(ys) < 90  # baseline is at y=98 for h=120; min value 660 ≫ 0


def test_large_series_downsampled_and_small():
    start = date(2025, 7, 1)
    points = [
        ((start + timedelta(days=i)).isoformat(), 100 + i) for i in range(365)
    ]
    svg = render(points)
    assert len(svg.encode()) < 5_000
    path_d = ET.fromstring(svg).find(".//{http://www.w3.org/2000/svg}path").get("d")
    assert path_d.count("L") <= 200
    assert "464" in svg  # last value always kept


def test_label_escaping():
    svg = render(label='x<>&"y · stars')
    ET.fromstring(svg)
    assert "x&lt;&gt;&amp;" in svg


def test_tick_abbreviation():
    points = [("2026-06-01", 500), ("2026-06-30", 55_000)]
    svg = render(points)
    assert "k" in svg  # gridline labels abbreviate thousands
    assert "55,000" in svg  # current value stays full
