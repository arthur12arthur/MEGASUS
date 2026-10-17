"""Indice de confiance : le maillon le plus faible borne l'indice."""

from __future__ import annotations

from hyperion.analysis.scorer import BaseScorer
from hyperion.confidence import CAP_FAIBLE, CAP_MODEREE, ConfidenceIndex, score_coverage
from hyperion.config import Settings
from hyperion.models import Discipline


def _compute(race, **kw):
    comp = {h.horse_id: 10.0 - i * 3.0 for i, h in enumerate(race.runners)}  # gros écart
    base = dict(stability=1.0, convergence=1.0)
    base.update(kw)
    return ConfidenceIndex().compute(race, comp, **base)


def test_aucun_plafond_quand_tout_va_bien(simple_race):
    res = _compute(simple_race, scoring_coverage=1.0)
    assert not [c for c in res.caps if "instable" in c or "identifiée" in c or "modèle" in c]


def test_identification_incomplete_plafonne_a_faible(simple_race):
    res = _compute(simple_race, scoring_coverage=1.0, identification=["hippodrome non identifié"])
    assert res.index <= CAP_FAIBLE
    assert any("mal identifiée" in c for c in res.caps)


def test_score_sur_une_seule_dimension_plafonne_a_faible(simple_race):
    res = _compute(simple_race, scoring_coverage=0.28)
    assert res.index <= CAP_FAIBLE
    assert any("28%" in c for c in res.caps)


def test_simulations_instables_plafonnent_a_moderee(simple_race):
    res = _compute(simple_race, scoring_coverage=1.0, stability=0.4)
    assert res.index <= CAP_MODEREE


def test_couverture_du_score_reelle(simple_race):
    scorer = BaseScorer(Settings()).score(
        simple_race, [h.horse_id for h in simple_race.runners], Discipline.TROT_ATTELE
    )
    cov = score_coverage(scorer)
    assert cov is not None and 0.0 < cov <= 1.0
    for s in scorer.scores:
        s.dimensions = {k: (1.0 if k == "historique" else None) for k in s.dimensions}
    assert score_coverage(scorer) < 0.5


def test_as_dict_expose_les_plafonds(simple_race):
    res = _compute(simple_race, scoring_coverage=0.2)
    assert res.as_dict()["caps"]
