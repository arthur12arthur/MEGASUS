"""Module 1.7b — BordaConsensus.

Agrège plusieurs classements en un seul par comptage de Borda : dans un
classement de n candidats, le premier reçoit n-1 points, le deuxième n-2,
etc. C'est une méthode de vote par classement qui résiste mieux qu'une
moyenne de rangs aux classements aberrantes d'une source isolée.

Deux usages dans le pipeline :
  * Borda sur les classements produits par chacune des 5 seeds Monte Carlo
    (fusion inter-seeds) ;
  * Borda sur le classement de compétitivité du BaseScorer, qui alimente
    ensuite MetaFusion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

Ranking = Sequence[str]


@dataclass
class BordaResult:
    """Résultat d'un comptage de Borda."""

    points: dict[str, float] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    rankings_used: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "points": {k: round(v, 4) for k, v in self.points.items()},
            "order": list(self.order),
            "rankings_used": self.rankings_used,
        }


def borda_points(rankings: Sequence[Ranking]) -> dict[str, float]:
    """Compte les points de Borda d'un ensemble de classements.

    Un classement peut être partiel : les candidats absents reçoivent 0 point
    pour ce classement (ils ne sont pas pénalisés au-delà).
    """
    totals: dict[str, float] = {}
    for ranking in rankings:
        n = len(ranking)
        for position, candidate in enumerate(ranking):
            totals[candidate] = totals.get(candidate, 0.0) + (n - 1 - position)
    return totals


def borda_order(rankings: Sequence[Ranking]) -> list[str]:
    """Classement agrégé par points de Borda décroissants."""
    points = borda_points(rankings)
    # Départage par rang moyen pour rester déterministe.
    return sorted(points, key=lambda c: (-points[c], _mean_rank(c, rankings), str(c)))


def _mean_rank(candidate: str, rankings: Sequence[Ranking]) -> float:
    ranks = [
        float(index)
        for ranking in rankings
        for index, item in enumerate(ranking)
        if item == candidate
    ]
    return sum(ranks) / len(ranks) if ranks else float("inf")


class BordaConsensus:
    """Agrégateur de classements par comptage de Borda."""

    def aggregate(self, rankings: Sequence[Ranking]) -> BordaResult:
        rankings = [list(r) for r in rankings if r]
        if not rankings:
            return BordaResult()
        points = borda_points(rankings)
        order = borda_order(rankings)
        return BordaResult(points=points, order=order, rankings_used=len(rankings))

    def from_scores(self, scores: Mapping[str, float]) -> BordaResult:
        """Borda sur un classement unique dérivé de scores décroissants."""
        ranking = sorted(scores, key=lambda c: -float(scores[c]))
        return self.aggregate([ranking])


__all__ = ["BordaConsensus", "BordaResult", "borda_points", "borda_order"]
