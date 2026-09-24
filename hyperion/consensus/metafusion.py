"""Module 1.7c — MetaFusion.

Fusionne plusieurs classements par comparaison PAIRWISE (méthode de Copeland
généralisée) : pour chaque couple de candidats (A, B), on compte dans combien
de classements A est préféré à B. Le score final d'un candidat est son nombre
de victoires moins son nombre de défaites, normalisé.

Pourquoi pairwise plutôt qu'une simple moyenne de rangs : une moyenne de
rangs peut produire un vainqueur qu'AUCUN classement d'entrée ne place
premier. La fusion pairwise respecte la structure de préférence des entrées —
le gagnant bat les autres dans la majorité des confrontations directes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

Ranking = Sequence[str]


def _positions(ranking: Ranking) -> dict[str, int]:
    return {candidate: index for index, candidate in enumerate(ranking)}


@dataclass
class MetaFusionResult:
    """Résultat de la fusion pairwise."""

    order: list[str] = field(default_factory=list)
    copeland: dict[str, float] = field(default_factory=dict)
    normalized: dict[str, float] = field(default_factory=dict)
    mean_rank: dict[str, float] = field(default_factory=dict)
    rankings_used: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "order": list(self.order),
            "copeland": {k: round(v, 4) for k, v in self.copeland.items()},
            "normalized": {k: round(v, 4) for k, v in self.normalized.items()},
            "mean_rank": {k: round(v, 4) for k, v in self.mean_rank.items()},
            "rankings_used": self.rankings_used,
        }


class MetaFusion:
    """Fusion de classements par comparaison pairwise."""

    def fuse(self, rankings: Sequence[Ranking]) -> MetaFusionResult:
        rankings = [list(r) for r in rankings if r]
        if not rankings:
            return MetaFusionResult()

        candidates: list[str] = []
        for ranking in rankings:
            for candidate in ranking:
                if candidate not in candidates:
                    candidates.append(candidate)

        positions = [_positions(r) for r in rankings]
        wins: dict[str, float] = {c: 0.0 for c in candidates}
        losses: dict[str, float] = {c: 0.0 for c in candidates}
        mean_rank: dict[str, float] = {}

        for candidate in candidates:
            ranks = [p[candidate] for p in positions if candidate in p]
            mean_rank[candidate] = sum(ranks) / len(ranks) if ranks else float("inf")

        for i, left in enumerate(candidates):
            for right in candidates[i + 1 :]:
                left_wins = 0
                right_wins = 0
                for pos in positions:
                    if left in pos and right in pos:
                        if pos[left] < pos[right]:
                            left_wins += 1
                        elif pos[right] < pos[left]:
                            right_wins += 1
                if left_wins > right_wins:
                    wins[left] += 1
                    losses[right] += 1
                elif right_wins > left_wins:
                    wins[right] += 1
                    losses[left] += 1
                else:
                    # Confrontation indécise : demi-point chacun.
                    wins[left] += 0.5
                    wins[right] += 0.5

        copeland = {c: wins[c] - losses[c] for c in candidates}
        max_score = max(1.0, float(len(candidates) - 1))
        normalized = {c: 0.5 + 0.5 * (copeland[c] / max_score) for c in candidates}

        order = sorted(
            candidates,
            key=lambda c: (
                -copeland[c],
                mean_rank[c] if mean_rank[c] != float("inf") else 1e9,
                str(c),
            ),
        )
        return MetaFusionResult(
            order=order,
            copeland=copeland,
            normalized=normalized,
            mean_rank=mean_rank,
            rankings_used=len(rankings),
        )


__all__ = ["MetaFusion", "MetaFusionResult"]
