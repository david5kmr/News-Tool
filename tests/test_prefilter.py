"""Der Prefilter muss eine kaputte Modellantwort ueberleben — er laeuft
unbeaufsichtigt um sechs Uhr morgens."""

import json

import pytest

from mi import db
from mi.llm import LLM, LLMError, _parse_json, load_prompt, interest_profile
from mi.prefilter import PrefilterStats, _apply_ratings, _render_batch, run


@pytest.fixture
def rows(conn):
    for i in range(3):
        db.insert_item(conn, {
            "url": f"https://x.de/{i}", "url_hash": f"h{i}", "title": f"Meldung {i}",
            "title_norm": f"meldung {i}", "source": "test",
            "raw_text": "Volltext " * 50,
        })
    return db.unscored_items(conn)


class FakeLLM(LLM):
    """Ersetzt nur den Netzaufruf — der Rest der Klasse laeuft echt."""

    def __init__(self, payload):
        super().__init__(api_key="test")
        self.payload = payload
        self.calls = 0

    def structured(self, **kwargs):
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class TestApplyRatings:
    def test_stores_score_summary_and_entities(self, conn, rows):
        stats = PrefilterStats()
        _apply_ratings(conn, rows, {
            int(rows[0]["id"]): {
                "score": 8, "summary": "S", "reason": "R",
                "topics": ["goae"], "entities": ["Dedalus"],
            }
        }, stats)
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (rows[0]["id"],)
        ).fetchone()
        assert row["score"] == 8 and json.loads(row["entities"]) == ["Dedalus"]
        assert stats.scored == 1
        entity = conn.execute("SELECT * FROM entities").fetchone()
        assert entity["name"] == "Dedalus"

    def test_clamps_out_of_range_scores(self, conn, rows):
        stats = PrefilterStats()
        _apply_ratings(conn, rows[:1], {
            int(rows[0]["id"]): {"score": 47, "summary": "", "reason": "",
                                 "topics": [], "entities": []}
        }, stats)
        assert conn.execute(
            "SELECT score FROM items WHERE id = ?", (rows[0]["id"],)
        ).fetchone()["score"] == 10

    def test_counts_missing_items_as_failed(self, conn, rows):
        stats = PrefilterStats()
        _apply_ratings(conn, rows, {}, stats)
        assert stats.failed == len(rows) and stats.scored == 0

    def test_drops_invented_topics(self, conn, rows):
        stats = PrefilterStats()
        _apply_ratings(conn, rows[:1], {
            int(rows[0]["id"]): {"score": 5, "summary": "", "reason": "",
                                 "topics": ["quatsch", "goae"], "entities": []}
        }, stats)
        assert json.loads(conn.execute(
            "SELECT topics FROM items WHERE id = ?", (rows[0]["id"],)
        ).fetchone()["topics"]) == ["goae"]

    def test_non_numeric_score_is_a_failure_not_a_crash(self, conn, rows):
        stats = PrefilterStats()
        _apply_ratings(conn, rows[:1], {
            int(rows[0]["id"]): {"score": "hoch", "summary": "", "reason": "",
                                 "topics": [], "entities": []}
        }, stats)
        assert stats.failed == 1


class TestRun:
    def test_a_failing_batch_does_not_abort_the_run(self, conn, rows, config):
        stats = run(conn, config, FakeLLM(LLMError("kaputt")), limit=10)
        assert stats.failed == len(rows) and stats.scored == 0

    def test_scores_are_written_and_counted(self, conn, rows, config):
        payload = {"ratings": [
            {"id": int(r["id"]), "score": 6, "summary": "S", "reason": "R",
             "topics": ["goae"], "entities": []}
            for r in rows
        ]}
        stats = run(conn, config, FakeLLM(payload), limit=10)
        assert stats.scored == len(rows)
        assert stats.distribution == {6: len(rows)}

    def test_nothing_to_do_makes_no_api_call(self, conn, config):
        llm = FakeLLM({"ratings": []})
        assert run(conn, config, llm, limit=10).batches == 0
        assert llm.calls == 0


class TestBatchRendering:
    def test_marks_items_without_text(self, conn):
        db.insert_item(conn, {
            "url": "https://x.de/1", "url_hash": "h1", "title": "Nur ein Titel",
            "title_norm": "t", "source": "test",
        })
        rendered = _render_batch(db.unscored_items(conn))
        assert "kein Text im Feed" in rendered

    def test_truncates_long_text(self, conn):
        db.insert_item(conn, {
            "url": "https://x.de/1", "url_hash": "h1", "title": "T",
            "title_norm": "t", "source": "test", "raw_text": "w " * 5000,
        })
        assert "[…]" in _render_batch(db.unscored_items(conn))


class TestJsonParsing:
    @pytest.mark.parametrize("raw", [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Gerne! Hier das Ergebnis:\n{"a": 1}\nViel Erfolg.',
    ])
    def test_recovers_json_from_chatty_answers(self, raw):
        assert _parse_json(raw) == {"a": 1}

    def test_raises_on_prose(self):
        with pytest.raises(LLMError):
            _parse_json("Dazu kann ich nichts sagen.")


class TestPrompts:
    def test_prefilter_prompt_embeds_the_profile(self):
        prompt = load_prompt("prefilter", PROFIL=interest_profile())
        assert "Co-Founder und Commercial Lead" in prompt
        assert "{{" not in prompt

    def test_unfilled_placeholder_is_caught(self):
        with pytest.raises(ValueError, match="Platzhalter"):
            load_prompt("digest", PROFIL="x")
