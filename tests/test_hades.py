"""HADES : signal annexe, sélectif, à sources indépendantes."""

from __future__ import annotations

import datetime as dt

from hyperion.analysis.market import MarketWatch
from hyperion.anomaly.hades import HADES, INTERNAL_TOP, MAX_FLAGGED


def _inverse_ranking(race) -> list[str]:
    """Analyse interne qui contredit le marché : maximise les écarts de rang."""
    return [h.horse_id for h in reversed(race.order_by_odds())]


def test_selectif_au_plus_max_flagged_et_dans_le_top_interne(simple_race):
    ranking = _inverse_ranking(simple_race)
    result = HADES().analyse(simple_race, internal_ranking=ranking)
    assert len(result.flagged) <= MAX_FLAGGED
    for hid in result.flagged:
        assert ranking.index(hid) + 1 <= INTERNAL_TOP


def test_sans_mouvement_de_cote_severite_jamais_au_dessus_de_faible(simple_race):
    result = HADES().analyse(simple_race, internal_ranking=_inverse_ranking(simple_race))
    assert all(s.severity == "faible" for s in result.signals)
    assert any("Aucun mouvement de cote exploitable" in n for n in result.notes)


def test_non_partant_tardif_jamais_signale(simple_race):
    market = MarketWatch().observe(
        simple_race, as_of=dt.datetime.now(dt.timezone.utc), non_runners=["h1"]
    )
    result = HADES().analyse(
        simple_race, market=market, internal_ranking=_inverse_ranking(simple_race)
    )
    assert "h1" not in result.flagged
    assert all(s.horse_id != "h1" for s in result.signals)


def test_bruit_de_cote_sur_outsider_pas_de_signal(simple_race):
    outsider = simple_race.order_by_odds()[-1]
    # Raccourcissement relatif important mais négligeable en probabilité implicite.
    latest = {outsider.horse_id: outsider.odds_pdf * 0.78}
    market = MarketWatch().observe(simple_race, latest_odds=latest)
    result = HADES().analyse(simple_race, market=market)
    sig = result.signal(outsider.horse_id)
    assert sig is not None and not sig.signals


def test_hades_ne_modifie_aucun_score(simple_race):
    before = [h.horse_id for h in simple_race.runners]
    HADES().analyse(simple_race, internal_ranking=_inverse_ranking(simple_race))
    assert [h.horse_id for h in simple_race.runners] == before
