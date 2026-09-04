"""Die Alert-Logik ist die Stelle, an der Fehlalarme am meisten kosten:
ein Kanal, der zu oft piept, wird nach zwei Wochen ignoriert."""

import pytest

from mi.alerts import detect_triggers, watchlist_hits


def triggers_for(title, summary="", raw_text=""):
    return [
        t.name
        for t in detect_triggers(
            {"title": title, "summary": summary, "raw_text": raw_text}
        )
    ]


class TestWatchlist:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Die ADK GmbH uebernimmt", ["ADK GmbH"]),
            ("Kreiskrankenhaus Ehingen erweitert", ["Kreiskrankenhaus Ehingen"]),
            ("Dedalus stellt ORBIS-Roadmap vor", ["Dedalus", "ORBIS"]),
            ("Schön Klinik baut aus", ["Schön Klinik"]),
            ("Ein ganz anderes Thema", []),
        ],
    )
    def test_hits(self, text, expected):
        assert watchlist_hits(text) == expected

    def test_watchlist_hit_alone_triggers(self):
        assert "Watchlist" in triggers_for("Avelios eroeffnet ein Buero")


class TestEreignisklassen:
    def test_1_goaeneu_statement_needs_actor_and_topic(self):
        assert "GOÄneu-Äußerung" in triggers_for(
            "Bundesärztekammer aeussert sich zur GOÄneu"
        )
        # Thema ohne Akteur reicht nicht.
        assert "GOÄneu-Äußerung" not in triggers_for(
            "Ein Kommentar zur GOÄneu aus der Praxis"
        )

    def test_2_verfahrensstufe(self):
        assert "Verfahrensstufe" in triggers_for(
            "Kabinett beschliesst Referentenentwurf zur Privatliquidation"
        )
        assert "Verfahrensstufe" not in triggers_for(
            "Debatte ueber Privatliquidation in der Fachpresse"
        )

    def test_3_wettbewerber_event(self):
        assert "Wettbewerber" in triggers_for(
            "Qodia meldet Finanzierungsrunde ueber 10 Millionen"
        )

    def test_3_mere_mention_is_not_an_event(self):
        assert "Wettbewerber" not in triggers_for(
            "Ein Marktueberblick nennt auch Qodia als Anbieter"
        )

    def test_4_klinikgruppe_over_five_sites(self):
        assert "Klinikgruppe" in triggers_for(
            "Klinikgruppe meldet Insolvenz", "Der Verbund betreibt 12 Standorte."
        )

    def test_4_small_group_is_filtered_out(self):
        assert "Klinikgruppe" not in triggers_for(
            "Klinikgruppe meldet Insolvenz", "Der Verbund betreibt 3 Standorte."
        )

    def test_4_unknown_size_still_triggers(self):
        # Groesse unklar: lieber melden als verpassen — der Score-Filter
        # davor hat die Meldung ohnehin schon als hochrelevant eingestuft.
        assert "Klinikgruppe" in triggers_for("Klinikkette wird uebernommen")

    def test_5_bw_hospital(self):
        assert "Baden-Württemberg" in triggers_for(
            "Klinikum Tuebingen: Traegerwechsel beschlossen"
        )

    def test_5_bw_without_event_does_not_trigger(self):
        assert "Baden-Württemberg" not in triggers_for(
            "Klinikum Tuebingen eroeffnet neue Station"
        )


class TestNegatives:
    @pytest.mark.parametrize(
        "title",
        [
            "Neue Pflegekammer nimmt Arbeit auf",
            "DRG-Kodierung: neue Regeln fuer die GKV",
            "Studie zu Blutdrucksenkern veroeffentlicht",
            "Oberarzt wechselt die Abteilung",
        ],
    )
    def test_irrelevant_items_stay_quiet(self, title):
        assert triggers_for(title) == []
