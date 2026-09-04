import pytest

from mi.collect.base import clean_text, is_recent, parse_date
from mi.collect.google_news import query_url
from mi.collect.html import parse_listing
from mi.collect.rss import parse_feed
from mi.sources import Source

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Testfeed</title>
  <item>
    <title>GOAEneu: Verhandlungen gehen weiter</title>
    <link>https://example.de/n/1</link>
    <pubDate>Wed, 03 Sep 2026 08:00:00 +0200</pubDate>
    <description>&lt;p&gt;Die BAEK teilte mit.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Zweite Meldung</title>
    <link>https://example.de/n/2</link>
    <pubDate>Wed, 03 Sep 2026 09:00:00 +0200</pubDate>
  </item>
  <item><title>Ohne Link</title></item>
</channel></rss>
"""


@pytest.fixture
def source():
    return Source(id="test", name="Test", kind="rss", url="https://example.de/feed")


class TestRss:
    def test_parses_entries_and_skips_incomplete(self, source):
        items = parse_feed(FEED, source)
        assert [i.url for i in items] == [
            "https://example.de/n/1",
            "https://example.de/n/2",
        ]

    def test_normalises_date_to_utc(self, source):
        assert parse_feed(FEED, source)[0].published_at == "2026-09-03T06:00:00+00:00"

    def test_strips_html_from_description(self, source):
        assert parse_feed(FEED, source)[0].raw_text == "Die BAEK teilte mit."

    def test_respects_max_items(self, source):
        source.max_items_per_run = 1
        assert len(parse_feed(FEED, source)) == 1


LISTING = """
<html><body><main>
  <article><a href="/nachricht/klinik-insolvenz-in-ulm">Klinik in Ulm meldet Insolvenz</a>
    <time datetime="2026-09-02">2. September 2026</time></article>
  <article><a href="/nachricht/goaeneu-zeitplan">GOAEneu Zeitplan steht</a></article>
  <a href="/impressum">Impressum</a>
  <a href="#top">Nach oben</a>
  <a href="https://werbepartner.de/anzeige">Eine Anzeige von einem Partner</a>
</main></body></html>
"""


class TestHtmlListing:
    @pytest.fixture
    def source(self):
        return Source(id="t", name="T", kind="html", html={"item_selector": "main a"})

    def test_collects_articles(self, source):
        items = parse_listing(LISTING, "https://example.de/news", source)
        assert [i.title for i in items] == [
            "Klinik in Ulm meldet Insolvenz",
            "GOAEneu Zeitplan steht",
        ]

    def test_resolves_relative_links(self, source):
        items = parse_listing(LISTING, "https://example.de/news", source)
        assert items[0].url == "https://example.de/nachricht/klinik-insolvenz-in-ulm"

    def test_reads_nearby_date(self, source):
        items = parse_listing(LISTING, "https://example.de/news", source)
        assert items[0].published_at.startswith("2026-09-02")

    def test_drops_boilerplate_anchors_and_foreign_domains(self, source):
        urls = [i.url for i in parse_listing(LISTING, "https://example.de/news", source)]
        assert not any("impressum" in u or "werbepartner" in u for u in urls)


class TestGoogleNews:
    def test_query_url_carries_locale_and_window(self):
        url = query_url("Krankenhaus Insolvenz")
        assert "hl=de&gl=DE&ceid=DE:de" in url
        assert "when%3A7d" in url


class TestHelpers:
    def test_clean_text_drops_scripts_and_nav(self):
        assert clean_text("<div><script>x</script><p>Text</p><nav>Menue</nav></div>") == "Text"

    def test_clean_text_truncates(self):
        assert clean_text("a" * 100, limit=10).startswith("a" * 10)

    def test_parse_date_returns_none_for_garbage(self):
        assert parse_date("kein Datum") is None

    def test_items_without_date_count_as_recent(self):
        assert is_recent(None, max_age_days=7) is True

    def test_old_items_are_rejected(self):
        assert is_recent("2020-01-01T00:00:00+00:00", max_age_days=7) is False
