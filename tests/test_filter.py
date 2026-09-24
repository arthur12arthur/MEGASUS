"""Tests du module 1.5 — DataFilter (portillon éliminatoire)."""

from __future__ import annotations

import datetime as dt

from hyperion.analysis.filter import DataFilter
from hyperion.analysis.market import MarketWatch
from hyperion.config import Settings


def _observe(race, latest=None, non_runners=()):
    return MarketWatch().observe(
        race,
        latest_odds=latest or {},
        as_of=dt.datetime(2026, 9, 20, 10, tzinfo=dt.timezone.utc),
        non_runners=non_runners,
    )


class TestDataFilter:
    def test_reduit_le_champ(self, simple_race):
        result = DataFilter().filter(simple_race, _observe(simple_race))
        assert 0 < len(result.selected) < len(simple_race.runners)
        assert len(result.selected) + len(result.excluded) == len(simple_race.runners)

    def test_groupe_minimum_respecte(self, simple_race):
        result = DataFilter().filter(simple_race, _observe(simple_race))
        assert len(result.selected) >= Settings().filter_min_group

    def test_favoris_toujours_retenus(self, simple_race):
        result = DataFilter().filter(simple_race, _observe(simple_race))
        favourites = {h.horse_id for h in simple_race.order_by_odds()[:5]}
        assert favourites.issubset(set(result.selected))

    def test_exclusion_avec_motif(self, simple_race):
        result = DataFilter().filter(simple_race, _observe(simple_race))
        for exclusion in result.excluded:
            assert exclusion.reason
            assert exclusion.risk_points >= Settings().filter_threshold_points

    def test_non_partant_ecarte(self, simple_race):
        result = DataFilter().filter(simple_race, _observe(simple_race, non_runners=["h1"]))
        assert "h1" in {e.horse_id for e in result.excluded}
        assert any("non-partant" in e.reason for e in result.excluded)

    def test_aucun_score_attache_aux_retenus(self, simple_race):
        """Correctif majeur : le portillon ne note PAS les chevaux retenus."""
        result = DataFilter().filter(simple_race, _observe(simple_race))
        # Les points de risque existent comme diagnostic interne uniquement.
        assert set(result.risk_diagnostics) == {h.horse_id for h in simple_race.runners}
        # Mais aucune note de sélection n'est attachée aux chevaux retenus.
        assert not hasattr(result, "selection_scores")

    def test_reintegration_si_groupe_trop_petit(self, simple_race):
        """Le groupe ne doit jamais descendre sous le minimum configuré.

        Cinq chevaux solides et un outsider à tous les égards : avec un
        plancher de 6, l'outsider est écarté par le seuil de risque puis
        réintégré par la règle de plancher.
        """
        settings = Settings(**{**Settings().__dict__, "filter_min_group": 6})
        race = simple_race
        race.horses = race.horses[:6]
        for index, horse in enumerate(race.horses[:5]):
            horse.gains = 1_000_000
            horse.recent_places = (1, 2, 3, 4, 5)
            horse.odds_pdf = 3.0 + index
        outsider = race.horses[5]
        outsider.gains = 0
        outsider.recent_places = (0, 0, 0, 0, 0)
        outsider.odds_pdf = 50.0
        outsider.history_stats = {"weeks_since_last_run": 40}
        result = DataFilter(settings).filter(race, _observe(race))
        assert outsider.horse_id in {e.horse_id for e in result.excluded}
        assert len(result.selected) == 6
        assert any("réintégré" in note for note in result.notes)

    def test_course_vide(self, simple_race):
        simple_race.horses = []
        result = DataFilter().filter(simple_race, _observe(simple_race))
        assert result.selected == []
        assert result.notes

    def test_delta_de_cote_compte_dans_le_risque(self, simple_race):
        """Le filtrage doit voir une cote à jour, pas celle du PDF."""
        without = DataFilter().filter(simple_race, _observe(simple_race, {"h6": 30.0}))
        with_drift = DataFilter().filter(
            simple_race, _observe(simple_race, {"h6": 40.0})
        )
        assert without.risk_diagnostics["h6"] <= with_drift.risk_diagnostics["h6"]
