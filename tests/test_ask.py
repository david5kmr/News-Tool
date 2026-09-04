import pytest

from mi import db
from mi.ask import fts_query, search
from mi.monthly import previous_month
from datetime import date


class TestFtsQuery:
    def test_builds_an_or_query(self):
        assert fts_query("Stand GOÄneu Verhandlungen") == (
            '"Stand" OR "GOÄneu" OR "Verhandlungen"'
        )

    def test_strips_fts_operators_from_user_input(self):
        query = fts_query('Was ist mit "GOÄneu" (Stand)?')
        assert "(" not in query and query.count('"') % 2 == 0

    def test_empty_question_yields_empty_query(self):
        assert fts_query("???") == ""


class TestSearch:
    @pytest.fixture
    def seeded(self, conn):
        item_id = db.insert_item(conn, {
            "url": "https://x.de/1", "url_hash": "h1",
            "title": "GOÄneu Verhandlungen stocken", "title_norm": "t",
            "source": "baek", "raw_text": "BÄK und PKV verhandeln über Steigerungsfaktoren.",
        })
        db.set_score(conn, item_id, score=9, summary="BÄK und PKV verhandeln.",
                     reason="r", topics=["goae"], entities=[])
        return conn

    def test_finds_by_title(self, seeded):
        assert [r["title"] for r in search(seeded, "GOÄneu")] == [
            "GOÄneu Verhandlungen stocken"
        ]

    def test_finds_by_body_text(self, seeded):
        assert len(search(seeded, "Steigerungsfaktoren")) == 1

    def test_unrelated_question_finds_nothing(self, seeded):
        assert search(seeded, "Fussballergebnisse") == []

    def test_empty_question_finds_nothing(self, seeded):
        assert search(seeded, "?!") == []


class TestPreviousMonth:
    @pytest.mark.parametrize("today,expected", [
        (date(2026, 9, 4), "2026-08"),
        (date(2026, 1, 1), "2025-12"),
        (date(2026, 3, 31), "2026-02"),
    ])
    def test_rolls_over_the_year(self, today, expected):
        assert previous_month(today) == expected
