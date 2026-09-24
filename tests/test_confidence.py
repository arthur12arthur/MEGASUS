"""Tests du module 1.10 — ConfidenceIndex."""

from __future__ import annotations

from hyperion.confidence import ConfidenceIndex, data_completeness
from hyperion.models import Discipline, Horse, Race, RaceMeta
import datetime as dt


def _make(discipline=Discipline.PLAT, complete=True):
    horses = []
    for i in range(6):
        horses.append(
            Horse(
                horse_id=f"h{i}",
                name=f"Cheval {i}",
                number=i + 1,
                driver=f"DRIVER {i}",
                odds_pdf=3.0 + i,
                gains=1_000_000 * (6 - i),
                recent_places=(1, 2, 3, 4, 0),
                surface={"bon": 0.7},
                distance={"1600": 0.6},
            )
        )
    return Race(
        meta=RaceMeta(
            operator="LONAB",
            country="Burkina Faso",
            date=dt.date(2026, 9, 20),
            discipline=discipline,
        ),
        horses=horses,
        race_id="r",
    )


class TestCompletude:
    def test_complet(self):
        completeness, missing = data_completeness(_make(), Discipline.PLAT)
        assert completeness == 1.0
        assert missing == []

    def test_manques_listes(self):
        race = _make()
        for horse in race.horses[:2]:
            horse.gains = None
            horse.recent_places = ()
        completeness, missing = data_completeness(race, Discipline.PLAT)
        assert completeness < 1.0
        assert any("gains" in item for item in missing)

    def test_ferrure_non_penalisante_hors_trot(self):
        race = _make(discipline=Discipline.PLAT)
        completeness, missing = data_completeness(race, Discipline.PLAT)
        assert completeness == 1.0

    def test_ferrure_exigee_en_trot(self):
        race = _make(discipline=Discipline.TROT_ATTELE)
        completeness, missing = data_completeness(race, Discipline.TROT_ATTELE)
        assert completeness < 1.0
        assert any("shoeing" in item for item in missing)

    def test_course_vide(self):
        race = _make()
        race.horses = []
        assert data_completeness(race) == (0.0, ["aucun partant"])


class TestConfidenceIndex:
    def test_indice_dans_l_intervalle(self):
        race = _make()
        result = ConfidenceIndex().compute(
            race, {"h0": 8.0, "h1": 7.0, "h2": 6.0}, stability=0.8, convergence=0.6
        )
        assert 0.0 <= result.index <= 10.0

    def test_ecart_important_monte_la_confiance(self):
        race = _make()
        tight = ConfidenceIndex().compute(
            race, {"h0": 5.0, "h1": 4.99, "h2": 4.98}, stability=0.8, convergence=0.6
        )
        wide = ConfidenceIndex().compute(
            race, {"h0": 9.0, "h1": 3.0, "h2": 1.0}, stability=0.8, convergence=0.6
        )
        assert wide.index > tight.index

    def test_stabilite_monte_la_confiance(self):
        race = _make()
        unstable = ConfidenceIndex().compute(
            race, {"h0": 8.0, "h1": 7.0}, stability=0.2, convergence=0.6
        )
        stable = ConfidenceIndex().compute(
            race, {"h0": 8.0, "h1": 7.0}, stability=1.0, convergence=0.6
        )
        assert stable.index > unstable.index

    def test_donnees_manquantes_toujours_listees(self):
        race = _make()
        race.horses[0].gains = None
        result = ConfidenceIndex().compute(
            race, {"h0": 8.0, "h1": 7.0}, stability=0.8, convergence=0.6
        )
        assert result.missing_data
        assert any("Cheval 0" in item for item in result.missing_data)

    def test_convergence_absente_valeur_neutre(self):
        race = _make()
        result = ConfidenceIndex().compute(
            race, {"h0": 8.0, "h1": 7.0}, stability=0.8, convergence=None
        )
        assert result.components["convergence_externe"] == 5.0
        assert any("neutre" in text for text in result.justification)

    def test_justification_presente(self):
        race = _make()
        result = ConfidenceIndex().compute(
            race, {"h0": 8.0, "h1": 7.0}, stability=0.8, convergence=0.6
        )
        assert len(result.justification) == 4

    def test_niveaux_qualitatifs(self):
        race = _make()
        high = ConfidenceIndex().compute(
            race, {"h0": 10.0, "h1": 0.0}, stability=1.0, convergence=1.0
        )
        assert high.level in ("élevée", "bonne")

    def test_serialisation(self):
        race = _make()
        result = ConfidenceIndex().compute(
            race, {"h0": 8.0, "h1": 7.0}, stability=0.8, convergence=0.6
        )
        data = result.as_dict()
        assert "index" in data and "missing_data" in data and "justification" in data
