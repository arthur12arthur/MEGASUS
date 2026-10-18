"""Backtest sur courses réelles : reconstruction sans fuite, métriques correctes."""

from __future__ import annotations

import json
from pathlib import Path

from hyperion.evaluation.realbacktest import (
    actual_order,
    build_race,
    summarise,
    wilson,
)
from hyperion.history import collect_course
from tests.test_history import PARTICIPANTS, PROGRAMME, _Session
from hyperion.history import list_courses


def _record():
    ref = next(r for r in list_courses(PROGRAMME, "all") if r["course"] == 3)
    return collect_course(__import__("datetime").date(2026, 10, 17), ref, _Session())


def test_course_reconstruite_sans_aucune_donnee_de_resultat():
    race = build_race(_record())
    blob = json.dumps([h.as_dict() for h in race.horses], default=str)
    assert "ordre_arrivee" not in blob and "ordreArrivee" not in blob
    assert race.meta.discipline.value != "unknown" and len(race.runners) >= 6


def test_arrivee_officielle_aplatie_dans_l_ordre():
    rec = {"arrivee": [[3], [7], [1, 5], [2]]}
    assert actual_order(rec) == ["n3", "n7", "n1", "n5", "n2"]


def test_wilson_encadre_la_proportion():
    lo, hi = wilson(30, 100)
    assert lo < 0.30 < hi and 0 < lo and hi < 1
    assert wilson(0, 0) == (0.0, 0.0)


def test_summarise_compare_systeme_marche_hasard():
    rows = [
        {"system_top1": 1, "system_top3": 1, "system_top5": 1, "market_top1": 0, "market_top3": 1,
         "market_top5": 1, "random_top1": 0.1, "random_top3": 0.3, "random_top5": 0.5,
         "system_overlap3": 2, "market_overlap3": 1, "system_overlap5": 3, "market_overlap5": 2,
         "random_overlap3": 0.9, "random_overlap5": 2.5},
        {"system_top1": 0, "system_top3": 0, "system_top5": 0, "market_top1": 1, "market_top3": 1,
         "market_top5": 1, "random_top1": 0.1, "random_top3": 0.3, "random_top5": 0.5,
         "system_overlap3": 0, "market_overlap3": 2, "system_overlap5": 1, "market_overlap5": 3,
         "random_overlap3": 0.9, "random_overlap5": 2.5},
    ]
    s = summarise(rows)
    assert s["system_top1"] == 0.5 and s["market_top1"] == 0.5 and s["random_top1"] == 0.1
    assert s["top5_marche_seul"] == 1 and s["top5_sys_seul"] == 0
