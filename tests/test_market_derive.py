"""La dérive de cote ne compte que si elle déplace réellement la probabilité implicite."""

from __future__ import annotations

from hyperion.analysis.market import MIN_IMPLIED_SHIFT, MarketSignal, _direction


def _sig(pdf: float, latest: float) -> MarketSignal:
    return MarketSignal(
        horse_id="h", name="H", odds_pdf=pdf, odds_latest=latest,
        delta=(latest - pdf) / pdf, direction=_direction((latest - pdf) / pdf, pdf, latest),
    )


def test_bruit_sur_outsider_n_est_pas_une_derive():
    sig = _sig(30.0, 36.0)  # +20 % mais ~0,5 point de probabilité
    assert not sig.drifting
    assert sig.direction == "stable"


def test_vraie_derive_sur_favori_detectee():
    sig = _sig(4.0, 5.0)  # +25 % et 5 points de probabilité
    assert sig.drifting
    assert sig.direction == "derive"


def test_raccourcissement_jamais_penalise():
    sig = _sig(8.0, 5.0)
    assert not sig.drifting
    assert sig.direction == "raccourci"


def test_implied_shift_signe_et_seuil():
    assert _sig(4.0, 5.0).implied_shift > MIN_IMPLIED_SHIFT
    assert _sig(5.0, 4.0).implied_shift < 0
