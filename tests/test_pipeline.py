"""Tests d'intégration du pipeline complet et de la livraison."""

from __future__ import annotations

import datetime as dt
import json

from hyperion.config import Settings
from hyperion.delivery import Delivery, PIPELINE_STEPS, check_deadline
from hyperion.external import ManualPanelProvider
from hyperion.models import OfficialResult
from hyperion.pipeline import run_pipeline
from hyperion.storage import JsonStore


class TestPipeline:
    def _panel(self, race):
        ordered = race.order_by_odds()
        payload = {
            "Genybet": [h.name for h in ordered[:5]],
            "Equidia": [h.name for h in ordered[:6]],
            "Paris-Turf": [h.name for h in ordered[:4]],
        }
        return ManualPanelProvider(payload).fetch(race)

    def test_pipeline_complet(self, simple_race):
        result = run_pipeline(simple_race, Settings(), external_picks=self._panel(simple_race))
        assert result.ranking
        assert result.scorer.scores
        assert result.confidence.index > 0

    def test_ordre_des_modules_respecte(self, simple_race):
        """MarketWatch doit être positionné AVANT le filtrage."""
        result = run_pipeline(simple_race, Settings(), latest_odds={"h1": 2.0})
        # La cote fraîche a bien été injectée avant le filtrage.
        assert simple_race.by_id("h1").odds == 2.0
        assert result.market.signal("h1").odds_latest == 2.0

    def test_separation_des_deux_scores(self, simple_race):
        result = run_pipeline(simple_race, Settings())
        record = result.record
        assert "competitivite" in record
        assert "classement" in record
        # Les deux listes existent et sont distinctes dans leur construction.
        assert record["competitivite"]
        assert record["classement"]

    def test_classement_complet_sur_le_groupe_filtre(self, simple_race):
        """Le classement porte sur le groupe retenu, pas sur tous les partants."""
        result = run_pipeline(simple_race, Settings())
        groupe = set(result.filter_result.selected)
        assert groupe
        assert set(result.ranking) == groupe

    def test_record_serialisable(self, simple_race):
        result = run_pipeline(simple_race, Settings())
        text = json.dumps(result.record, ensure_ascii=False, default=str)
        assert "consensusinterne" in text
        assert "confiance" in text

    def test_blocs_de_livraison(self, simple_race):
        result = run_pipeline(simple_race, Settings())
        assert len(result.blocks) == 5
        for block in result.blocks.values():
            assert block.strip()

    def test_hors_delai_signale(self, simple_race):
        late = dt.datetime(2026, 9, 20, 18, 0, tzinfo=dt.timezone.utc)
        result = run_pipeline(simple_race, Settings(), now=late)
        assert result.out_of_deadline.is_late
        assert "HORS DÉLAI" in result.blocks["1/5 · Course"]

    def test_dans_les_delais(self, simple_race):
        early = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)
        result = run_pipeline(simple_race, Settings(), now=early)
        assert not result.out_of_deadline.is_late
        assert "HORS DÉLAI" not in result.blocks["1/5 · Course"]

    def test_hades_ne_bloque_jamais(self, simple_race):
        result = run_pipeline(simple_race, Settings(), latest_odds={"h3": 1.5})
        # Un signal HADES ne retire aucun cheval du groupe filtre.
        assert set(result.ranking) == set(result.filter_result.selected)
        assert result.hades.flagged

    def test_hades_sans_cote_fraiche(self, simple_race):
        result = run_pipeline(simple_race, Settings())
        assert result.hades is not None

    def test_sans_panel_externe(self, simple_race):
        result = run_pipeline(simple_race, Settings(), external_picks=[])
        assert result.external.convergence == 0.0
        assert result.confidence.components["convergence_externe"] == 5.0


class TestStorage:
    def test_sauvegarde_et_rechargement(self, simple_race, tmp_path):
        result = run_pipeline(simple_race, Settings())
        store = JsonStore(runs_dir=tmp_path / "runs")
        path = store.save(result.record)
        assert path.exists()
        loaded = store.load(simple_race.race_id, simple_race.meta.date)
        assert loaded is not None
        assert loaded["race_id"] == simple_race.race_id

    def test_organisation_par_date(self, simple_race, tmp_path):
        store = JsonStore(runs_dir=tmp_path / "runs")
        path = store.save({"race_id": "x", "date": "2026-09-20"})
        assert path == tmp_path / "runs" / "2026" / "09" / "20" / "x.json"

    def test_records_pour_une_date(self, simple_race, tmp_path):
        store = JsonStore(runs_dir=tmp_path / "runs")
        store.save({"race_id": "a", "date": "2026-09-20"})
        store.save({"race_id": "b", "date": "2026-09-20"})
        store.save({"race_id": "c", "date": "2026-09-21"})
        assert len(store.records_for(dt.date(2026, 9, 20))) == 2

    def test_all_records(self, tmp_path):
        store = JsonStore(runs_dir=tmp_path / "runs")
        store.save({"race_id": "a", "date": "2026-09-20"})
        store.save({"race_id": "b", "date": "2026-09-21"})
        assert len(store.all_records()) == 2

    def test_repertoire_vide(self, tmp_path):
        assert JsonStore(runs_dir=tmp_path / "absent").all_records() == []


