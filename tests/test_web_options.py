"""Query-param plumbing for the new graph options."""
from datetime import date, timedelta


def seed(storage):
    # relative dates: the default look window is 30d
    storage.upsert_points(
        [
            (
                "github", "zubrafex/braggraphs", "stars", "cumulative", 100,
                (date.today() - timedelta(days=20)).isoformat(),
            ),
            (
                "github", "zubrafex/braggraphs", "stars", "cumulative", 210,
                (date.today() - timedelta(days=1)).isoformat(),
            ),
        ]
    )


URL = "/graph/zubrafex/braggraphs/stars.svg"


def test_theme_param_new_palettes(client, storage):
    seed(storage)
    body = client.get(URL + "?theme=pine").get_data(as_text=True)
    assert "#222d27" in body  # pine bg
    body = client.get(URL + "?theme=oat").get_data(as_text=True)
    assert "#3a643a" in body  # oat accent


def test_accent_and_bg_params(client, storage):
    seed(storage)
    body = client.get(URL + "?accent=b15a2b&bg=e8e2d4").get_data(as_text=True)
    assert 'stroke="#b15a2b"' in body
    assert 'fill="#e8e2d4"' in body
    # invalid values are ignored, not errors
    r = client.get(URL + "?accent=notahex&bg=zz")
    assert r.status_code == 200
    assert "#5b6ee8" in r.get_data(as_text=True)  # default accent kept


def test_toggle_params(client, storage):
    seed(storage)
    body = client.get(
        URL + "?grid=0&baseline=0&border=0&dates=0&label=0&value=0&dot=0"
    ).get_data(as_text=True)
    assert "<line" not in body
    assert "<circle" not in body
    assert "<text" not in body
    assert "<path" in body


def test_sparkline_shorthand(client, storage):
    seed(storage)
    body = client.get(URL + "?sparkline=1&w=100&h=24").get_data(as_text=True)
    assert 'width="100" height="24"' in body
    assert "<text" not in body  # no labels/dates/values
    assert "<line" not in body  # no grid/baseline
    assert "<circle" in body  # dot stays on
    assert "<rect" not in body  # transparent bg, no border
    # …and params can override the shorthand
    body = client.get(URL + "?sparkline=1&dot=0&bg=ffffff").get_data(as_text=True)
    assert "<circle" not in body
    assert 'fill="#ffffff"' in body


def test_tiny_size_clamps(client, storage):
    seed(storage)
    body = client.get(URL + "?w=41&h=17").get_data(as_text=True)
    assert 'width="41" height="17"' in body
    body = client.get(URL + "?w=1&h=1").get_data(as_text=True)
    assert 'width="40" height="16"' in body  # clamped to the floor


def test_embed_forwards_all_params(client, storage):
    seed(storage)
    html = client.get(
        "/embed/zubrafex/braggraphs/stars?theme=pine&sparkline=1&accent=b15a2b"
    ).get_data(as_text=True)
    assert "theme=pine" in html
    assert "sparkline=1" in html
    assert "accent=b15a2b" in html
