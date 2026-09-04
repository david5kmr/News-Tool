"""Der Digest ist die Stelle, an der ein halluzinierter Link den ganzen Brief
entwertet — die URL muss aus der DB kommen, nie aus der Modellantwort."""

import pytest

from mi import db
from mi.digest import _resolve_items
from mi.render import digest_html, digest_subject, digest_text
from mi.sources import Source, SourceRegistry


@pytest.fixture
def registry():
    return SourceRegistry(
        sources=[Source(id="aerzteblatt", name="Deutsches Ärzteblatt", kind="rss")],
        competitors=[],
        defaults={},
        path=None,
    )


@pytest.fixture
def candidates(conn):
    item_id = db.insert_item(conn, {
        "url": "https://aerzteblatt.de/n/1", "url_hash": "h1",
        "title": "GOÄneu Zeitplan", "title_norm": "goaeneu zeitplan",
        "source": "aerzteblatt", "published_at": "2026-09-03T06:00:00+00:00",
    })
    db.set_score(conn, item_id, score=9, summary="Zusammenfassung", reason="r",
                 topics=["goae"], entities=[])
    return db.digest_candidates(conn, 4)


class TestResolveItems:
    def test_uses_the_database_url_not_the_model(self, candidates, registry):
        item_id = candidates[0]["id"]
        resolved = _resolve_items(candidates, [{
            "item_id": item_id, "headline": "H", "what": "W", "einordnung": "E",
            "url": "https://erfunden.example/halluziniert",
        }], registry)
        assert resolved[0]["url"] == "https://aerzteblatt.de/n/1"

    def test_resolves_the_readable_source_name(self, candidates, registry):
        resolved = _resolve_items(candidates, [{
            "item_id": candidates[0]["id"], "headline": "H", "what": "W",
            "einordnung": "E",
        }], registry)
        assert resolved[0]["source_name"] == "Deutsches Ärzteblatt"

    def test_drops_unknown_ids(self, candidates, registry):
        resolved = _resolve_items(candidates, [
            {"item_id": 99999, "headline": "H", "what": "W", "einordnung": "E"}
        ], registry)
        assert resolved == []

    def test_drops_repeated_ids(self, candidates, registry):
        entry = {"item_id": candidates[0]["id"], "headline": "H", "what": "W",
                 "einordnung": "E"}
        assert len(_resolve_items(candidates, [entry, entry], registry)) == 1

    def test_falls_back_to_the_stored_title(self, candidates, registry):
        resolved = _resolve_items(candidates, [{
            "item_id": candidates[0]["id"], "headline": "", "what": "",
            "einordnung": "E",
        }], registry)
        assert resolved[0]["headline"] == "GOÄneu Zeitplan"
        assert resolved[0]["what"] == "Zusammenfassung"


ITEMS = [{
    "headline": "GOÄneu: Zeitplan steht",
    "source_name": "Deutsches Ärzteblatt",
    "published_at": "2026-09-03T08:00:00+00:00",
    "what": "Was passiert ist.",
    "einordnung": "Was das für uns bedeutet.",
    "url": "https://aerzteblatt.de/n/1",
}]


class TestRendering:
    def test_subject_matches_the_spec_format(self):
        from datetime import datetime
        subject = digest_subject("GOÄneu-Zeitplan, 2 Klinikübernahmen",
                                 today=datetime(2026, 9, 4))
        assert subject == "Marktbrief 04.09. — GOÄneu-Zeitplan, 2 Klinikübernahmen"

    def test_subject_survives_an_empty_suffix(self):
        from datetime import datetime
        assert digest_subject("", today=datetime(2026, 9, 4)) == "Marktbrief 04.09."

    def test_text_carries_every_required_field(self):
        text = digest_text(ITEMS, "Diese Woche.", "meta")
        for fragment in ("GOÄneu: Zeitplan steht", "Deutsches Ärzteblatt",
                         "03.09.2026", "Was passiert ist.",
                         "Einordnung: Was das für uns bedeutet.",
                         "https://aerzteblatt.de/n/1", "Diese Woche im Blick"):
            assert fragment in text

    def test_html_escapes_titles(self):
        item = dict(ITEMS[0], headline='<script>alert("x")</script>')
        html = digest_html([item], "", "meta", "Betreff")
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html

    def test_html_without_week_ahead_omits_the_block(self):
        assert "Diese Woche im Blick" not in digest_html(ITEMS, "", "m", "B")
