from mi.collect.website_diff import diff_lines, normalize_lines


class TestNormalizeLines:
    def test_drops_boilerplate_and_fragments(self):
        lines = normalize_lines(
            "Wir automatisieren die Abrechnung.\n"
            "© 2026 Doctario\n"
            "ok\n"
            "Datenschutz und Cookies verwalten\n"
        )
        assert lines == ["Wir automatisieren die Abrechnung."]

    def test_collapses_whitespace(self):
        assert normalize_lines("  Ein    langer  Satz hier  ") == ["Ein langer Satz hier"]


class TestDiff:
    def test_reports_added_lines(self):
        old = ["Wir automatisieren die Abrechnung."]
        new = ["Wir automatisieren die Abrechnung.", "Neu: Partnerschaft mit XY."]
        added, removed = diff_lines(old, new)
        assert added == ["Neu: Partnerschaft mit XY."] and removed == []

    def test_reports_removed_lines(self):
        added, removed = diff_lines(["Stelle: Backend Engineer"], [])
        assert removed == ["Stelle: Backend Engineer"] and added == []

    def test_identical_pages_produce_nothing(self):
        assert diff_lines(["a b c d e"], ["a b c d e"]) == ([], [])
