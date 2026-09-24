"""Test fonctionnel : le système bat-il le hasard et la cote ?

C'est le test qui valide l'ensemble du pipeline sur un grand nombre de
courses à vérité connue. Il mesure :

  * le taux de gagnant présent dans le Top 5 du système (référence hasard :
    5/n) ;
  * le taux de gagnant en tête du classement ;
  * la comparaison avec le MARCHÉ (la cote la plus basse) — un système qui ne
    bat pas la cote n'apporte rien ;
  * la calibration des probabilités annoncées (score de Brier).
"""

from __future__ import annotations

import datetime as dt
import statistics

import pytest

from hyperion.lab import Ouroboros
from hyperion.synthetic import SyntheticProvider

#: Nombre de courses du backtest (compromis temps de test / robustesse).
N_RACES = 30


@pytest.fixture(scope="module")
def backtest():
    """Backtest complet + les deux références nécessaires pour juger le résultat."""
    import random as _random

    provider = SyntheticProvider(seed=2026)
    ouroboros = Ouroboros()
    runs = []
    market_hits = 0
    random_hits = 0
    n = N_RACES
    for index in range(n):
        synthetic = provider.race(
            dt.date(2026, 1, 1) + dt.timedelta(days=index), index=index + 1
        )
        actual = synthetic.simulate_outcome(seed=5000 + index)
        runs.append(ouroboros.evaluate_shadow(synthetic.race, actual))

        winner = actual[0]
        # Référence 1 : le marché (les 5 cotes les plus basses).
        market_top5 = [h.horse_id for h in synthetic.race.order_by_odds()[:5]]
        market_hits += 1 if winner in market_top5 else 0
        # Référence 2 : le hasard (5 chevaux tirés au sort).
        field = [h.horse_id for h in synthetic.race.runners]
        draw = _random.Random(index).sample(field, min(5, len(field)))
        random_hits += 1 if winner in draw else 0
    return runs, market_hits / n, random_hits / n


class TestBacktest:
    def test_le_systeme_bat_largement_le_hasard(self, backtest):
        """Le signal existe : les observables portent une information réelle."""
        runs, _, random_rate = backtest
        system_rate = sum(1 for r in runs if r.winner_in_top5) / len(runs)
        assert system_rate > random_rate + 0.20, (
            f"système {system_rate:.0%} vs hasard {random_rate:.0%}"
        )

    def test_le_systeme_est_competitif_face_a_un_marche_efficace(self, backtest):
        """Un marché synthétique efficient est la référence difficile.

        Le système ne doit pas être dominé par la cote : l'objectif n'est pas
        de battre le marché sur les favoris, mais de rester dans le même ordre
        de grandeur tout en apportant un classement et des probabilités.
        """
        runs, market_rate, _ = backtest
        system_rate = sum(1 for r in runs if r.winner_in_top5) / len(runs)
        assert system_rate >= market_rate - 0.10, (
            f"système {system_rate:.0%} vs marché {market_rate:.0%}"
        )

    def test_le_gagnant_en_tete_bat_le_hasard(self, backtest):
        """Annoncer le bon gagnant est bien mieux qu'un tirage au sort."""
        runs, _, random_rate = backtest
        system_rate = sum(1 for r in runs if r.winner_in_top1) / len(runs)
        chance = 1 / statistics.mean(r.field_size for r in runs)
        assert system_rate > chance + 0.05, (
            f"système {system_rate:.0%} vs hasard {chance:.0%}"
        )

    def test_recouvrement_du_top5(self, backtest):
        runs, _, _ = backtest
        overlap = statistics.mean(r.top5_overlap for r in runs)
        assert 0.0 <= overlap <= 1.0

    def test_calibration_des_probabilites(self, backtest):
        runs, _, _ = backtest
        briers = [r.brier for r in runs if r.brier is not None]
        assert briers
        mean_brier = statistics.mean(briers)
        # Un modèle incohérent dépasserait largement 0.15.
        assert mean_brier < 0.15, f"Brier moyen {mean_brier:.3f}"

    def test_log_loss_raisonnable(self, backtest):
        runs, _, _ = backtest
        losses = [r.log_loss for r in runs if r.log_loss is not None]
        assert losses
        assert statistics.mean(losses) < 4.0

    def test_toutes_les_courses_ont_un_classement(self, backtest):
        runs, _, _ = backtest
        for run in runs:
            assert len(run.predicted_top) == 5
            assert run.actual_order
            assert run.field_size >= 8

    def test_reproductibilite_du_backtest(self):
        """Deux exécutions identiques doivent donner le même résultat."""
        provider = SyntheticProvider(seed=77)
        ouroboros = Ouroboros()
        first = ouroboros.evaluate_shadow(
            provider.race(dt.date(2026, 3, 1), 1),
            provider.race(dt.date(2026, 3, 1), 1).simulate_outcome(seed=9),
        )
        second = ouroboros.evaluate_shadow(
            provider.race(dt.date(2026, 3, 1), 1),
            provider.race(dt.date(2026, 3, 1), 1).simulate_outcome(seed=9),
        )
        assert first.predicted_top == second.predicted_top
        assert first.log_loss == second.log_loss
