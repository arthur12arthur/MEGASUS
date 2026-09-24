"""Module 1.7 — ConsensusInterne (score de classement).

Détermine l'ordre le plus probable AU SEIN du groupe filtré et déjà noté par
BaseScorer. Trois méthodes sont combinées :

  1. MonteCarloEngine — 5 seeds fixes × 10 000 simulations (Plackett-Luce) ;
  2. BordaConsensus — agrégation des classements par comptage de Borda ;
  3. MetaFusion — fusion pairwise (Copeland) des classements produits.

Ce module produit le score de CLASSEMENT. Il est strictement séparé du score
de COMPÉTITIVITÉ du BaseScorer : c'est la correction principale apportée aux
versions précédentes, qui mélangeaient les deux et étaient peu précises sur
l'ordre exact.

Sortie : classement final + probabilité par position + indicateur de
stabilité inter-seeds (Top 3 stable sur au moins 4 seeds sur 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from hyperion.consensus.borda import BordaConsensus, BordaResult
from hyperion.consensus.metafusion import MetaFusion, MetaFusionResult
from hyperion.consensus.montecarlo import (
    MonteCarloEngine,
    MonteCarloResult,
    HorseProbabilities,
)
from hyperion.config import Settings


@dataclass
class ConsensusResult:
    """Sortie du ConsensusInterne."""

    ranking: list[str] = field(default_factory=list)
    probabilities: MonteCarloResult | None = None
    borda_seeds: BordaResult | None = None
    borda_scores: BordaResult | None = None
    fusion: MetaFusionResult | None = None
    stability: float = 0.0
    is_stable: bool = False
    notes: list[str] = field(default_factory=list)

    def rank_of(self, horse_id: str) -> int | None:
        try:
            return self.ranking.index(horse_id) + 1
        except ValueError:
            return None

    def probabilities_by_position(self) -> list[tuple[str, float]]:
        """(cheval, probabilité d'occuper ce rang) dans l'ordre du classement."""
        if not self.probabilities:
            return []
        out: list[tuple[str, float]] = []
        for position, horse_id in enumerate(self.ranking, start=1):
            prob = self.probabilities.probabilities.get(horse_id)
            chance = 0.0
            if prob is not None:
                chance = prob.position_distribution.get(position, 0.0)
            out.append((horse_id, chance))
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "ranking": list(self.ranking),
            "stability": round(self.stability, 4),
            "is_stable": self.is_stable,
            "montecarlo": self.probabilities.as_dict() if self.probabilities else None,
            "borda_seeds": self.borda_seeds.as_dict() if self.borda_seeds else None,
            "borda_scores": self.borda_scores.as_dict() if self.borda_scores else None,
            "fusion": self.fusion.as_dict() if self.fusion else None,
            "notes": list(self.notes),
        }


class ConsensusInterne:
    """Combine Monte Carlo, Borda et MetaFusion en un classement unique."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.engine = MonteCarloEngine(
            seeds=self.settings.mc_seeds,
            simulations=self.settings.mc_simulations,
            concentration=self.settings.mc_concentration,
            top_n=self.settings.mc_top_n_stable,
        )
        self.borda = BordaConsensus()
        self.fusion = MetaFusion()

    def compute(
        self,
        competitiveness: Mapping[str, float],
        names: Mapping[str, str] | None = None,
    ) -> ConsensusResult:
        if not competitiveness:
            return ConsensusResult(notes=["aucun score de compétitivité à classer"])

        monte = self.engine.run(competitiveness, names=names)
        mc_ranking = [p.horse_id for p in monte.ordered()]

        # Borda sur les classements de chacune des 5 seeds.
        seed_rankings = [list(top) for top in monte.per_seed_top.values()]
        borda_seeds = self.borda.aggregate(seed_rankings)

        # Borda sur le classement de compétitivité du BaseScorer.
        score_ranking = sorted(competitiveness, key=lambda c: -float(competitiveness[c]))
        borda_scores = self.borda.aggregate([score_ranking])

        fusion = self.fusion.fuse([mc_ranking, borda_seeds.order, borda_scores.order])

        notes: list[str] = []
        if not monte.is_stable:
            notes.append(
                f"Top {self.settings.mc_top_n_stable} stable sur seulement "
                f"{monte.stable_seeds}/{len(monte.seeds)} seeds — ordre à manier "
                "avec prudence"
            )
        return ConsensusResult(
            ranking=fusion.order,
            probabilities=monte,
            borda_seeds=borda_seeds,
            borda_scores=borda_scores,
            fusion=fusion,
            stability=monte.stability,
            is_stable=monte.is_stable,
            notes=notes,
        )


def summarise(result: ConsensusResult, names: Mapping[str, str] | None = None) -> str:
    """Résumé lisible du classement interne."""
    names = names or {}
    lines = [
        "ConsensusInterne — classement (MonteCarlo + Borda + MetaFusion)",
        f"  Stabilité inter-seeds : {result.stability * 100:.0f}% "
        f"({'stable' if result.is_stable else 'instable'})",
    ]
    for position, (horse_id, chance) in enumerate(result.probabilities_by_position(), start=1):
        prob = result.probabilities.probabilities.get(horse_id) if result.probabilities else None
        name = names.get(horse_id, horse_id)
        if prob is None:
            lines.append(f"  {position}. {name}")
        else:
            lines.append(
                f"  {position}. {name} — P(rang {position}) {chance * 100:.0f}% · "
                f"P(gagne) {prob.p_win * 100:.0f}% · position moyenne "
                f"{prob.expected_position:.2f}"
            )
    for note in result.notes:
        lines.append(f"  ⚠ {note}")
    return "\n".join(lines)


__all__ = [
    "ConsensusInterne",
    "ConsensusResult",
    "MonteCarloEngine",
    "MonteCarloResult",
    "HorseProbabilities",
    "BordaConsensus",
    "BordaResult",
    "MetaFusion",
    "MetaFusionResult",
]
