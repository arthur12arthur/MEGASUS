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

from hyperion.analysis.market import MIN_IMPLIED_SHIFT, MarketWatchResult
from hyperion.config import Settings
from hyperion.models import Race

#: Raccourcissement de cote déclenchant le signal (relatif).
DROP_THRESHOLD = -0.20
#: Écart minimal de rang entre le marché et l'analyse interne.
RANK_GAP_THRESHOLD = 3
#: Nombre de chevaux du Top considérés comme « mis en avant » par le marché.
MARKET_SPOTLIGHT = 4
#: Un écart de rang n'a de sens que pour un cheval que l'analyse interne place
#: dans son Top N (sinon on signale la moitié du lot, ce qui n'est pas de la valeur).
INTERNAL_TOP = 4
#: Plafond de chevaux signalés par course : HADES doit rester sélectif.
MAX_FLAGGED = 3
_SEVERITY_ORDER = {"forte": 3, "moyenne": 2, "faible": 1}


@dataclass
class HadesSignal:
    """Signal qualitatif pour un cheval — jamais un score chiffré."""

    horse_id: str
    name: str
    number: int | None = None
    signals: list[str] = field(default_factory=list)
    severity: str = "faible"  # "faible" | "moyenne" | "forte"

    @property
    def display_name(self) -> str:
        return f"n°{self.number} · {self.name}" if self.number is not None else self.name

    def as_dict(self) -> dict[str, Any]:
        return {
            "horse_id": self.horse_id,
            "name": self.name,
            "number": self.number,
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
        gaps: dict[str, int] = {}
        late_non_runners = set(market.late_non_runners) if market is not None else set()

        for horse in race.runners:
            # Un non-partant tardif n'est jamais une « valeur » : il est géré en amont.
            if horse.horse_id in late_non_runners:
                continue
            signals: list[str] = []
            market_signal = False
            gap_signal = False

            # Famille 1 — information de marché (cote qui fond réellement).
            if market is not None:
                sig = market.signal(horse.horse_id)
                shift = sig.implied_shift if sig is not None else None
                if (
                    sig is not None
                    and sig.delta is not None
                    and sig.delta <= DROP_THRESHOLD
                    and (shift is None or shift <= -MIN_IMPLIED_SHIFT)
                ):
                    if not _comment_mentions_improvement(horse.comment):
                        market_signal = True
                        signals.append(
                            f"cote fondue de {sig.delta * 100:+.0f}% depuis le PDF, "
                            "sans justification visible dans le journal"
                        )

            # Famille 2 — écart entre l'analyse interne et le marché.
            if internal_ranking:
                try:
                    internal_rank = list(internal_ranking).index(horse.horse_id) + 1
                except ValueError:
                    internal_rank = None
                market_rank = market_ranks.get(horse.horse_id)
                if internal_rank and market_rank and internal_rank <= INTERNAL_TOP:
                    gap = market_rank - internal_rank
                    if gap >= RANK_GAP_THRESHOLD:
                        gap_signal = True
                        gaps[horse.horse_id] = gap
                        signals.append(
                            f"l'analyse interne le place {gap} rangs plus haut que "
                            f"le marché (interne {internal_rank} vs marché {market_rank})"
                        )

            # Corroboration — jamais un signal à lui seul (il redit surtout l'écart).
            journal_signal = False
            if (
                gap_signal
                and horse.comment is None
                and market_ranks.get(horse.horse_id, 99) > MARKET_SPOTLIGHT
                and competitiveness is not None
            ):
                ranked = sorted(competitiveness, key=lambda c: -float(competitiveness[c]))
                if ranked and horse.horse_id in ranked[: max(3, len(ranked) // 3)]:
                    journal_signal = True
                    signals.append(
                        "aucune mise en avant dans le journal malgré des "
                        "critères internes favorables"
                    )

            # Sévérité = nombre de sources INDÉPENDANTES, pas de lignes de texte.
            severity = "faible"
            if market_signal and gap_signal:
                severity = "forte" if journal_signal else "moyenne"
            result.signals.append(
                HadesSignal(
                    horse_id=horse.horse_id,
                    name=horse.name,
                    number=horse.number,
                    signals=signals,
                    severity=severity if signals else "faible",
                )
            )

        # Sélectivité : on ne garde que les MAX_FLAGGED signaux les plus solides.
        flagged = [s for s in result.signals if s.signals]
        if len(flagged) > MAX_FLAGGED:
            flagged.sort(
                key=lambda s: (_SEVERITY_ORDER[s.severity], gaps.get(s.horse_id, 0)),
                reverse=True,
            )
            dropped = flagged[MAX_FLAGGED:]
            for sig in dropped:
                sig.signals = []
                sig.severity = "faible"
            result.notes.append(
                f"{len(dropped)} signal(s) moins solide(s) écarté(s) : HADES reste "
                f"limité à {MAX_FLAGGED} chevaux par course."
            )
        if market is None or not any(
            s.delta is not None for s in market.signals
        ):
            result.notes.append(
                "Aucun mouvement de cote exploitable : sévérité plafonnée à « faible »."
            )
        return result


def summarise(result: HadesResult) -> str:
    """Résumé lisible de HADES."""
    lines = ["HADES — valeur masquée (signal annexe, non bloquant)"]
    flagged = [s for s in result.signals if s.signals]
    if not flagged:
        lines.append("  Aucun signal retenu.")
    for sig in flagged:
        lines.append(f"  ◆ {sig.display_name} — sévérité {sig.severity}")
        lines.extend(f"      · {text}" for text in sig.signals)
    return "\n".join(lines)


__all__ = ["HADES", "HadesResult", "HadesSignal"]
