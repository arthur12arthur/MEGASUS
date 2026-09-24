"""Module 1.5 — DataFilter (portillon éliminatoire).

Réduit le champ de partants au groupe des chevaux réellement compétitifs,
comme portillon binaire (retenu / écarté) — SANS les ordonner et SANS
produire de score propre.

Correctif appliqué par rapport aux versions précédentes : l'ancienne version
produisait un « score de sélection normalisé » en plus du score de
compétitivité du BaseScorer, ce qui doublait la notation et créait deux
échelles concurrentes dans le même rapport. Ce score est supprimé : les
points de risque restent un diagnostic interne, jamais une note affichée.

Méthode :
  * score de risque interne par points (gains, forme, cote, absence, delta
    MarketWatch) ;
  * seuil d'élimination figé à 3 points ;
  * maintien forcé des N favoris aux cotes les plus basses ;
  * réintégration si le groupe descend sous la taille minimale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from hyperion.analysis.market import MarketWatchResult
from hyperion.config import Settings
from hyperion.models import Horse, Race

#: Absence (en semaines) au-delà de laquelle un cheval prend un point.
ABSENCE_WEEKS_LIMIT = 12
#: Cote au-delà de laquelle un cheval est considéré « très outsider ».
OUTSIDER_ODDS = 30.0
#: Nombre de courses récentes examinées pour la forme.
FORM_LOOKBACK = 5


@dataclass
class Exclusion:
    """Cheval écarté, avec son motif (traçabilité exigée)."""

    horse_id: str
    name: str
    reason: str
    risk_points: int


@dataclass
class FilterResult:
    """Sortie du portillon éliminatoire."""

    selected: list[str] = field(default_factory=list)
    excluded: list[Exclusion] = field(default_factory=list)
    #: Diagnostic interne — n'est JAMAIS présenté comme un score de cheval.
    risk_diagnostics: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def selected_horses(self) -> list[str]:
        return list(self.selected)

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected": list(self.selected),
            "excluded": [
                {
                    "horse_id": e.horse_id,
                    "name": e.name,
                    "reason": e.reason,
                    "risk_points": e.risk_points,
                }
                for e in self.excluded
            ],
            "notes": list(self.notes),
        }


def _form_is_poor(horse: Horse) -> bool:
    places = horse.recent_places[:FORM_LOOKBACK]
    if not places:
        return False  # donnée absente = non déterminé, pas un motif d'exclusion
    return all(p == 0 or p > 3 for p in places)


def _gains_is_low(horse: Horse, field_median: float | None) -> bool:
    if horse.gains is None or field_median is None:
        return False
    return horse.gains < field_median * 0.25


def _absence_is_long(horse: Horse) -> bool:
    runs = horse.history_stats.get("weeks_since_last_run")
    if runs is None:
        return False
    return float(runs) > ABSENCE_WEEKS_LIMIT


def risk_points(
    horse: Horse,
    field_median_gains: float | None,
    market: MarketWatchResult | None,
) -> tuple[int, list[str]]:
    """Calcule le score de risque interne d'un cheval (diagnostic seulement)."""
    points = 0
    reasons: list[str] = []

    if _gains_is_low(horse, field_median_gains):
        points += 1
        reasons.append("gains très inférieurs à la médiane du champ")
    if _form_is_poor(horse):
        points += 1
        reasons.append(f"aucune place dans les {FORM_LOOKBACK} dernières sorties")
    if horse.odds is not None and horse.odds > OUTSIDER_ODDS:
        points += 1
        reasons.append(f"cote > {OUTSIDER_ODDS:.0f}/1")
    if _absence_is_long(horse):
        points += 1
        reasons.append(f"absence > {ABSENCE_WEEKS_LIMIT} semaines")

    if market is not None:
        signal = market.signal(horse.horse_id)
        if signal is not None and signal.drifting:
            points += 1
            reasons.append("cote en dérive depuis la publication du PDF")
        if signal is not None and signal.non_runner_alert:
            points += 99
            reasons.append("non-partant signalé par MarketWatch")
    return points, reasons


class DataFilter:
    """Portillon éliminatoire binaire."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def filter(
        self,
        race: Race,
        market: MarketWatchResult | None = None,
    ) -> FilterResult:
        runners = race.runners
        if not runners:
            return FilterResult(notes=["aucun partant au départ"])

        gains = sorted(h.gains for h in runners if h.gains is not None)
        median = _median(gains)

        result = FilterResult()
        candidates: list[Horse] = []

        for horse in runners:
            points, reasons = risk_points(horse, median, market)
            result.risk_diagnostics[horse.horse_id] = points
            result.reasons[horse.horse_id] = reasons
            if points >= 99:
                result.excluded.append(
                    Exclusion(horse.horse_id, horse.name, "non-partant tardif", points)
                )
                continue
            if points >= self.settings.filter_threshold_points:
                result.excluded.append(
                    Exclusion(
                        horse.horse_id,
                        horse.name,
                        f"score de risque {points} ≥ seuil {self.settings.filter_threshold_points}",
                        points,
                    )
                )
                continue
            candidates.append(horse)

        # Maintien forcé des favoris aux cotes les plus basses.
        favourites = race.order_by_odds()[: self.settings.filter_forced_favourites]
        kept_ids = {h.horse_id for h in candidates}
        for horse in favourites:
            if horse.horse_id not in kept_ids:
                candidates.append(horse)
                result.notes.append(
                    f"{horse.name} réintégré : favori aux cotes les plus basses "
                    "(maintien forcé)"
                )
                kept_ids.add(horse.horse_id)

        # Réintégration si le groupe descend sous la taille minimale.
        if len(candidates) < self.settings.filter_min_group:
            remaining = sorted(
                (h for h in runners if h.horse_id not in kept_ids),
                key=lambda h: result.risk_diagnostics.get(h.horse_id, 0),
            )
            for horse in remaining:
                if len(candidates) >= self.settings.filter_min_group:
                    break
                candidates.append(horse)
                kept_ids.add(horse.horse_id)
                result.notes.append(
                    f"{horse.name} réintégré : groupe sous le minimum de "
                    f"{self.settings.filter_min_group} chevaux"
                )

        # Tri stable par numéro de corde pour la lisibilité du rapport.
        candidates.sort(key=lambda h: (h.number is None, h.number or 0))
        result.selected = [h.horse_id for h in candidates]
        return result


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2)


def summarise(result: FilterResult, race: Race) -> str:
    """Résumé lisible — aucun score n'est attaché aux chevaux retenus."""
    lines = ["DataFilter — portillon éliminatoire", f"  Retenus : {len(result.selected)}"]
    for hid in result.selected:
        horse = race.by_id(hid)
        if horse:
            lines.append(f"    ✓ {horse.name}")
    lines.append(f"  Écartés : {len(result.excluded)}")
    for exc in result.excluded:
        lines.append(f"    ✗ {exc.name} — {exc.reason}")
    if result.notes:
        lines.append("  Notes :")
        lines.extend(f"    · {note}" for note in result.notes)
    return "\n".join(lines)
