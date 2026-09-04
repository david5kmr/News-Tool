"""Die Verifikation ist Bauschritt 1 — sie darf nichts als Feed durchwinken,
was keiner ist. Eine HTML-Fehlerseite mit Status 200 ist der Normalfall."""


from mi.net import Response
from mi.verify import discover_feed_links, looks_like_feed

FEED_BODY = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<title>T</title><item><title>A</title><link>https://x.de/1</link></item>
</channel></rss>"""


def response(content: bytes, ctype: str = "application/rss+xml", status: int = 200):
    return Response(
        url="https://x.de/feed",
        status=status,
        text=content.decode("utf-8", "replace"),
        content=content,
        headers={"Content-Type": ctype},
    )


class TestLooksLikeFeed:
    def test_accepts_a_real_feed(self):
        ok, entries = looks_like_feed(response(FEED_BODY))
        assert ok and entries == 1

    def test_rejects_html_served_with_status_200(self):
        html = b"<!doctype html><html><body><h1>Seite nicht gefunden</h1></body></html>"
        assert looks_like_feed(response(html, "text/html")) == (False, 0)

    def test_rejects_empty_feed(self):
        empty = b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title></channel></rss>'
        assert looks_like_feed(response(empty)) == (False, 0)

    def test_rejects_error_status(self):
        assert looks_like_feed(response(FEED_BODY, status=404)) == (False, 0)

    def test_accepts_xml_body_despite_wrong_content_type(self):
        # Manche Server liefern Feeds als text/plain aus.
        ok, _ = looks_like_feed(response(FEED_BODY, "text/plain"))
        assert ok


class TestAutodiscovery:
    def test_finds_rss_and_atom_links(self):
        html = (
            '<link rel="alternate" type="application/rss+xml" href="/rss/news.rss">'
            '<link rel="stylesheet" href="/a.css">'
            "<link rel=alternate type=\"application/atom+xml\" href=\"/atom\">"
        )
        assert discover_feed_links(html, "https://x.de/news") == [
            "https://x.de/rss/news.rss",
            "https://x.de/atom",
        ]

    def test_ignores_pages_without_feeds(self):
        assert discover_feed_links("<html><body>nix</body></html>", "https://x.de") == []
