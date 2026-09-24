"""Tests des modèles de données."""

from __future__ import annotations

import datetime as dt
import json

from hyperion.models import (
    Discipline,
    Horse,
    OfficialResult,
    Race,
    Shoeing,
)


class TestHorse:
    def test_odds_prend_la_plus_fraiche(self):
        horse = Horse(horse_id="h", name="X", odds_pdf=10.0, odds_latest=6.0)
        assert horse.odds == 6.0

    def test_odds_sans_cote_fraiche(self):
        horse = Horse(horse_id="h", name="X", odds_pdf=10.0)
        assert horse.odds == 10.0

    def test_delta_relatif(self):
        horse = Horse(horse_id="h", name="X", odds_pdf=10.0, odds_latest=8.0)
        assert horse.odds_delta == -0.2

    def test_delta_sans_cote_pdf(self):
        horse = Horse(horse_id="h", name="X", odds_latest=8.0)
        assert horse.odds_delta is None

    def test_recent_places_tolere_une_liste(self):
        horse = Horse(horse_id="h", name="X", recent_places=[1, 2, 0])
        assert horse.recent_places == (1, 2, 0)

    def test_identifiant_genere_depuis_le_nom(self):
        horse = Horse(horse_id="", name="Tonnerre de Mai")
        assert horse.horse_id == "tonnerre-de-mai"

    def test_seriejson_ronde(self):
        horse = Horse(
            horse_id="h",
            name="X",
            odds_pdf=4.0,
            recent_places=[1, 3],
            shoeing=Shoeing(label="D4", adequacy=0.5),
        )
        data = horse.as_dict()
        assert json.loads(json.dumps(data))["recent_places"] == [1, 3]
        assert data["shoeing"]["adequacy"] == 0.5


class TestRace:
    def test_runners_exclut_les_non_partants(self, simple_race):
        simple_race.horses[0].non_runner = True
        assert all(h.horse_id != "h1" for h in simple_race.runners)

    def test_order_by_odds(self, simple_race):
        order = simple_race.order_by_odds()
        assert order[0].horse_id == "h1"
        assert [h.odds for h in order] == sorted(h.odds for h in simple_race.runners)

    def test_names_map(self, simple_race):
        names = simple_race.names_map()
        assert names["h1"] == "Tonnerre de Mai"

    def test_serialisation_roundtrip(self, simple_race):
        restored = Race.from_dict(json.loads(json.dumps(simple_race.as_dict())))
        assert restored.race_id == simple_race.race_id
        assert restored.meta.discipline is Discipline.TROT_ATTELE
        assert restored.meta.date == simple_race.meta.date
        assert restored.horses[0].recent_places == simple_race.horses[0].recent_places
        assert restored.horses[0].shoeing.adequacy == simple_race.horses[0].shoeing.adequacy


class TestOfficialResult:
    def test_winner(self):
        result = OfficialResult(race_id="r", date=dt.date(2026, 9, 20), order=["a", "b", "c"])
        assert result.winner == "a"

    def test_position_of(self):
        result = OfficialResult(race_id="r", date=dt.date(2026, 9, 20), order=["a", "b"])
        assert result.position_of("b") == 2
        assert result.position_of("z") is None

    def test_roundtrip(self):
        result = OfficialResult(race_id="r", date=dt.date(2026, 9, 20), order=["a", "b"])
        restored = OfficialResult.from_dict(result.as_dict())
        assert restored.order == ["a", "b"]
        assert restored.date == dt.date(2026, 9, 20)
