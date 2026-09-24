"""Tests de l'interface en ligne de commande."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from hyperion.cli import main
from hyperion.synthetic import SyntheticProvider


class TestDemo:
    def test_demo_texte(self, capsys):
        assert main(["demo", "--seed", "3", "--index", "2"]) == 0
        out = capsys.readouterr().out
        assert "Hyperion" in out or "HYPERION" in out
        assert "Classement" in out

    def test_demo_json(self, capsys):
        assert main(["demo", "--seed", "3", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["classement"]
        assert "_verite_latente" in payload


class TestRun:
    def test_run_depuis_json(self, tmp_path, capsys):
        provider = SyntheticProvider(seed=5)
        synthetic = provider.race(dt.date.today(), 1)
        journal = tmp_path / "journal.json"
        journal.write_text(json.dumps(synthetic.race.as_dict()), encoding="utf-8")
        assert main(["run", "--input", str(journal)]) == 0
        assert "Classement" in capsys.readouterr().out

    def test_run_json(self, tmp_path, capsys):
        provider = SyntheticProvider(seed=5)
        synthetic = provider.race(dt.date.today(), 1)
        journal = tmp_path / "journal.json"
        journal.write_text(json.dumps(synthetic.race.as_dict()), encoding="utf-8")
        assert main(["run", "--input", str(journal), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["consensusinterne"]["ranking"]

    def test_run_avec_panel_et_cotes(self, tmp_path, capsys):
        provider = SyntheticProvider(seed=6)
        synthetic = provider.race(dt.date.today(), 1)
        journal = tmp_path / "journal.json"
        journal.write_text(json.dumps(synthetic.race.as_dict()), encoding="utf-8")
        panel = tmp_path / "panel.json"
        ordered = synthetic.race.order_by_odds()
        panel.write_text(
            json.dumps({"Genybet": [h.name for h in ordered[:5]]}), encoding="utf-8"
        )
        odds = tmp_path / "odds.json"
        odds.write_text(json.dumps(synthetic.market_odds), encoding="utf-8")
        assert main(["run", "--input", str(journal), "--panel", str(panel), "--odds", str(odds)]) == 0
        assert "panel" in capsys.readouterr().out

    def test_run_stocke_le_rapport(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("HYPERION_RUNS_DIR", str(tmp_path / "runs"))
        provider = SyntheticProvider(seed=7)
        synthetic = provider.race(dt.date.today(), 1)
        journal = tmp_path / "journal.json"
        journal.write_text(json.dumps(synthetic.race.as_dict()), encoding="utf-8")
        assert main(["run", "--input", str(journal), "--store"]) == 0
        assert "stocké" in capsys.readouterr().err
        assert list((tmp_path / "runs").rglob("*.json")), "le rapport doit aller dans HYPERION_RUNS_DIR"

    def test_date_invalide(self):
        from hyperion.cli import _parse_date

        with pytest.raises(SystemExit) as exc:
            _parse_date("pas-une-date")
        assert "invalide" in str(exc.value)

    def test_date_valide(self):
        from hyperion.cli import _parse_date

        assert _parse_date("2026-09-20") == dt.date(2026, 9, 20)
        assert _parse_date(None) is None


class TestBacktestCli:
    def test_backtest_court(self, capsys):
        assert main(["backtest", "--n", "4", "--seed", "1"]) == 0
        out = capsys.readouterr().out
        assert "Top 5" in out

    def test_backtest_json(self, capsys):
        assert main(["backtest", "--n", "3", "--seed", "1", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["n"] == 3
        assert len(payload["runs"]) == 3


class TestCalibrateCli:
    def test_calibrate_court(self, capsys):
        assert main(["calibrate", "--n", "3", "--seed", "1"]) == 0
        out = capsys.readouterr().out
        assert "concentration" in out
        assert "retenue" in out

    def test_calibrate_json(self, capsys):
        assert main(["calibrate", "--n", "3", "--seed", "1", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert "best" in payload
        assert "scores" in payload


class TestEvaluateCli:
    def test_evaluate_sans_historique(self, capsys):
        assert main(["evaluate"]) == 0
        out = capsys.readouterr().out
        assert "Courses évaluées" in out

    def test_evaluate_json(self, capsys):
        assert main(["evaluate", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert "races" in payload
