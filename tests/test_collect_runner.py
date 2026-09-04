"""Integrationstest des Sammelpfads mit gestubbtem Netz: Von der Feed-Antwort
bis zur Zeile in der Datenbank, inklusive Dedupe."""

import pytest

from mi import db
from mi.collect import runner
from mi.collect.rss import parse_feed
from mi.net import Response
from mi.sources import Source, SourceRegistry


def feed_with(*titles: str) -> bytes:
    items = "".join(
        f"<item><title>{t}</title><link>https://example.de/n/{i}</link>"
        f"<pubDate>Wed, 03 Sep 2026 08:00:00 +0000</pubDate>"
        f"<description>Volltext zur Meldung.</description></item>"
        for i, t in enumerate(titles)
    )
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>F</title>'
        f"{items}</channel></rss>"
    ).encode()


@pytest.fixture
def registry():
    return SourceRegistry(
        sources=[Source(
            id="testfeed", name="Testfeed", kind="rss", status="verified",
            url="https://example.de/feed",
        )],
        competitors=[], defaults={}, path=None,
    )


def stub_collector(payload: bytes, *, status: int = 200, error: str | None = None):
    def collector(source, fetcher, **kwargs):
        resp = Response(url=source.url, status=status, text=payload.decode(),
                        content=payload, headers={"ETag": "etag-1"}, error=error)
        items = parse_feed(payload, source) if resp.ok else []
        return items, resp

    return collector


@pytest.fixture
def stubbed(monkeypatch):
    def install(collector):
        monkeypatch.setitem(runner.COLLECTORS, "rss", collector)

    return install


class TestCollect:
    def test_stores_items_and_records_source_state(
        self, conn, config, registry, stubbed
    ):
        stubbed(stub_collector(feed_with("Erste Meldung", "Zweite Meldung")))
        stats = runner.collect(conn, registry, config)
        assert stats.stored == 2 and stats.sources_run == 1
        assert db.get_source_state(conn, "testfeed")["etag"] == "etag-1"

    def test_second_run_stores_nothing_new(self, conn, config, registry, stubbed):
        stubbed(stub_collector(feed_with("Erste Meldung", "Zweite Meldung")))
        runner.collect(conn, registry, config)
        stats = runner.collect(conn, registry, config)
        assert stats.stored == 0 and stats.duplicates_url == 2

    def test_reworded_headline_is_flagged_as_a_duplicate(
        self, conn, config, registry, stubbed
    ):
        stubbed(stub_collector(feed_with("Kreiskrankenhaus Ehingen meldet Insolvenz an")))
        runner.collect(conn, registry, config)

        stubbed(stub_collector(
            b'<?xml version="1.0"?><rss version="2.0"><channel><title>F</title>'
            b"<item><title>Insolvenz: Das Kreiskrankenhaus Ehingen meldet an</title>"
            b"<link>https://example.de/andere-quelle/9</link></item>"
            b"</channel></rss>"
        ))
        stats = runner.collect(conn, registry, config)
        assert stats.duplicates_title == 1
        # Gespeichert wird trotzdem — das Archiv soll vollstaendig bleiben.
        assert stats.stored == 1
        assert conn.execute(
            "SELECT count(*) AS n FROM items WHERE dedupe_of IS NOT NULL"
        ).fetchone()["n"] == 1

    def test_duplicates_are_kept_out_of_the_scoring_queue(
        self, conn, config, registry, stubbed
    ):
        stubbed(stub_collector(feed_with("Kreiskrankenhaus Ehingen meldet Insolvenz an")))
        runner.collect(conn, registry, config)
        stubbed(stub_collector(
            b'<?xml version="1.0"?><rss version="2.0"><channel><title>F</title>'
            b"<item><title>Insolvenz: Das Kreiskrankenhaus Ehingen meldet an</title>"
            b"<link>https://example.de/andere-quelle/9</link></item>"
            b"</channel></rss>"
        ))
        runner.collect(conn, registry, config)
        assert len(db.unscored_items(conn)) == 1

    def test_unverified_sources_are_skipped(self, conn, config, registry, stubbed):
        registry.sources[0].status = "unverified"
        stubbed(stub_collector(feed_with("Eine Meldung")))
        stats = runner.collect(conn, registry, config)
        assert stats.stored == 0 and stats.sources_skipped == ["testfeed"]

    def test_allow_unverified_overrides_the_skip(
        self, conn, config, registry, stubbed
    ):
        registry.sources[0].status = "unverified"
        stubbed(stub_collector(feed_with("Eine Meldung")))
        assert runner.collect(
            conn, registry, config, allow_unverified=True
        ).stored == 1

    def test_a_failing_source_is_recorded_not_raised(
        self, conn, config, registry, stubbed
    ):
        stubbed(stub_collector(b"", status=0, error="Zeitueberschreitung"))
        stats = runner.collect(conn, registry, config)
        assert stats.sources_failed == ["testfeed"]
        assert db.get_source_state(conn, "testfeed")["error_streak"] == 1

    def test_an_exception_in_one_source_does_not_abort_the_run(
        self, conn, config, registry, stubbed
    ):
        def boom(source, fetcher, **kwargs):
            raise RuntimeError("Parser kaputt")

        stubbed(boom)
        stats = runner.collect(conn, registry, config)
        assert stats.sources_failed == ["testfeed"]

    def test_stale_items_are_dropped(self, conn, config, registry, stubbed):
        old = (
            '<?xml version="1.0"?><rss version="2.0"><channel><title>F</title>'
            "<item><title>Uralte Meldung</title><link>https://example.de/alt</link>"
            "<pubDate>Mon, 01 Jan 2024 08:00:00 +0000</pubDate></item>"
            "</channel></rss>"
        ).encode()
        stubbed(stub_collector(old))
        stats = runner.collect(conn, registry, config)
        assert stats.too_old == 1 and stats.stored == 0

    def test_not_modified_short_circuits(self, conn, config, registry, stubbed):
        def not_modified(source, fetcher, **kwargs):
            return [], Response(url=source.url, status=304, text="", content=b"",
                                headers={})

        stubbed(not_modified)
        stats = runner.collect(conn, registry, config)
        assert stats.not_modified == 1 and stats.stored == 0
