"""Correctifs issus du diagnostic de novembre : noms pollués, consensus externe, départ passé."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from hyperion.cli import after_departure
from hyperion.external import HttpPanelProvider
from hyperion.models import Horse, Race, RaceMeta
from hyperion.pmu import enrich_race, load_payload, names_match

FIXTURE = Path(__file__).parent / "fixtures" / "pmu_participants_20261015_R1C1.json"


def test_nom_pollue_par_le_driver_rapproche_du_nom_pmu():
    assert names_match("NOLITO G.GUEDJ-GAY", "NOLITO")
    assert names_match("NOLITO", "NOLITO G.GUEDJ-GAY")
    assert names_match("Côtelette", "COTELETTE")


def test_pas_de_faux_rapprochement():
    assert not names_match("ASTER MAN", "AS")          # préfixe sans frontière de mot
    assert not names_match("ROYALWOOD", "ROYAL")
    assert not names_match("", "NOLITO") and not names_match("NOLITO", None)


def test_enrichissement_apparie_le_cheval_pollue_et_garde_le_nom_pmu():
    payload = load_payload(FIXTURE)
    horses = [Horse(horse_id=p["nom"].lower().replace(" ", "-"), name=p["nom"], number=p["numPmu"])
              for p in payload["participants"]]
    horses[0] = Horse(horse_id="sweet-chop-x", name="SWEET CHOP A.LEMAITRE-MURPHY", number=1)
    race = Race(meta=RaceMeta(), horses=horses)
    enriched, report = enrich_race(race, payload)
    assert report.matched == 16 and not report.unmatched_race
    assert not [w for w in report.warnings if "ignoré" in w]
    first = enriched.horses[0]
    assert first.name == "SWEET CHOP" and first.driver == "A.LEMAITRE" and first.odds_latest == 5.2


def test_pages_web_ne_comptent_jamais_comme_pronostic(monkeypatch):
    horses = [Horse(horse_id=f"h{i}", name=f"CHEVAL NUMERO{i:02d}", number=i) for i in range(1, 9)]
    race = Race(meta=RaceMeta(date=dt.date(2026, 11, 11), hippodrome="TOULOUSE", race_number=3), horses=horses)
    page = " ".join(h.name for h in horses)  # noms dans l'ordre du programme

    class _Resp:
        status_code, text = 200, page

        def raise_for_status(self):
            pass

    provider = HttpPanelProvider({"Genybet": "http://exemple.invalid/{date}"})
    monkeypatch.setattr(HttpPanelProvider, "_get", lambda self, requests, url: page)
    picks = provider.fetch(race, panel=["Genybet"])
    assert picks[0].ranking and picks[0].coverage == 1.0
    assert picks[0].usable is False and "pas un pronostic" in picks[0].note


def test_depart_passe_detecte_sans_dependre_de_pmu():
    avant = RaceMeta(start_time=dt.datetime(2026, 11, 5, 12, 55, tzinfo=dt.timezone.utc))
    now = dt.datetime(2026, 11, 5, 14, 49, tzinfo=dt.timezone.utc)
    assert after_departure(avant, now) is True
    assert after_departure(avant, dt.datetime(2026, 11, 5, 11, 0, tzinfo=dt.timezone.utc)) is False
    assert after_departure(RaceMeta(), now) is False  # heure inconnue : on ne bloque pas
