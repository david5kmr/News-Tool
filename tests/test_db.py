import json

from mi import db


class TestItems:
    def test_insert_and_duplicate(self, conn):
        item = {
            "url": "https://x.de/1", "url_hash": "h1", "title": "Titel",
            "title_norm": "titel", "source": "s",
        }
        assert db.insert_item(conn, item) == 1
        assert db.insert_item(conn, item) is None

    def test_set_score_serialises_lists(self, conn):
        item_id = db.insert_item(conn, {
            "url": "https://x.de/1", "url_hash": "h1", "title": "T",
            "title_norm": "t", "source": "s",
        })
        db.set_score(conn, item_id, score=7, summary="s", reason="r",
                     topics=["goae"], entities=["BÄK"])
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["score"] == 7
        assert json.loads(row["topics"]) == ["goae"]
        assert json.loads(row["entities"]) == ["BÄK"]

    def test_unscored_excludes_duplicates(self, conn):
        first = db.insert_item(conn, {
            "url": "https://x.de/1", "url_hash": "h1", "title": "T",
            "title_norm": "t", "source": "s",
        })
        db.insert_item(conn, {
            "url": "https://x.de/2", "url_hash": "h2", "title": "T",
            "title_norm": "t", "source": "s", "dedupe_of": first,
        })
        assert [r["id"] for r in db.unscored_items(conn)] == [first]

    def test_digest_candidates_respect_threshold_and_sent_state(self, conn):
        for i, score in enumerate([2, 5, 9], start=1):
            item_id = db.insert_item(conn, {
                "url": f"https://x.de/{i}", "url_hash": f"h{i}", "title": f"T{i}",
                "title_norm": f"t{i}", "source": "s",
            })
            db.set_score(conn, item_id, score=score, summary=None, reason=None,
                         topics=[], entities=[])
        assert len(db.digest_candidates(conn, 4)) == 2
        db.mark_digested(conn, [r["id"] for r in db.digest_candidates(conn, 4)])
        assert db.digest_candidates(conn, 4) == []


class TestAlerts:
    def test_recording_an_alert_flags_the_item(self, conn):
        item_id = db.insert_item(conn, {
            "url": "https://x.de/1", "url_hash": "h1", "title": "T",
            "title_norm": "t", "source": "s",
        })
        db.set_score(conn, item_id, score=9, summary=None, reason=None,
                     topics=[], entities=[])
        assert db.alerts_sent_today(conn) == 0
        db.record_alert(conn, item_id, "Watchlist")
        assert db.alerts_sent_today(conn) == 1
        assert db.alert_candidates(conn, 8) == []

    def test_record_alert_is_idempotent(self, conn):
        item_id = db.insert_item(conn, {
            "url": "https://x.de/1", "url_hash": "h1", "title": "T",
            "title_norm": "t", "source": "s",
        })
        db.record_alert(conn, item_id, "a")
        db.record_alert(conn, item_id, "b")
        assert db.alerts_sent_today(conn) == 1


class TestEntities:
    def test_mention_count_increments(self, conn):
        for i in (1, 2):
            item_id = db.insert_item(conn, {
                "url": f"https://x.de/{i}", "url_hash": f"h{i}", "title": "T",
                "title_norm": f"t{i}", "source": "s",
            })
            db.touch_entity(conn, item_id, "Dedalus", "company")
        row = conn.execute(
            "SELECT * FROM entities WHERE name_norm = 'dedalus'"
        ).fetchone()
        assert row["mention_count"] == 2
        assert row["type"] == "company"


class TestSourceState:
    def test_error_streak_grows_then_resets(self, conn):
        assert db.record_source_error(conn, "s1", "boom") == 1
        assert db.record_source_error(conn, "s1", "boom") == 2
        db.record_source_success(conn, "s1", etag="e", last_modified=None, items_seen=3)
        assert db.get_source_state(conn, "s1")["error_streak"] == 0


class TestFullText:
    def test_index_follows_updates(self, conn):
        item_id = db.insert_item(conn, {
            "url": "https://x.de/1", "url_hash": "h1",
            "title": "GOÄneu Verhandlungen", "title_norm": "t", "source": "s",
        })
        db.set_score(conn, item_id, score=8, summary="BÄK und PKV verhandeln",
                     reason="r", topics=[], entities=[])
        hits = conn.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", ('"verhandeln"',)
        ).fetchall()
        assert [h["rowid"] for h in hits] == [item_id]

    def test_delete_removes_from_index(self, conn):
        item_id = db.insert_item(conn, {
            "url": "https://x.de/1", "url_hash": "h1", "title": "Einzigartig",
            "title_norm": "t", "source": "s",
        })
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        hits = conn.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", ('"Einzigartig"',)
        ).fetchall()
        assert hits == []
