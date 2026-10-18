"""Le moteur de décision lit un rapport : JSON stocké ou texte collé."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hyperion.decision import decide, evaluate_criteria, render
from hyperion.reader import ReportUnreadable, read_record, read_text

FIX = Path(__file__).parent / "fixtures"
TEXTE = (FIX / "rapport_colle_20261018.txt").read_text(encoding="utf-8")


def test_texte_colle_lit_les_15_partants():
    rows, source, _ = read_text(TEXTE)
    assert len(rows) == 15 and "collé" in source
    assert rows[0]["number"] == 8 and rows[0]["name"] == "MIGHTY BOY"
    assert rows[1]["name"] == "PRINCESSE D'AMOUR"
    assert rows[0]["odds"] == pytest.approx(100 / 15.8)


def test_decision_depuis_texte_colle_choisit_les_favoris_du_marche():
    rows, source, _ = read_text(TEXTE)
    d = decide(rows, cote_source=source)
    assert [x.number for x in d.picks][:2] == [8, 4]
    assert d.classe in ("intermédiaire", "ouverte")


def test_selection_seule_comme_repli():
    texte = "\n".join(
        f"  {i}. n°{n} CHEVAL {n} — cote {c} · gagne 10 % · top 3 20 %" for i, (n, c) in enumerate([(1, 3.0), (2, 4.5), (3, 6), (4, 9), (5, 12)], 1)
    )
    rows, source, _ = read_text(texte)
    assert len(rows) == 5 and "partiel" in source


def test_texte_illisible_est_refuse_sans_rien_inventer():
    with pytest.raises(ReportUnreadable):
        read_text("bonjour, voici ma course du jour")


def test_lecture_d_un_enregistrement_json(tmp_path):
    rec = {"race_id": "x", "date": "2026-10-18", "classement": [{"horse_id": "a"}, {"horse_id": "b"}],
           "marketwatch": {"signals": [
               {"horse_id": f"h{i}", "name": f"C{i}", "number": i, "odds_pdf": 4.0 + i, "odds_latest": 3.5 + i} for i in range(1, 9)]}}
    f = tmp_path / "r.json"
    f.write_text(json.dumps(rec), encoding="utf-8")
    rows, source, meta = read_record(f)
    assert len(rows) == 8 and source == "cote directe PMU" and meta["ranking"] == ["a", "b"]


def test_criteres_fixes_par_l_utilisateur_respectes_ou_non():
    rows, source, _ = read_text(TEXTE)
    d = decide(rows, cote_source=source)
    strict = evaluate_criteria(d, {"criteres": {"min_p_au_moins_3": 0.99, "classes_acceptees": ["lisible"]}})
    souple = evaluate_criteria(d, {"criteres": {"min_p_au_moins_3": 0.01, "classes_acceptees": ["lisible", "intermédiaire", "ouverte"]}})
    assert not any(c["respecte"] for c in strict) and all(c["respecte"] for c in souple)
    assert "HORS de tes critères" in render(d, {"criteres": {"min_p_au_moins_3": 0.99, "classes_acceptees": ["lisible"]}})
    assert "ne promet aucun gain" in render(d)
