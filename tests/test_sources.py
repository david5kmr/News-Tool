from pathlib import Path

import pytest
import yaml

from mi.sources import LOCK_FILENAME, load

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestRealRegistry:
    """sources.yaml ist Konfiguration, aber ein Tippfehler darin kostet
    einen ganzen Tageslauf."""

    @pytest.fixture
    def registry(self):
        return load(REPO_ROOT / "sources.yaml")

    def test_loads(self, registry):
        assert len(registry.sources) > 10

    def test_every_source_has_a_reason(self, registry):
        missing = [s.id for s in registry.sources if not (s.why or s.notes)]
        assert missing == []

    def test_nothing_is_usable_before_verification(self, registry):
        """Bauschritt 1 ist offen — solange darf nichts stillschweigend laufen."""
        assert [s.id for s in registry.sources if s.is_usable] == []

    def test_alert_sources_also_run_in_the_daily_pass(self, registry):
        daily = {s.id for s in registry.for_cadence("daily")}
        alert_only = {s.id for s in registry.sources if s.cadence == "alerts"}
        assert alert_only <= daily

    def test_competitors_have_pages(self, registry):
        assert all(c.pages for c in registry.competitors)

    def test_paywalled_source_is_disabled(self, registry):
        handelsblatt = registry.by_id("handelsblatt-inside-digital-health")
        assert handelsblatt is not None and not handelsblatt.enabled


class TestValidation:
    def _write(self, tmp_path, data):
        path = tmp_path / "sources.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return path

    def test_rejects_unknown_kind(self, tmp_path):
        path = self._write(tmp_path, {"sources": [
            {"id": "a", "name": "A", "kind": "telepathie"}
        ]})
        with pytest.raises(ValueError, match="kind"):
            load(path)

    def test_rejects_duplicate_ids(self, tmp_path):
        path = self._write(tmp_path, {"sources": [
            {"id": "a", "name": "A", "kind": "rss"},
            {"id": "a", "name": "B", "kind": "rss"},
        ]})
        with pytest.raises(ValueError, match="Doppelte"):
            load(path)

    def test_rejects_missing_name(self, tmp_path):
        path = self._write(tmp_path, {"sources": [{"id": "a", "kind": "rss"}]})
        with pytest.raises(ValueError, match="name"):
            load(path)


class TestLockfile:
    def test_lock_promotes_a_source_to_usable(self, tmp_path):
        source_path = tmp_path / "sources.yaml"
        source_path.write_text(
            yaml.safe_dump({"sources": [
                {"id": "a", "name": "A", "kind": "rss", "status": "unverified"}
            ]}),
            encoding="utf-8",
        )
        (tmp_path / LOCK_FILENAME).write_text(
            yaml.safe_dump({"sources": {"a": {
                "status": "verified", "url": "https://x.de/feed", "kind": "rss",
            }}}),
            encoding="utf-8",
        )
        source = load(source_path).by_id("a")
        assert source.is_usable and source.url == "https://x.de/feed"

    def test_lock_does_not_override_manual(self, tmp_path):
        source_path = tmp_path / "sources.yaml"
        source_path.write_text(
            yaml.safe_dump({"sources": [
                {"id": "a", "name": "A", "kind": "html", "status": "manual",
                 "enabled": False}
            ]}),
            encoding="utf-8",
        )
        (tmp_path / LOCK_FILENAME).write_text(
            yaml.safe_dump({"sources": {"a": {
                "status": "verified", "url": "https://x.de/feed",
            }}}),
            encoding="utf-8",
        )
        source = load(source_path).by_id("a")
        assert source.status == "manual" and source.url is None
