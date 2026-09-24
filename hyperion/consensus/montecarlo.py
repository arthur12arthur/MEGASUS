"""Module 1.7a — MonteCarloEngine.

Simule la course un grand nombre de fois pour transformer un score de
compétitivité en probabilités de position.

Modèle : Plackett-Luce (Luce) échantillonné par l'astuce de Gumbel. Pour
chaque cheval on tire une utilité

    u_i = concentration × score_i + G_i ,  G_i ~ Gumbel(0, 1)

et le classement de la simulation est l'ordre décroissant des u_i. C'est un
échantillonnage EXACT du modèle de Plackett-Luce, pas une approximation :
les probabilités obtenues sont donc cohérentes entre elles (elles somment à
1 sur chaque position).

Robustesse : 5 seeds fixes × 10 000 simulations. La stabilité est mesurée
sur le Top 3 — le système est déclaré stable si le Top 3 est identique sur
au moins 4 seeds sur 5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

#: Nombre minimal de seeds devant produire le même Top N pour déclarer stable.
MIN_STABLE_SEEDS = 4


@dataclass
class HorseProbabilities:
    """Probabilités de position d'un cheval, issues des simulations."""

    horse_id: str
    name: str
    p_win: float = 0.0
    p_top3: float = 0.0
    p_top5: float = 0.0
    expected_position: float = 0.0
    position_distribution: dict[int, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "horse_id": self.horse_id,
            "name": self.name,
            "p_win": round(self.p_win, 6),
            "p_top3": round(self.p_top3, 6),
            "p_top5": round(self.p_top5, 6),
            "expected_position": round(self.expected_position, 4),
            "position_distribution": {
                str(k): round(v, 6) for k, v in sorted(self.position_distribution.items())
            },
        }


@dataclass
class MonteCarloResult:
    """Sortie du moteur Monte Carlo."""

    seeds: list[int]
    simulations: int
    concentration: float
    probabilities: dict[str, HorseProbabilities]
    per_seed_top: dict[int, list[str]] = field(default_factory=dict)
    reference_top: list[str] = field(default_factory=list)
    stable_seeds: int = 0
    stability: float = 0.0
    is_stable: bool = False

    def ordered(self) -> list[HorseProbabilities]:
        return sorted(self.probabilities.values(), key=lambda p: p.expected_position)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seeds": list(self.seeds),
            "simulations": self.simulations,
            "concentration": self.concentration,
            "probabilities": {
                hid: p.as_dict() for hid, p in self.probabilities.items()
            },
            "per_seed_top": {str(k): v for k, v in self.per_seed_top.items()},
            "reference_top": list(self.reference_top),
            "stable_seeds": self.stable_seeds,
            "stability": round(self.stability, 4),
            "is_stable": self.is_stable,
        }


class MonteCarloEngine:
    """Moteur de simulation Plackett-Luce."""

    def __init__(
        self,
        seeds: Sequence[int] = (11, 23, 37, 51, 73),
        simulations: int = 10_000,
        concentration: float = 0.8,
        top_n: int = 3,
    ) -> None:
        if simulations <= 0:
            raise ValueError("le nombre de simulations doit être positif")
        if concentration <= 0:
            raise ValueError("la concentration doit être positive")
        self.seeds = [int(seed) for seed in seeds]
        self.simulations = int(simulations)
        self.concentration = float(concentration)
        self.top_n = int(top_n)

    # -- simulation --------------------------------------------------------

    def simulate_positions(self, scores: np.ndarray, seed: int) -> np.ndarray:
        """Positions (simulations × chevaux) pour une seed donnée."""
        rng = np.random.default_rng(seed)
        gumbel = rng.gumbel(size=(self.simulations, scores.shape[0]))
        utility = scores[None, :] * self.concentration + gumbel
        order = np.argsort(-utility, axis=1)  # indices du meilleur au pire
        ranks = np.empty_like(order)
        rows = np.arange(self.simulations)[:, None]
        ranks[rows, order] = np.arange(1, scores.shape[0] + 1)[None, :]
        return ranks

    def run(
        self,
        competitiveness: Mapping[str, float],
        names: Mapping[str, str] | None = None,
    ) -> MonteCarloResult:
        names = names or {}
        ids = list(competitiveness)
        if not ids:
            return MonteCarloResult(
                seeds=self.seeds,
                simulations=self.simulations,
                concentration=self.concentration,
                probabilities={},
            )
        scores = np.array([float(competitiveness[i]) for i in ids], dtype=float)
        n = len(ids)

        rank_sums = np.zeros(n, dtype=float)
        win_counts = np.zeros(n, dtype=float)
        top3_counts = np.zeros(n, dtype=float)
        top5_counts = np.zeros(n, dtype=float)
        #: position_counts[i, p] = nombre de fois où le cheval i a fini p-ème.
        position_counts = np.zeros((n, n + 1), dtype=float)
        per_seed_top: dict[int, list[str]] = {}

        for seed in self.seeds:
            ranks = self.simulate_positions(scores, seed)
            rank_sums += ranks.sum(axis=0)
            win_counts += (ranks == 1).sum(axis=0)
            top3_counts += (ranks <= 3).sum(axis=0)
            top5_counts += (ranks <= 5).sum(axis=0)
            for i in range(n):
                positions, freq = np.unique(ranks[:, i], return_counts=True)
                position_counts[i, positions] += freq
            expected = ranks.mean(axis=0)
            order = np.argsort(expected)
            per_seed_top[seed] = [ids[i] for i in order[: self.top_n]]

        total = self.simulations * len(self.seeds)
        probabilities: dict[str, HorseProbabilities] = {}
        for index, hid in enumerate(ids):
            row = position_counts[index]
            distribution = {
                int(pos): float(row[pos] / total)
                for pos in range(1, n + 1)
                if row[pos] > 0
            }
            probabilities[hid] = HorseProbabilities(
                horse_id=hid,
                name=names.get(hid, hid),
                p_win=float(win_counts[index] / total),
                p_top3=float(top3_counts[index] / total),
                p_top5=float(top5_counts[index] / total),
                expected_position=float(rank_sums[index] / total),
                position_distribution=distribution,
            )

        # Stabilité inter-seeds : Top N majoritaire comme référence.
        counts: dict[str, int] = {}
        for top in per_seed_top.values():
            key = tuple(sorted(top))
            counts[key] = counts.get(key, 0) + 1
        reference_key = max(counts, key=lambda k: counts[k])
        reference_top = list(reference_key)
        stable = counts[reference_key]
        return MonteCarloResult(
            seeds=list(self.seeds),
            simulations=self.simulations,
            concentration=self.concentration,
            probabilities=probabilities,
            per_seed_top=per_seed_top,
            reference_top=reference_top,
            stable_seeds=stable,
            stability=stable / len(self.seeds) if self.seeds else 0.0,
            is_stable=stable >= min(MIN_STABLE_SEEDS, len(self.seeds)),
        )


def calibration_grid() -> list[float]:
    """Grille de calibration du paramètre de concentration (labo Ouroboros)."""
    return [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0]


__all__ = [
    "MonteCarloEngine",
    "MonteCarloResult",
    "HorseProbabilities",
    "calibration_grid",
    "MIN_STABLE_SEEDS",
]
