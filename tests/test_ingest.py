from assistant.ingest import sections_from_html

HTML = """
<html><head><script>tracker()</script></head><body>
<nav><h2>Menu</h2><li>Home</li><li>Fares</li></nav>
<main>
<h1>Fares</h1>
<p>All fares are one-way.</p>
<h2>Discount Eligibility</h2>
<p>Discount fare for riders 65 years and older.</p>
<ul><li>Proof of age required</li><li>Medicare Card accepted</li></ul>
<h2>Fare Table</h2>
<table>
<tr><th>Type</th><th>Price</th></tr>
<tr><td>Regular</td><td>$2.00</td></tr>
<tr><td>Discount</td><td>$1.00</td></tr>
</table>
<h2>Follow us</h2>
<p>Twitter, Facebook, and our newsletter signup live here today.</p>
</main>
<footer><p>Copyright</p></footer>
</body></html>
"""


def test_sections_split_on_headings():
    sections = sections_from_html(HTML)
    headings = [h for h, _ in sections]
    assert "Discount Eligibility" in headings
    assert "Fare Table" in headings


def test_nav_and_script_stripped():
    text = " ".join(body for _, body in sections_from_html(HTML))
    assert "tracker()" not in text
    assert "Copyright" not in text


def test_boilerplate_headings_dropped():
    headings = [h for h, _ in sections_from_html(HTML)]
    assert "Follow us" not in headings


def test_table_rows_linearized():
    by_heading = dict(sections_from_html(HTML))
    assert "Regular | $2.00" in by_heading["Fare Table"]
    assert "Discount | $1.00" in by_heading["Fare Table"]


def test_list_items_kept():
    by_heading = dict(sections_from_html(HTML))
    assert "Medicare Card accepted" in by_heading["Discount Eligibility"]
