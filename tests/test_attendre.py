"""Attente avant le départ du Quinté : calcul pur, jamais bloquant en cas de doute."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("attendre", ROOT / "scripts" / "attendre_cloture.py")
attendre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attendre)

PROGRAMME = json.loads((ROOT / "tests" / "fixtures" / "pmu_programme_20261017_trim.json").read_text(encoding="utf-8"))
UTC = dt.timezone.utc


def test_depart_du_quinte_lu_dans_le_programme():
    start = attendre.quinte_start(PROGRAMME)
    assert start is not None and start.tzinfo is not None and start.date() == dt.date(2026, 10, 17)


def test_attente_jusqu_a_depart_moins_90_minutes():
    start = dt.datetime(2026, 10, 17, 13, 0, tzinfo=UTC)
    now = dt.datetime(2026, 10, 17, 8, 30, tzinfo=UTC)
    assert attendre.wait_seconds(now, start, 90) == 3 * 3600  # cible 11h30 -> 3 h


def test_pas_d_attente_si_l_heure_est_passee_ou_inconnue():
    start = dt.datetime(2026, 10, 17, 13, 0, tzinfo=UTC)
    assert attendre.wait_seconds(dt.datetime(2026, 10, 17, 12, 0, tzinfo=UTC), start, 90) == 0
    assert attendre.wait_seconds(dt.datetime(2026, 10, 17, 8, 0, tzinfo=UTC), None) == 0


def test_attente_plafonnee():
    start = dt.datetime(2026, 10, 17, 20, 0, tzinfo=UTC)
    now = dt.datetime(2026, 10, 17, 6, 0, tzinfo=UTC)
    assert attendre.wait_seconds(now, start, 90, cap_min=330) == 330 * 60
