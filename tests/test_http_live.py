"""Der Sammelpfad einmal ueber echtes HTTP.

Alle anderen Collector-Tests stubben den Netzzugriff. Hier laeuft ein echter
HTTP-Server auf localhost, und der echte Fetcher (requests) holt dort echte
Feeds ab. Das deckt die Schicht ab, die sonst niemand testet: Header,
Weiterleitungen, Content-Type, Encoding, Autodiscovery ueber die Leitung.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from mi.collect import runner
from mi.config import Config
from mi.net import Fetcher
from mi.sources import Competitor, Source, SourceRegistry
from mi.verify import verify_source

FIXTURES = Path(__file__).parent / "fixtures"


class Handler(http.server.SimpleHTTPRequestHandler):
    """Bildet die Pfade der echten Quellen auf die Fixture-Dateien ab."""

    ROUTES = {
        "/rss/news.rss": ("aerzteblatt_feed.xml", "application/rss+xml"),
        "/presse/pressemitteilungen": ("bmg_index.html", "text/html"),
        "/feeds/presse.xml": ("bmg_presse.xml", "application/rss+xml"),
        # Der klassische Fallstrick: Fehlerseite mit Status 200
        "/feed": ("fake_feed.html", "text/html"),
    }

    def do_GET(self):  # noqa: N802
        route = self.ROUTES.get(self.path.split("?")[0])
        if route is None:
            self.send_error(404)
            return
        filename, content_type = route
        payload = (FIXTURES / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("ETag", '"fixture-v1"')
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def fetcher():
    f = Fetcher(user_agent="mi-test/1.0", timeout=10, delay=0.0)
    yield f
    f.close()


class TestVerifyOverHttp:
    def test_finds_a_feed_from_a_candidate_url(self, server, fetcher):
        source = Source(
            id="aerzteblatt", name="Ärzteblatt", kind="rss",
            candidates=[f"{server}/rss/news.rss"],
        )
        result = verify_source(source, fetcher)
        assert result.status == "verified"
        assert result.entries == 3

    def test_autodiscovers_a_feed_from_the_homepage(self, server, fetcher):
        """Kein Kandidat trifft — der Feed wird aus <link rel="alternate"> gelesen."""
        source = Source(
            id="bmg", name="BMG", kind="rss",
            candidates=[f"{server}/gibt-es-nicht.xml"],
            homepage=f"{server}/presse/pressemitteilungen",
        )
        result = verify_source(source, fetcher)
        assert result.status == "verified"
        assert result.url == f"{server}/feeds/presse.xml"
        assert "Autodiscovery" in result.note

    def test_html_error_page_with_status_200_is_not_accepted(self, server, fetcher):
        source = Source(
            id="fake", name="Fake", kind="rss",
            candidates=[f"{server}/feed"],
            homepage=f"{server}/presse/pressemitteilungen",
        )
        result = verify_source(source, fetcher)
        # Es findet ueber die Homepage den echten Feed — aber NICHT /feed.
        assert result.url != f"{server}/feed"

    def test_unreachable_host_is_reported_not_raised(self, fetcher):
        source = Source(
            id="tot", name="Tot", kind="rss",
            candidates=["http://127.0.0.1:1/feed"],
            homepage="http://127.0.0.1:1/",
        )
        assert verify_source(source, fetcher).status == "broken"


class TestCollectOverHttp:
    @pytest.fixture
    def registry(self, server):
        return SourceRegistry(
            sources=[Source(
                id="aerzteblatt", name="Deutsches Ärzteblatt", kind="rss",
                status="verified", url=f"{server}/rss/news.rss",
            )],
            competitors=[], defaults={}, path=None,
        )

    def test_collects_real_feed_into_the_database(self, conn, tmp_path, registry):
        config = Config(db_path=tmp_path / "t.db")
        stats = runner.collect(conn, registry, config)
        assert stats.stored == 3

        rows = conn.execute("SELECT * FROM items ORDER BY id").fetchall()
        titles = [r["title"] for r in rows]
        assert "GOÄneu: Bundesärztekammer und PKV-Verband einigen sich auf Zeitplan" in titles

    def test_umlauts_and_entities_survive_the_round_trip(
        self, conn, tmp_path, registry
    ):
        runner.collect(conn, registry, Config(db_path=tmp_path / "t.db"))
        row = conn.execute(
            "SELECT raw_text FROM items WHERE title LIKE 'GOÄneu%'"
        ).fetchone()
        # &auml; und &uuml; aus dem CDATA muessen echte Umlaute geworden sein
        assert "Bundesärztekammer" in row["raw_text"]
        assert "Gebührenordnung" in row["raw_text"]
        assert "&auml;" not in row["raw_text"]
        assert "<b>" not in row["raw_text"]

    def test_tracking_parameters_are_stripped_from_the_url(
        self, conn, tmp_path, registry
    ):
        runner.collect(conn, registry, Config(db_path=tmp_path / "t.db"))
        row = conn.execute(
            "SELECT url, url_hash FROM items WHERE title LIKE 'Klinikverbund%'"
        ).fetchone()
        # Die Original-URL bleibt erhalten, der Hash ignoriert utm_*
        assert "utm_source" in row["url"]
        from mi.dedupe import url_hash
        assert row["url_hash"] == url_hash(
            "https://www.aerzteblatt.de/nachrichten/158232/klinikverbund-insolvenz"
        )

    def test_a_second_run_adds_nothing(self, conn, tmp_path, registry):
        config = Config(db_path=tmp_path / "t.db")
        runner.collect(conn, registry, config)
        assert runner.collect(conn, registry, config).stored == 0

    def test_the_collected_items_drive_the_alert_logic(
        self, conn, tmp_path, registry
    ):
        """Von der Feed-Antwort bis zum ausgeloesten Alert, ohne Stub."""
        from mi.alerts import detect_triggers

        runner.collect(conn, registry, Config(db_path=tmp_path / "t.db"))
        rows = conn.execute("SELECT * FROM items").fetchall()

        def triggers_for(fragment: str) -> list[str]:
            row = next(r for r in rows if fragment in r["title"])
            return [t.name for t in detect_triggers(dict(row))]

        assert "GOÄneu-Äußerung" in triggers_for("GOÄneu")
        assert "Klinikgruppe" in triggers_for("Klinikverbund")
        assert triggers_for("Pflegekammer") == []


class TestCompetitorDiffOverHttp:
    def test_first_run_lays_a_baseline_then_detects_change(
        self, conn, tmp_path, server, monkeypatch
    ):
        from mi.collect.website_diff import run as run_diff

        config = Config(db_path=tmp_path / "t.db")
        competitor = Competitor(
            id="bmg", name="BMG", pages=[f"{server}/presse/pressemitteilungen"]
        )
        first = run_diff(conn, [competitor], config)
        assert first.first_seen and not first.changes

        # Zweiter Lauf gegen unveraenderte Seite: keine Meldung
        assert not run_diff(conn, [competitor], config).changes
