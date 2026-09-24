"""Module 1.8 — HADES (détection de valeur masquée).

Détecte les chevaux dont les chances réelles semblent masquées ou
sous-évaluées par le marché.

AVERTISSEMENT DE CALIBRATION : ce module est historiquement mal calibré
(faux positifs fréquents). Il a aussi été combiné à tort avec le
risk-management, ce qui provoquait parfois une suppression totale des
prédictions. Depuis, il est strictement un SIGNAL ANNEXE :

  * il ne retire jamais un cheval du classement ;
  * il ne modifie jamais un score ;
  * il ne bloque jamais la livraison d'un rapport.

Les seuils sont volontairement conservateurs (precision avant recall) et
chaque signal est accompagné de la justification qui l'a déclenché.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from hyperion.analysis.market import MarketWatchResult
from hyperion.config import Settings
from hyperion.models import Race

#: Raccourcissement de cote déclenchant le signal (relatif).
DROP_THRESHOLD = -0.20
#: Écart minimal de rang entre le marché et l'analyse interne.
RANK_GAP_THRESHOLD = 3
#: Nombre de chevaux du Top considérés comme « mis en avant » par le marché.
MARKET_SPOTLIGHT = 4


@dataclass
class HadesSignal:
    """Signal qualitatif pour un cheval — jamais un score chiffré."""

    horse_id: str
    name: str
    signals: list[str] = field(default_factory=list)
    severity: str = "faible"  # "faible" | "moyenne" | "forte"

    def as_dict(self) -> dict[str, Any]:
        return {
            "horse_id": self.horse_id,
            "name": self.name,
            "signals": list(self.signals),
            "severity": self.severity,
        }


@dataclass
class HadesResult:
    """Sortie de HADES."""

    signals: list[HadesSignal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> list[str]:
        return [s.horse_id for s in self.signals if s.signals]

    def signal(self, horse_id: str) -> HadesSignal | None:
        for sig in self.signals:
            if sig.horse_id == horse_id:
                return sig
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "signals": [s.as_dict() for s in self.signals],
            "flagged": self.flagged,
            "notes": list(self.notes),
        }


def _comment_mentions_improvement(comment: str | None) -> bool:
    if not comment:
        return False
    text = comment.lower()
    markers = (
        "gagnant",
        "vainqueur",
        "en progrs",
        "en progrès",
        "confirm",
        "réuss",
        "reuss",
        "vient de",
        "adapté",
        "adapte",
        "déferré",
        "deferre",
        "première",
        "premiere",
        "retour",
        "vient d'affronter",
        "vient d'affronter",
    )
    return any(marker in text for marker in markers)


def _market_rank(race: Race) -> dict[str, int]:
    order = race.order_by_odds()
    return {horse.horse_id: index + 1 for index, horse in enumerate(order)}


class HADES:
    """Détecteur de valeur masquée — signal annexe, jamais bloquant."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def analyse(
        self,
        race: Race,
        market: MarketWatchResult | None = None,
        internal_ranking: Sequence[str] | None = None,
        competitiveness: Mapping[str, float] | None = None,
    ) -> HadesResult:
        result = HadesResult()
        result.notes.append(
            "HADES est un signal annexe : il ne modifie aucun score et ne retire "
            "aucun cheval du classement."
        )
        market_ranks = _market_rank(race)

        for horse in race.runners:
            signals: list[str] = []

            if market is not None:
                sig = market.signal(horse.horse_id)
                if sig is not None and sig.delta is not None and sig.delta <= DROP_THRESHOLD:
                    justified = _comment_mentions_improvement(horse.comment)
                    if not justified:
                        signals.append(
                            f"cote fondue de {sig.delta * 100:+.0f}% depuis le PDF, "
                            "sans justification visible dans le journal"
                        )
                if sig is not None and sig.non_runner_alert:
                    signals.append("non-partant signalé tardivement")

            if internal_ranking:
                try:
                    internal_rank = list(internal_ranking).index(horse.horse_id) + 1
                except ValueError:
                    internal_rank = None
                market_rank = market_ranks.get(horse.horse_id)
                if internal_rank and market_rank:
                    gap = market_rank - internal_rank
                    if gap >= RANK_GAP_THRESHOLD:
                        signals.append(
                            f"l'analyse interne le place {gap} rangs plus haut que "
                            f"le marché (interne {internal_rank} vs marché {market_rank})"
                        )

            if horse.comment is None and market_ranks.get(horse.horse_id, 99) > MARKET_SPOTLIGHT:
                if competitiveness is not None:
                    ranked = sorted(
                        competitiveness, key=lambda c: -float(competitiveness[c])
                    )
                    if ranked and horse.horse_id in ranked[: max(3, len(ranked) // 3)]:
                        signals.append(
                            "aucune mise en avant dans le journal malgré des "
                            "critères internes favorables"
                        )

            severity = "faible"
            if len(signals) >= 3:
                severity = "forte"
            elif len(signals) >= 2:
                severity = "moyenne"
            result.signals.append(
                HadesSignal(
                    horse_id=horse.horse_id,
                    name=horse.name,
                    signals=signals,
                    severity=severity if signals else "faible",
                )
            )
        return result


def summarise(result: HadesResult) -> str:
    """Résumé lisible de HADES."""
    lines = ["HADES — valeur masquée (signal annexe, non bloquant)"]
    flagged = [s for s in result.signals if s.signals]
    if not flagged:
        lines.append("  Aucun signal retenu.")
    for sig in flagged:
        lines.append(f"  ◆ {sig.name} — sévérité {sig.severity}")
        lines.extend(f"      · {text}" for text in sig.signals)
    return "\n".join(lines)


__all__ = ["HADES", "HadesResult", "HadesSignal"]
