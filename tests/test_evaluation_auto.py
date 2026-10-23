"""Évaluation du soir : arrivée officielle PMU -> bilan -> cumul prédit/observé."""

from __future__ import annotations

import json
from pathlib import Path

from hyperion.evaluation import results as ev

FIX = Path(__file__).parent / "fixtures"
PROGRAMME = json.loads((FIX / "pmu_programme_20261017_trim.json").read_text(encoding="utf-8"))
COURSE3 = next(c for c in PROGRAMME["programme"]["reunions"][0]["courses"] if c["numOrdre"] == 3)
ARRIVEE = [int(n) for g in COURSE3["ordreArrivee"] for n in g]


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        return _Resp(PROGRAMME)


def _record(selection=(1, 2, 3, 4, 5), ranking=None):
    nums = list(range(1, 12))
    return {
        "kind": "analysis", "race_id": "20261017-auteuil-c3", "date": "2026-10-17",
        "meta": {"hippodrome": "AUTEUIL", "race_number": 3, "name": COURSE3["libelle"], "date": "2026-10-17"},
        "marketwatch": {"signals": [{"horse_id": f"c{n}", "number": n, "odds_latest": 2.0 + n} for n in nums]},
        "classement": [{"horse_id": f"c{n}", "numero": n} for n in (ranking or nums)],
        "decision": {"classe": "ouverte", "nb_attendu_dans_top5": 2.4, "p_gagnant_dans_selection": 0.6,
                     "p_au_moins": {"3": 0.45},
                     "selection": [{"numero": n} for n in selection],
                     "criteres": [{"respecte": False}]},
    }


def test_arrivee_officielle_lue_dans_le_programme():
    session = _Session()
    out = ev.fetch_official_results([_record()], session, log=lambda m: None)
    got = out["20261017-auteuil-c3"]
    assert got["numbers"] == ARRIVEE and got["result"].winner == f"c{ARRIVEE[0]}"
    assert session.calls[0].endswith("/programme/17102026")


def test_course_inconnue_ou_non_terminee_est_ignoree():
    rec = _record()
    rec["meta"]["hippodrome"] = "CHANTILLY"
    assert ev.fetch_official_results([rec], _Session(), log=lambda m: None) == {}


def test_bilan_compare_selection_systeme_et_favori():
    rec = _record(selection=tuple(ARRIVEE[:3]) + (98, 99), ranking=ARRIVEE + [99])
    bilan = ev.evaluate_record(rec, {"numbers": ARRIVEE})
    assert bilan["bons_dans_top5"] == 3 and bilan["gagnant_dans_selection"] is True
    assert bilan["au_moins_3_reel"] is True and bilan["systeme_gagnant_en_tete"] is True
    assert bilan["favori_marche"] == 1 and bilan["criteres_respectes"] is False


def test_cumul_compare_predit_et_observe_et_message_court(tmp_path):
    rec = _record()
    bilan = ev.evaluate_record(rec, {"numbers": ARRIVEE})
    path = ev.save_evaluation(bilan, rec, tmp_path)
    assert path.exists() and ev.eval_path(rec, tmp_path) == path
    cum = ev.cumulative(ev.load_evaluations(tmp_path))
    assert cum["courses_avec_decision"] == 1 and cum["bons_attendus"] == 2.4
    txt = ev.render_evening(bilan, cum)
    assert "RÉSULTAT" in txt and "attendu 2.4" in txt and "trop peu de courses" in txt


def test_les_enregistrements_de_resultat_ne_sont_pas_des_analyses():
    assert ev.is_analysis(_record())
    assert not ev.is_analysis({"kind": "official_result", "race_id": "x", "classement": [{"a": 1}]})
