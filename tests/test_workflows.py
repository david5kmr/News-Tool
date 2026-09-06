"""Die Workflow-Dateien werden von niemandem geprueft, bevor GitHub sie ablehnt —
und eine abgelehnte Datei scheitert sofort, mit dem Dateipfad als Namen.

yaml.safe_load faengt das nicht: PyYAML ueberschreibt doppelte Schluessel still,
GitHub weist sie zurueck. Deshalb hier ein Loader, der auf Duplikate besteht.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))
SCHEDULED = {"digest.yml", "alerts.yml", "monthly.yml", "competitors.yml"}


class StrictLoader(yaml.SafeLoader):
    """Wie SafeLoader, lehnt aber doppelte Schluessel ab — so wie GitHub."""


def _no_duplicates(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, f"doppelter Schluessel {key!r}", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


def load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
class TestWorkflowFile:
    def test_parses_without_duplicate_keys(self, path):
        assert load(path)

    def test_every_step_has_at_most_one_condition(self, path):
        """Der Fehler, der digest.yml einmal unbrauchbar gemacht hat."""
        for job in load(path).get("jobs", {}).values():
            for step in job.get("steps", []):
                assert isinstance(step, dict)

    def test_steps_reference_only_defined_step_ids(self, path):
        data = load(path)
        for job in data.get("jobs", {}).values():
            known = {s["id"] for s in job.get("steps", []) if "id" in s}
            for step in job.get("steps", []):
                condition = str(step.get("if", ""))
                if "steps." in condition:
                    referenced = condition.split("steps.")[1].split(".")[0]
                    assert referenced in known, (
                        f"{path.name}: Schritt verweist auf unbekannte id "
                        f"{referenced!r}"
                    )


class TestScheduledWorkflows:
    @pytest.mark.parametrize("name", sorted(SCHEDULED))
    def test_starts_with_a_preflight_gate(self, name):
        """Ohne die Kontrolle scheitert jeder Cron-Lauf, solange Bauschritt 1
        offen ist oder Secrets fehlen."""
        data = load(Path(__file__).resolve().parent.parent / ".github" / "workflows" / name)
        steps = next(iter(data["jobs"].values()))["steps"]
        preflight = [s for s in steps if s.get("id") == "pre"]
        assert preflight, f"{name} hat keine Vorflugkontrolle"
        assert "mi preflight" in preflight[0]["run"]

    @pytest.mark.parametrize("name", sorted(SCHEDULED))
    def test_preflight_uses_pipefail(self, name):
        """Ohne pipefail wertet `if cmd | tee` den Exit-Code von tee aus —
        die Kontrolle waere immer bestanden."""
        data = load(Path(__file__).resolve().parent.parent / ".github" / "workflows" / name)
        steps = next(iter(data["jobs"].values()))["steps"]
        run = next(s["run"] for s in steps if s.get("id") == "pre")
        assert "set -o pipefail" in run

    @pytest.mark.parametrize("name", sorted(SCHEDULED))
    def test_work_steps_are_gated(self, name):
        data = load(Path(__file__).resolve().parent.parent / ".github" / "workflows" / name)
        steps = next(iter(data["jobs"].values()))["steps"]
        after_preflight = steps[next(
            i for i, s in enumerate(steps) if s.get("id") == "pre"
        ) + 1:]
        ungated = [s.get("name", s.get("run", "?")) for s in after_preflight
                   if "steps.pre.outputs.ready" not in str(s.get("if", ""))]
        assert ungated == [], f"{name}: ungeschuetzte Schritte {ungated}"
