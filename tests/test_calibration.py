"""Tests des métriques de calibration (module 1.13)."""

from __future__ import annotations

from hyperion.evaluation.calibration import (
    ReliabilityLedger,
    SegmentKey,
    brier_score,
    expected_calibration_error,
    log_loss,
    odds_band,
)


class TestLogLoss:
    def test_prediction_parfaite(self):
        assert log_loss("a", {"a": 1.0}) < 1e-6

    def test_prediction_catastrophique(self):
        assert log_loss("a", {"a": 1e-6}) > 13.0

    def test_cheval_absent(self):
        assert log_loss("z", {"a": 0.5}) > 30.0

    def test_monotone(self):
        # Plus la probabilité annoncée est faible, plus la perte est forte.
        losses = [log_loss("a", {"a": p}) for p in (0.99, 0.9, 0.5, 0.1, 0.01)]
        assert losses == sorted(losses)


class TestBrier:
    def test_parfait(self):
        assert brier_score("a", {"a": 1.0, "b": 0.0}) < 1e-9

    def test_pire(self):
        # Le gagnant annoncé à 0 et le perdant à 1 : écart maximal sur chacun.
        assert abs(brier_score("a", {"a": 0.0, "b": 1.0}) - 1.0) < 1e-9

    def test_vide(self):
        assert brier_score("a", {}) == 0.0

    def test_bornes(self):
        value = brier_score("a", {"a": 0.4, "b": 0.3, "c": 0.3})
        assert 0.0 <= value <= 2.0


class TestECE:
    def test_parfaitement_calibre(self):
        predictions = {"a": 0.5, "b": 0.5, "c": 0.5, "d": 0.5}
        outcomes = {"a": True, "b": False, "c": True, "d": False}
        assert expected_calibration_error(predictions, outcomes) < 1e-9

    def test_surconfiance_detectee(self):
        predictions = {f"h{i}": 0.95 for i in range(10)}
        outcomes = {f"h{i}": (i == 0) for i in range(10)}
        assert expected_calibration_error(predictions, outcomes) > 0.5

    def test_vide(self):
        assert expected_calibration_error({}, {}) == 0.0


class TestOddsBand:
    def test_intervalles(self):
        assert odds_band(2.0) == "<3/1"
        assert odds_band(4.0) == "3-6/1"
        assert odds_band(8.0) == "6-11/1"
        assert odds_band(15.0) == "11-21/1"
        assert odds_band(40.0) == ">21/1"

    def test_inconnu(self):
        assert odds_band(None) == "inconnu"


class TestSegmentKey:
    def test_creation(self):
        key = SegmentKey.of("trot_attele", 4.0)
        assert key.discipline == "trot_attele"
        assert key.odds_band == "3-6/1"


class TestReliabilityLedger:
    def test_taux_de_reussite(self):
        ledger = ReliabilityLedger()
        segment = SegmentKey("trot_attele", "<3/1")
        ledger.record("Genybet", segment, True)
        ledger.record("Genybet", segment, True)
        ledger.record("Genybet", segment, False)
        assert abs(ledger.get("Genybet", segment).hit_rate - 2 / 3) < 1e-9

    def test_segmentation(self):
        ledger = ReliabilityLedger()
        ledger.record("Genybet", SegmentKey("trot_attele", "<3/1"), True)
        ledger.record("Genybet", SegmentKey("plat", ">21/1"), False)
        assert ledger.global_reliability("Genybet") == 0.5

    def test_repli_global_si_petit_echantillon(self):
        ledger = ReliabilityLedger()
        ledger.record("Genybet", SegmentKey("plat", "<3/1"), True)
        ledger.record("Genybet", SegmentKey("plat", "<3/1"), False)
        ledger.record("Genybet", SegmentKey("trot_attele", "<3/1"), True)
        ledger.record("Genybet", SegmentKey("trot_attele", "<3/1"), True)
        ledger.record("Genybet", SegmentKey("trot_attele", "<3/1"), True)
        # Le segment « plat » n'a que 2 courses : repli sur le taux global.
        assert ledger.reliability("Genybet", SegmentKey("plat", "<3/1")) == 0.8

    def test_source_inconnue(self):
        assert ReliabilityLedger().global_reliability("Inconnue") == 0.0

    def test_serialisation_roundtrip(self):
        ledger = ReliabilityLedger()
        ledger.record("Genybet", SegmentKey("plat", "3-6/1"), True, 0.5, 0.2)
        restored = ReliabilityLedger.from_dict(ledger.as_dict())
        segment = SegmentKey("plat", "3-6/1")
        assert restored.get("Genybet", segment).total == 1
        assert restored.get("Genybet", segment).hits == 1
