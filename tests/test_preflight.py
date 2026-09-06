"""Die Vorflugkontrolle entscheidet, ob ein Cron-Lauf ueberhaupt startet.
Sagt sie faelschlich "startklar", scheitert der Lauf mit einer Fehlermail —
genau das, was sie verhindern soll."""

import pytest

from mi.config import Config, MailConfig
from mi.preflight import render, run


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    lock = tmp_path / "sources.lock.yaml"
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "sources:\n  - id: a\n    name: A\n    kind: rss\n    status: unverified\n",
        encoding="utf-8",
    )
    lock.write_text(
        "sources:\n  a:\n    status: verified\n    url: https://x.de/feed\n",
        encoding="utf-8",
    )
    return Config(
        sources_path=sources,
        anthropic_api_key="sk-ant-test",
        mail=MailConfig(backend="resend", recipients=("du@example.de",),
                        resend_api_key="re_test"),
    )


def names_failing(checks):
    return {c.name for c in checks if not c.ok}


class TestFullyConfigured:
    def test_everything_green(self, configured):
        assert names_failing(run(configured)) == set()

    def test_render_says_it_can_start(self, configured):
        assert "Der Lauf kann starten." in render(run(configured))


class TestMissingPieces:
    def test_unverified_sources_block_the_run(self, tmp_path, configured):
        configured.sources_path.write_text(
            "sources:\n  - id: b\n    name: B\n    kind: rss\n    status: unverified\n",
            encoding="utf-8",
        )
        # Kein Lock-Eintrag fuer b -> nichts nutzbar
        (tmp_path / "sources.lock.yaml").write_text("sources: {}\n", encoding="utf-8")
        assert "Quellen" in names_failing(run(configured))

    def test_missing_api_key(self, configured, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(
            "mi.preflight.Path.exists", lambda self: False, raising=False
        )
        bare = Config(sources_path=configured.sources_path, anthropic_api_key=None,
                      mail=configured.mail)
        assert "Anthropic" in names_failing(run(bare))

    def test_resend_without_key(self, configured):
        configured = Config(
            sources_path=configured.sources_path,
            anthropic_api_key="sk-ant-test",
            mail=MailConfig(backend="resend", recipients=("du@example.de",),
                            resend_api_key=None),
        )
        assert "Versand" in names_failing(run(configured))

    def test_no_recipient(self, configured):
        configured = Config(
            sources_path=configured.sources_path,
            anthropic_api_key="sk-ant-test",
            mail=MailConfig(backend="resend", recipients=(), resend_api_key="re_x"),
        )
        assert "Versand" in names_failing(run(configured))

    def test_console_backend_never_blocks(self, configured):
        configured = Config(
            sources_path=configured.sources_path,
            anthropic_api_key="sk-ant-test",
            mail=MailConfig(backend="console"),
        )
        assert "Versand" not in names_failing(run(configured))

    def test_render_says_it_will_be_skipped(self, configured):
        bare = Config(sources_path=configured.sources_path, anthropic_api_key=None,
                      mail=MailConfig(backend="resend"))
        assert "uebersprungen" in render(run(bare))


class TestScoping:
    def test_only_the_requested_checks_run(self, configured):
        checks = run(configured, ("mail",))
        assert [c.name for c in checks] == ["Versand"]
