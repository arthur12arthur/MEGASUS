"""Contexte relais : course française, marché LONAB burkinabè."""

from __future__ import annotations

import datetime as dt

from hyperion import relay
from hyperion.ingestion import verify_identity
from hyperion.models import Discipline, Race, RaceMeta


class TestPariDuJour:
    def test_calendrier_lonab(self):
        # Semaine du lundi 14 au dimanche 20 septembre 2026.
        expected = {
            14: ("Quarté", 4),  # lundi
            15: ("Quarté", 4),  # mardi
            16: ("Tiercé", 3),  # mercredi
            17: ("Quarté", 4),  # jeudi
            18: ("4+1", 5),  # vendredi
            19: ("Tiercé", 3),  # samedi
            20: ("4+1", 5),  # dimanche
        }
        for day, (name, places) in expected.items():
            game = relay.lonab_game_for(dt.date(2026, 9, day))
            assert (game.name, game.places) == (name, places), day

    def test_dernier_mardi_du_mois(self):
        game = relay.lonab_game_for(dt.date(2026, 9, 29))
        assert game.name == "4+1"
        assert "dernier mardi" in game.reason
        assert relay.lonab_game_for(dt.date(2026, 9, 22)).name == "Quarté"

    def test_programme_prioritaire_sur_calendrier(self):
        game = relay.lonab_game_for(dt.date(2026, 9, 20), declared="TIERCE")
        assert game.name == "Tiercé"
        assert game.reason == "lu dans le programme"

    def test_normalisation(self):
        assert relay.normalise_game("4 + 1") == "4+1"
        assert relay.normalise_game("QUARTE") == "Quarté"
        assert relay.normalise_game("tiercé") == "Tiercé"
        assert relay.normalise_game("Couplé") is None
        assert relay.lonab_game_for(None) is None


class TestHoraires:
    def test_ete_deux_heures_d_ecart(self):
        start = dt.datetime(2026, 9, 20, 14, 15)  # naïve = heure du programme LONAB
        schedule = relay.schedule_for(start)
        assert schedule.offset_hours == 2
        assert schedule.start_france.hour == 16
        assert (schedule.close_relay.hour, schedule.close_relay.minute) == (14, 5)

    def test_hiver_une_heure_d_ecart(self):
        # Exemple observé : départ 14h15 (Ouagadougou), clôture 14h05, Vincennes.
        schedule = relay.schedule_for(dt.datetime(2024, 12, 15, 14, 15))
        assert schedule.offset_hours == 1
        assert schedule.start_france.hour == 15
        assert "15h15 heure de Paris" in schedule.describe()

    def test_heure_de_paris_convertie(self):
        from zoneinfo import ZoneInfo

        start = dt.datetime(2026, 9, 20, 15, 15, tzinfo=ZoneInfo("Europe/Paris"))
        schedule = relay.schedule_for(start)
        assert schedule.start_relay.hour == 13

    def test_cloture_du_programme(self):
        start = dt.datetime(2026, 9, 20, 14, 15)
        close = dt.datetime(2026, 9, 20, 14, 0)
        schedule = relay.schedule_for(start, close)
        assert schedule.close_source == "programme"
        assert schedule.close_relay.minute == 0

    def test_cloture_incoherente_ignoree(self):
        start = dt.datetime(2026, 9, 20, 14, 15)
        schedule = relay.schedule_for(start, dt.datetime(2026, 9, 20, 14, 30), closing_minutes=15)
        assert schedule.close_source == "départ − 15 min"
        assert schedule.close_relay.minute == 0

    def test_sans_heure(self):
        assert relay.schedule_for(None) is None


class TestHippodromes:
    def test_reconnaissance(self):
        assert relay.lookup_hippodrome("R1 - PARIS-VINCENNES")[0] == "Paris-Vincennes"
        assert relay.lookup_hippodrome("Hippodrome de Chantilly")[0] == "Chantilly"
        assert relay.lookup_hippodrome("Cagnes-sur-Mer")[0] == "Cagnes-sur-Mer"
        assert relay.lookup_hippodrome("Ouagadougou") is None

    def test_indice_mono_discipline_seulement(self):
        assert relay.discipline_hint("Auteuil")[0] is Discipline.OBSTACLE
        assert relay.discipline_hint("ParisLongchamp")[0] is Discipline.PLAT
        # Vincennes : attelé OU monté -> aucune devinette.
        assert relay.discipline_hint("Vincennes")[0] is None

    def test_incoherences(self):
        assert relay.consistency_warnings("Vincennes", Discipline.PLAT)
        assert relay.consistency_warnings("Auteuil", Discipline.TROT_ATTELE)
        assert not relay.consistency_warnings("Vincennes", Discipline.TROT_MONTE)
        assert not relay.consistency_warnings("Cagnes-sur-Mer", Discipline.PLAT)
        assert not relay.consistency_warnings("Inconnu", Discipline.PLAT)

    def test_pays_de_la_course(self):
        assert relay.race_country_for("Deauville") == ("France", "hippodrome français reconnu : Deauville")
        country, why = relay.race_country_for(None)
        assert country == "France" and "par défaut" in why


class TestIdentite:
    def test_course_francaise_acceptee(self):
        meta = RaceMeta(operator="LONAB", country="Burkina Faso", race_country="France")
        assert verify_identity(meta).ok

    def test_course_hors_france_refusee(self):
        meta = RaceMeta(operator="LONAB", country="Burkina Faso", race_country="Suède")
        check = verify_identity(meta)
        assert not check.ok
        assert "relaie" in check.reasons[0]


class TestCompatibilite:
    def test_ancien_enregistrement_sans_pays_de_course(self):
        race = Race.from_dict(
            {"race_id": "x", "meta": {"operator": "LONAB", "country": "Burkina Faso",
                                      "start_time": "2026-09-20T14:15:00+00:00"}, "horses": []}
        )
        assert race.meta.race_country == "France"
        assert race.meta.betting_close is None

    def test_aller_retour_json(self):
        meta = RaceMeta(
            race_country="France",
            betting_close=dt.datetime(2026, 9, 20, 14, 5, tzinfo=dt.timezone.utc),
            bet_type="4+1",
        )
        again = Race.from_dict(Race(meta=meta).as_dict())
        assert again.meta.betting_close == meta.betting_close
        assert again.meta.bet_type == "4+1"
