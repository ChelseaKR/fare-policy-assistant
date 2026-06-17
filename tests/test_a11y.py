from web.a11y import PAGE, check_html


def test_shipped_page_passes():
    assert check_html(PAGE.read_text(encoding="utf-8")) == []


GOOD = """<!doctype html><html lang="en"><head><title>T</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>button { min-height: 2.5rem; }</style></head>
<body><h1>H</h1><h2>S</h2>
<label for="q">Q</label><textarea id="q"></textarea>
<button>Ask</button><a href="/x">link</a></body></html>"""


def test_good_page_passes():
    assert check_html(GOOD) == []


def test_missing_lang_flagged():
    assert any("lang" in i for i in check_html(GOOD.replace('<html lang="en">', "<html>")))


def test_unlabeled_control_flagged():
    bad = GOOD.replace(
        '<label for="q">Q</label><textarea id="q"></textarea>', "<textarea></textarea>"
    )
    assert any("accessible name" in i for i in check_html(bad))


def test_skipped_heading_flagged():
    bad = GOOD.replace("<h2>S</h2>", "<h4>S</h4>")
    assert any("skips" in i for i in check_html(bad))


def test_empty_link_flagged():
    bad = GOOD.replace('<a href="/x">link</a>', '<a href="/x"></a>')
    assert any("discernible text" in i for i in check_html(bad))


def test_zoom_disabled_flagged():
    bad = GOOD.replace("initial-scale=1", "initial-scale=1, user-scalable=no")
    assert any("zoom" in i for i in check_html(bad))


def test_small_target_flagged():
    bad = GOOD.replace("min-height: 2.5rem", "min-height: 1rem")
    assert any("Target Size" in i for i in check_html(bad))