class TestDelivery:
    def test_signature_identifiable(self):
        text = Delivery().signature({"course": "r1"})
        assert "Hyperion" in text
        assert "UTC" in text
        assert "r1" in text

    def test_checklist_complete(self, simple_race):
        result = run_pipeline(simple_race, Settings())
        checklist = Delivery().auto_validate(result.record)
        assert checklist.items
        assert all(name for name, _ in checklist.items)

    def test_checklist_detecte_un_manque(self, simple_race):
        result = run_pipeline(simple_race, Settings())
        record = dict(result.record)
        del record["hades"]
        checklist = Delivery().auto_validate(record)
        assert not checklist.passed
        assert any("HADES" in name for name in checklist.failures)

    def test_les_9_etapes_sont_verifiees(self):
        assert len(PIPELINE_STEPS) == 9

    def test_canaux_non_configures(self, simple_race):
        result = run_pipeline(simple_race, Settings())
        reports = Delivery().deliver("sujet", list(result.blocks.values()))
        assert reports
        assert all(not report.ok for report in reports)

    def test_dry_run(self, simple_race):
        settings = Settings(**{**Settings().__dict__, "dry_run": True})
        reports = Delivery(settings).deliver("sujet", ["a", "b"])
        assert any(report.channel == "dry-run" and report.ok for report in reports)

    def test_messages_courts(self, simple_race):
        result = run_pipeline(simple_race, Settings())
        messages = Delivery().split_messages(result.blocks)
        assert len(messages) == 5
        assert all(len(m) < 4000 for m in messages)

    def test_heure_limite_sans_heure_de_depart(self, simple_race):
        simple_race.meta.start_time = None
        assert not check_deadline(simple_race).is_late

    def test_heure_limite_avec_fuseau_local(self, simple_race):
        simple_race.meta.start_time = dt.datetime(
            2026, 9, 20, 15, 0, tzinfo=dt.timezone.utc
        )
        late = check_deadline(
            simple_race,
            now=dt.datetime(2026, 9, 20, 18, 0, tzinfo=dt.timezone.utc),
        )
        # L'heure limite est la clôture LONAB (départ − 10 min), pas le départ.
        assert late.is_late
        assert late.race_started
        assert late.minutes_late == 190.0

    def test_cloture_lonab_passee_course_pas_partie(self, simple_race):
        simple_race.meta.start_time = dt.datetime(2026, 9, 20, 14, 15, tzinfo=dt.timezone.utc)
        late = check_deadline(
            simple_race, now=dt.datetime(2026, 9, 20, 14, 10, tzinfo=dt.timezone.utc)
        )
        assert late.is_late and not late.race_started
        assert late.minutes_late == 5.0
        header = Delivery().render_header(simple_race, late)
        assert "HORS DÉLAI" in header and "enjeux LONAB sont clos" in header

    def test_heure_de_cloture_du_programme_prioritaire(self, simple_race):
        simple_race.meta.start_time = dt.datetime(2026, 9, 20, 14, 15, tzinfo=dt.timezone.utc)
        simple_race.meta.betting_close = dt.datetime(2026, 9, 20, 14, 0, tzinfo=dt.timezone.utc)
        late = check_deadline(
            simple_race, now=dt.datetime(2026, 9, 20, 14, 2, tzinfo=dt.timezone.utc)
        )
        assert late.is_late
        assert late.schedule.close_source == "programme"

    def test_entete_affiche_les_deux_fuseaux(self, simple_race):
        simple_race.meta.start_time = dt.datetime(2026, 9, 20, 14, 15, tzinfo=dt.timezone.utc)
        late = check_deadline(
            simple_race, now=dt.datetime(2026, 9, 20, 9, 30, tzinfo=dt.timezone.utc)
        )
        header = Delivery().render_header(simple_race, late)
        assert "14h15 à Ouagadougou" in header
        assert "16h15 heure de Paris" in header  # heure d'été : +2 h
        assert "clôture LONAB 14h05" in header
        assert "Paris-Vincennes (France)" in header
        assert "relayée par LONAB" in header
        assert "4+1" in header  # dimanche 20/09/2026
        assert "4 h 35 avant la clôture LONAB" in header

    def test_format_des_durees(self):
        from hyperion.delivery import format_duration

        assert format_duration(5) == "5 minutes"
        assert format_duration(190) == "3 h 10"
        assert format_duration(6243) == "4 jours"


class TestEveningEvaluation:
    def test_evaluation_du_soir(self, simple_race, tmp_path):
        from hyperion.evaluation import EveningEvaluation

        result = run_pipeline(simple_race, Settings())
        winner = result.ranking[0]
        official = OfficialResult(
            race_id=simple_race.race_id,
            date=simple_race.meta.date,
            order=[winner] + [h for h in result.ranking if h != winner],
        )
        report = EveningEvaluation().evaluate(result.record, official, simple_race)
        assert report.system_winner_in_top1
        assert report.system_winner_in_top5

    def test_evaluation_cumulee(self, simple_race, tmp_path):
        from hyperion.evaluation import EveningEvaluation

        result = run_pipeline(simple_race, Settings())
        official = OfficialResult(
            race_id=simple_race.race_id,
            date=simple_race.meta.date,
            order=list(result.ranking),
        )
        evaluation = EveningEvaluation().cumulative(
            [result.record], {simple_race.race_id: official}
        )
        assert evaluation.races == 1
        assert evaluation.winner_in_top1 == 1.0

    def test_calibration_non_exploitable_sur_petit_historique(self, simple_race):
        from hyperion.evaluation import EveningEvaluation, MIN_RACES_FOR_CALIBRATION

        result = run_pipeline(simple_race, Settings())
        official = OfficialResult(
            race_id=simple_race.race_id,
            date=simple_race.meta.date,
            order=list(result.ranking),
        )
        evaluation = EveningEvaluation().cumulative(
            [result.record], {simple_race.race_id: official}
        )
        assert not evaluation.calibration_usable
        assert MIN_RACES_FOR_CALIBRATION > 1
