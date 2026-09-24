"""Tests de l'ingestion (1.1), du labo Ouroboros et de l'orchestrateur."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from hyperion.config import Settings
from hyperion.ingestion import (
    IngestionError,
    JsonFileProvider,
    LonabProvider,
    parse_journal_text,
    verify_identity,
)
from hyperion.lab import Ouroboros, aggregate, walk_forward_folds
from hyperion.models import Discipline, RaceMeta
from hyperion.orchestrator import Orchestrator, SystemReport
from hyperion.synthetic import SyntheticProvider, save_synthetic


JOURNAL = """LONAB — JOURNAL HIPPIQUE OFFICIEL
BURKINA FASO — Réunion R1 - Ouagadougou
20/09/2026 — Départ 15h30 — Trot attelé 2150m — Prix de la République

1  TONNERRE DE MAI  (H/KEITA M.)  1a2a3a  1 250 000 FCFA  3.5
2  ECLAIR DU SAHEL  (H/SAWADOGO A.)  2a4a1a  600 000 FCFA  5.0
3  SAGESSE NOIRE  (H/OUEDRAOGO B.)  6a0a8a  300 000 FCFA  8.0
"""


class TestParsing:
    def test_parse_un_journal(self):
        race, report = parse_journal_text(JOURNAL, race_type="Trot attelé")
        assert len(race.horses) == 3
        assert report.lines_parsed == 3
        assert report.parse_rate == 0.5
        assert race.meta.discipline is Discipline.TROT_ATTELE
        assert race.meta.operator == "LONAB"
        assert race.meta.country == "Burkina Faso"
        assert race.meta.distance_m == 2150

    def test_extraction_des_champs(self):
        race, _ = parse_journal_text(JOURNAL)
        first = race.horses[0]
        assert first.number == 1
        assert first.name == "TONNERRE DE MAI"
        assert first.driver == "KEITA M."
        assert first.odds_pdf == 3.5
        assert first.music == "1a2a3a"
        assert first.gains == 1_250_000

    def test_date_et_heure(self):
        race, _ = parse_journal_text(JOURNAL)
        assert race.meta.date == dt.date(2026, 9, 20)
        assert race.meta.start_time is not None
        assert race.meta.start_time.hour == 15
        assert race.meta.start_time.minute == 30

    def test_race_type_conserve(self):
        race, _ = parse_journal_text(JOURNAL, race_type="Trot attelé")
        assert race.meta.race_type == "Trot attelé"

    def test_journal_vide(self):
        with pytest.raises(IngestionError):
            parse_journal_text("   ")

    def test_aucun_partant(self):
        with pytest.raises(IngestionError):
            parse_journal_text("LONAB — rien à voir ici du tout")

    def test_lignes_non_reconnues_rapportees(self):
        text = JOURNAL + "\nCette ligne ne ressemble à aucun partant.\n"
        race, report = parse_journal_text(text)
        assert report.unparsed
        assert len(race.horses) == 3

    def test_motif_personnalise(self):
        text = "1|B|C\n2|E|F\n"
        race, report = parse_journal_text(
            text, partant_re=__import__("re").compile(r"^(?P<num>\d)\|(?P<name>[A-Z])\|")
        )
        assert len(race.horses) == 2
        assert race.horses[0].name == "B"


class TestVerificationIdentite:
    def test_identite_conforme(self):
        meta = RaceMeta(operator="LONAB", country="Burkina Faso", date=dt.date(2026, 9, 20))
        check = verify_identity(meta, dt.date(2026, 9, 20))
        assert check.ok

    def test_autre_pays_refuse(self):
        meta = RaceMeta(operator="LONAB", country="Côte d'Ivoire")
        check = verify_identity(meta)
        assert not check.ok
        assert any("Côte d'Ivoire" in r for r in check.reasons)

    def test_autre_operateur_refuse(self):
        meta = RaceMeta(operator="PMU France", country="Burkina Faso")
        check = verify_identity(meta)
        assert not check.ok

    def test_mauvaise_date_refusee(self):
        meta = RaceMeta(operator="LONAB", country="Burkina Faso", date=dt.date(2026, 9, 19))
        check = verify_identity(meta, dt.date(2026, 9, 20))
        assert not check.ok
        assert any("date" in r for r in check.reasons)

    def test_operateur_absent_signale(self):
        meta = RaceMeta(country="Burkina Faso")
        check = verify_identity(meta)
        assert not check.ok
        assert any("opérateur" in r for r in check.reasons)


class TestProviders:
    def test_json_provider(self, simple_race, tmp_path):
        path = tmp_path / "journal.json"
        path.write_text(json.dumps(simple_race.as_dict()), encoding="utf-8")
        race = JsonFileProvider(path).fetch()
        assert race.race_id == simple_race.race_id
        assert len(race.horses) == len(simple_race.horses)

    def test_json_provider_absent(self, tmp_path):
        with pytest.raises(IngestionError):
            JsonFileProvider(tmp_path / "absent.json").fetch()

    def test_lonab_sans_url_bascule_sur_le_secours(self, simple_race, tmp_path):
        path = tmp_path / "journal.json"
        path.write_text(json.dumps(simple_race.as_dict()), encoding="utf-8")
        provider = LonabProvider(settings=Settings(), fallback=JsonFileProvider(path))
        race = provider.fetch()
        assert race.race_id == simple_race.race_id

    def test_lonab_sans_url_sans_secours(self):
        with pytest.raises(IngestionError):
            LonabProvider(settings=Settings()).fetch()


class TestOuroboros:
    def test_evaluation_shadow(self, synthetic):
        run = Ouroboros().evaluate_shadow(
            synthetic.race, synthetic.simulate_outcome(seed=1)
        )
        assert run.race_id == synthetic.race.race_id
        assert len(run.predicted_top) == 5
        assert isinstance(run.winner_in_top5, bool)

    def test_aggregation(self, synthetic):
        runs = [
            Ouroboros().evaluate_shadow(s.race, s.simulate_outcome(seed=i))
            for i, s in enumerate(
                SyntheticProvider(seed=1).series(dt.date(2026, 1, 1), 5)
            )
        ]
        summary = aggregate(runs)
        assert summary["n"] == 5
        assert 0.0 <= summary["winner_in_top5"] <= 1.0

    def test_aggregation_vide(self):
        assert aggregate([]) == {"n": 0}

    def test_walk_forward(self):
        dates = [dt.date(2026, 1, i) for i in range(1, 6)]
        folds = walk_forward_folds(dates, min_train=2)
        assert len(folds) == 3
        for train, test in folds:
            assert train and test
            assert max(train) < min(test)

    def test_comparaison_sans_regression(self, synthetic):
        runs = [Ouroboros().evaluate_shadow(s.race, s.simulate_outcome(seed=i))
                for i, s in enumerate(SyntheticProvider(seed=2).series(dt.date(2026, 1, 1), 5))]
        report = Ouroboros().compare(runs, runs)
        assert not report.regression
        assert "déployée" in report.recommendation

    def test_comparaison_detecte_une_regression(self, synthetic):
        good = [Ouroboros().evaluate_shadow(s.race, s.simulate_outcome(seed=i))
                for i, s in enumerate(SyntheticProvider(seed=3).series(dt.date(2026, 1, 1), 5))]
        from hyperion.lab import ShadowRun

        bad = [ShadowRun(race_id=r.race_id, date=r.date, winner_in_top5=False) for r in good]
        report = Ouroboros().compare(good, bad)
        assert report.regression
        assert "RÉGRESSION" in report.recommendation

    def test_calibration_de_la_concentration(self):
        provider = SyntheticProvider(seed=5)
        races = provider.series(dt.date(2026, 1, 1), 6)
        results = {s.race.race_id: s.simulate_outcome(seed=100 + i)
                   for i, s in enumerate(races)}
        best, scores = Ouroboros().calibrate_concentration(
            [s.race for s in races], results, grid=[0.4, 0.8, 1.2]
        )
        assert best in (0.4, 0.8, 1.2)
        assert set(scores) == {0.4, 0.8, 1.2}


class TestOrchestrateur:
    def test_synthese_ponderee(self):
        reports = [
            SystemReport(system="V6", ranking=["a", "b", "c"], reliability=0.8),
            SystemReport(system="V7", ranking=["a", "c", "b"], reliability=0.4),
            SystemReport(system="Manus", ranking=["b", "a", "c"], reliability=0.2),
        ]
        synthesis = Orchestrator().synthesise(reports)
        assert synthesis.ranking[0] == "a"
        assert synthesis.weights

    def test_confederation_renforce_le_leader(self):
        reports = [
            SystemReport(system="Fiable", ranking=["a", "b"], reliability=0.9),
            SystemReport(system="Faible", ranking=["b", "a"], reliability=0.1),
        ]
        weights = Orchestrator().weights(reports)
        assert weights["Fiable"] > weights["Faible"]

    def test_poids_sommment_a_un(self):
        reports = [
            SystemReport(system="A", ranking=["x"], reliability=0.5),
            SystemReport(system="B", ranking=["y"], reliability=0.3),
        ]
        weights = Orchestrator().weights(reports)
        assert abs(sum(weights.values()) - 1.0) < 1e-9

    def test_accord_parfait(self):
        reports = [
            SystemReport(system="A", ranking=["a", "b", "c"], reliability=0.5),
            SystemReport(system="B", ranking=["a", "b", "c"], reliability=0.5),
        ]
        synthesis = Orchestrator().synthesise(reports)
        assert synthesis.agreement == 1.0

    def test_desaccord_signale(self):
        reports = [
            SystemReport(system="A", ranking=["a", "b", "c"], reliability=0.5),
            SystemReport(system="B", ranking=["d", "e", "f"], reliability=0.5),
        ]
        synthesis = Orchestrator().synthesise(reports)
        assert synthesis.agreement == 0.0
        assert synthesis.notes

    def test_sans_rapport(self):
        synthesis = Orchestrator().synthesise([])
        assert synthesis.ranking == []
        assert synthesis.notes

    def test_fiabilite_nulle_repartit_equitablement(self):
        reports = [
            SystemReport(system="A", ranking=["a"], reliability=0.0),
            SystemReport(system="B", ranking=["b"], reliability=0.0),
        ]
        weights = Orchestrator().weights(reports)
        assert abs(weights["A"] - weights["B"]) < 1e-9

    def test_depuis_json(self):
        payload = json.dumps(
            {"reports": [{"system": "A", "ranking": ["a", "b"], "reliability": 0.7}]}
        )
        reports = Orchestrator.from_json(payload)
        assert len(reports) == 1
        assert reports[0].system == "A"

    def test_serialisation(self):
        synthesis = Orchestrator().synthesise(
            [SystemReport(system="A", ranking=["a", "b"], reliability=0.5)]
        )
        data = synthesis.as_dict()
        assert "ranking" in data and "weights" in data and "agreement" in data


class TestSynthetique:
    def test_reproductibilite(self):
        first = SyntheticProvider(seed=9).race(dt.date(2026, 1, 1), 1)
        second = SyntheticProvider(seed=9).race(dt.date(2026, 1, 1), 1)
        assert [h.name for h in first.race.horses] == [h.name for h in second.race.horses]
        assert first.latent == second.latent

    def test_tous_les_partants_presents(self, synthetic):
        assert len(synthetic.race.runners) >= 8

    def test_verite_latente_ordonnee(self, synthetic):
        ranking = synthetic.true_ranking()
        values = [synthetic.latent[h] for h in ranking]
        assert values == sorted(values, reverse=True)

    def test_arrivee_simulee_complete(self, synthetic):
        order = synthetic.simulate_outcome(seed=3)
        assert sorted(order) == sorted(synthetic.latent)
        assert len(order) == len(synthetic.race.runners)

    def test_arrivee_deterministe(self, synthetic):
        assert synthetic.simulate_outcome(seed=3) == synthetic.simulate_outcome(seed=3)

    def test_le_fort_gagne_plus_souvent(self):
        provider = SyntheticProvider(seed=11)
        synthetic = provider.race(dt.date(2026, 1, 1), 1)
        best = synthetic.true_ranking()[0]
        wins = sum(
            1
            for seed in range(200)
            if synthetic.simulate_outcome(seed=seed)[0] == best
        )
        assert wins > 20  # nettement plus que 1/n

    def test_sauvegarde_json(self, synthetic, tmp_path):
        path = save_synthetic(synthetic.race, tmp_path / "s.json", synthetic.latent)
        assert path.exists()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["_synthetic_latent"]

    def test_serie_de_courses(self):
        provider = SyntheticProvider(seed=13)
        series = provider.series(dt.date(2026, 1, 1), 4)
        assert len(series) == 4
        assert series[0].race.meta.date < series[3].race.meta.date
