"""Moteur de décision : probabilités cohérentes, ouverture mesurée, rapport recentré."""

from __future__ import annotations

import numpy as np
import pytest

from hyperion.config import Settings
from hyperion.decision import (
    calibrate,
    decide,
    implied,
    openness,
    openness_class,
    place_probabilities,
    render,
)
from hyperion.pipeline import run_pipeline

ODDS = [2.5, 4.0, 6.0, 8.0, 12.0, 15.0, 25.0, 40.0]


def _runners(odds=ODDS):
    return [{"horse_id": f"n{i+1}", "number": i + 1, "name": f"CHEVAL {i+1}", "odds": o} for i, o in enumerate(odds)]


def test_probabilites_somment_a_un_et_suivent_la_cote():
    q = implied(ODDS)
    assert q.sum() == pytest.approx(1.0) and np.all(np.diff(q) < 0)
    p = calibrate(q, 1.08)
    assert p.sum() == pytest.approx(1.0)


def test_probabilites_de_place_coherentes():
    p = calibrate(implied(ODDS), 1.08)
    pk = place_probabilities(p, 8000, 1, ks=(1, 3, 5), gamma=0.5)
    assert pk[1].sum() == pytest.approx(1.0, abs=0.01)
    assert pk[3].sum() == pytest.approx(3.0, abs=0.05) and pk[5].sum() == pytest.approx(5.0, abs=0.05)
    assert np.all(pk[5] >= pk[3] - 0.02) and pk[3][0] > pk[3][-1]


def test_ouverture_discrimine_course_lisible_et_ouverte():
    lisible = openness(calibrate(implied([1.8, 5.0, 9.0, 14.0, 20.0, 30.0, 50.0, 80.0]), 1.0))
    ouverte = openness(calibrate(implied([7.0, 8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0]), 1.0))
    assert lisible["n_eff"] < ouverte["n_eff"]
    seuils = [4.76, 6.32]
    assert openness_class(lisible["n_eff"], seuils) == "lisible"
    assert openness_class(ouverte["n_eff"], seuils) == "ouverte"


def test_decision_selectionne_les_5_meilleures_probabilites_et_chiffre_la_selection():
    d = decide(_runners(), system_ranking=["n8", "n1", "n2", "n3", "n4"])
    assert [x.number for x in d.picks] == [1, 2, 3, 4, 5]
    assert 0.0 < d.p_gagnant_dans_selection <= 1.0
    assert 0.0 < d.nb_attendu_dans_top5 <= 5.0
    assert d.p_au_moins[1] >= d.p_au_moins[3] >= d.p_au_moins[5]
    assert d.picks[0].system_rank == 2 and d.picks[3].system_rank == 5
    assert d.picks[4].system_rank is None  # n°5 absent du classement fourni


def test_decision_reproductible():
    a, b = decide(_runners()).as_dict(), decide(_runners()).as_dict()
    assert a == b


def test_decision_refuse_moins_de_4_cotes():
    with pytest.raises(ValueError):
        decide(_runners([2.0, 3.0, 5.0]))


def test_rendu_mentionne_source_risque_et_pas_de_promesse():
    txt = render(decide(_runners(), cote_source="cote directe PMU", cote_horodatage="11:56"))
    assert "cote directe PMU" in txt and "ne promet aucun gain" in txt
    assert "Sélection" in txt and "top 5" in txt


def test_rapport_recentre_sur_la_decision(simple_race):
    result = run_pipeline(simple_race, Settings())
    blocks = result.blocks
    assert "2/5 · Décision" in blocks and "DÉCISION" in blocks["2/5 · Décision"]
    assert not any("Confiance" in title for title in blocks)
    texte = "\n".join(blocks.values())
    assert "valeur masquée" not in texte.lower()
    assert "secondaire" in "".join(blocks)
    assert result.record["decision"]["selection"]
