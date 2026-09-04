"""Ein Aufruf, der mitten im Lauf an einem Tippfehler stirbt, kostet einen
ganzen Tag Sammelzeit — deshalb wird der Parser mitgetestet."""

import pytest

from mi.cli import build_parser


class TestParser:
    @pytest.mark.parametrize("command", [
        "verify-sources", "collect", "prefilter", "calibrate", "digest",
        "alerts", "monthly", "competitors", "status",
    ])
    def test_every_command_parses(self, command):
        assert build_parser().parse_args([command]).command == command

    def test_ask_requires_a_question(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["ask"])

    def test_ask_joins_multiple_words(self):
        args = build_parser().parse_args(["ask", "Stand", "GOÄneu"])
        assert args.question == ["Stand", "GOÄneu"]

    def test_collect_defaults_to_daily_and_verified_only(self):
        args = build_parser().parse_args(["collect"])
        assert args.cadence == "daily" and args.allow_unverified is False

    def test_collect_rejects_an_unknown_cadence(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["collect", "--cadence", "stuendlich"])

    def test_digest_dry_run_flag(self):
        assert build_parser().parse_args(["digest", "--dry-run"]).dry_run is True

    def test_unknown_command_exits(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["hellsehen"])
