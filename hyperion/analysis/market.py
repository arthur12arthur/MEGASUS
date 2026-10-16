"""Module 1.4 — MarketWatch (repositionné AVANT le filtrage).

Le PDF officiel est publié 2 à 3 jours avant la course : ses cotes peuvent
donc être obsolètes au moment de l'analyse. Ce module compare la cote
indicative du PDF à la cote la plus récente disponible et transmet le delta
au DataFilter (1.5) et à HADES (1.8).

Dans les versions précédentes du système, ce module s'exécutait en FIN de
pipeline : son signal n'était donc jamais utilisé par le filtrage. Il est
désormais en amont — c'est un correctif de positionnement, pas une
fonctionnalité nouvelle.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from hyperion.config import Settings
from hyperion.models import Race

#: Au-delà de ce raccourcissement relatif, on considère la cote comme « fondue ».
SHORTENING_THRESHOLD = -0.15
#: Au-delà de cet allongement relatif, on considère la cote comme « dérivante ».
DRIFTING_THRESHOLD = 0.20
#: Une dérive n'est significative que si elle déplace aussi la probabilité
#: implicite d'au moins 2 points (1/cote). Sans ce garde-fou, 30 → 36 (+20 %)
#: pénaliserait un outsider pour un simple bruit, alors que 4 → 5 (+25 %) est
#: un vrai mouvement du marché.
MIN_IMPLIED_SHIFT = 0.02


@dataclass
class MarketSignal:
    """Signal de marché pour un cheval."""

    horse_id: str
    name: str
    odds_pdf: float | None
    odds_latest: float | None
    delta: float | None            # relatif, négatif = raccourcissement
    direction: str                 # "raccourci" | "derive" | "stable" | "inconnu"
    non_runner_alert: bool = False
    note: str = ""
    number: int | None = None

    @property
    def implied_shift(self) -> float | None:
        """Variation de probabilité implicite (positive = le marché y croit moins)."""
        if not self.odds_pdf or not self.odds_latest:
            return None
        return 1.0 / self.odds_pdf - 1.0 / self.odds_latest

    @property
    def display_name(self) -> str:
        return f"n°{self.number} · {self.name}" if self.number is not None else self.name

    @property
    def shortened(self) -> bool:
        return self.delta is not None and self.delta <= SHORTENING_THRESHOLD

    @property
    def drifting(self) -> bool:
        if self.delta is None or self.delta < DRIFTING_THRESHOLD:
            return False
        shift = self.implied_shift
        return shift is None or shift >= MIN_IMPLIED_SHIFT

    def as_dict(self) -> dict[str, Any]:
        return {
            "horse_id": self.horse_id,
            "name": self.name,
            "number": self.number,
            "odds_pdf": self.odds_pdf,
            "odds_latest": self.odds_latest,
            "delta": self.delta,
            "implied_shift": self.implied_shift,
            "direction": self.direction,
            "non_runner_alert": self.non_runner_alert,
            "note": self.note,
        }


@dataclass
class MarketWatchResult:
    """Sortie du module 1.4, consommée par le DataFilter et HADES."""

    as_of: dt.datetime
    signals: list[MarketSignal] = field(default_factory=list)
    source: str = "cote la plus récente disponible"
    warnings: list[str] = field(default_factory=list)

    def signal(self, horse_id: str) -> MarketSignal | None:
        for sig in self.signals:
            if sig.horse_id == horse_id:
                return sig
        return None

    @property
    def late_non_runners(self) -> list[str]:
        return [s.horse_id for s in self.signals if s.non_runner_alert]

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "source": self.source,
            "signals": [s.as_dict() for s in self.signals],
            "late_non_runners": self.late_non_runners,
            "warnings": list(self.warnings),
        }


def _direction(
    delta: float | None,
    odds_pdf: float | None = None,
    odds_latest: float | None = None,
) -> str:
    if delta is None:
        return "inconnu"
    if delta <= SHORTENING_THRESHOLD:
        return "raccourci"
    if delta >= DRIFTING_THRESHOLD:
        if odds_pdf and odds_latest:
            if 1.0 / odds_pdf - 1.0 / odds_latest < MIN_IMPLIED_SHIFT:
                return "stable"  # variation relative forte mais négligeable en probabilité
        return "derive"
    return "stable"


class MarketWatch:
    """Compare la cote du PDF à la cote courante, avant tout filtrage."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def observe(
        self,
        race: Race,
        latest_odds: Mapping[str, float] | None = None,
        as_of: dt.datetime | None = None,
        source: str = "panel externe / scraping",
        non_runners: Iterable[str] = (),
    ) -> MarketWatchResult:
        latest_odds = dict(latest_odds or {})
        non_runners = {str(hid) for hid in non_runners}
        when = as_of or dt.datetime.now(dt.timezone.utc)

        signals: list[MarketSignal] = []
        warnings: list[str] = []
        for horse in race.horses:
            fresh = latest_odds.get(horse.horse_id)
            if fresh is None:
                fresh = horse.odds_latest
            if fresh is None:
                warnings.append(
                    f"{horse.name} ({horse.horse_id}) : aucune cote récente disponible"
                )
            delta = None
            if horse.odds_pdf and fresh:
                delta = (fresh - horse.odds_pdf) / horse.odds_pdf
            alert = horse.horse_id in non_runners or horse.non_runner
            note = ""
            if alert:
                note = "non-partant signalé après publication du PDF"
            elif delta is not None and delta <= SHORTENING_THRESHOLD:
                note = "cote fondue sans justification visible dans le journal"
            elif _direction(delta, horse.odds_pdf, fresh) == "derive":
                note = "cote en dérive à la hausse"
            signals.append(
                MarketSignal(
                    horse_id=horse.horse_id,
                    name=horse.name,
                    number=horse.number,
                    odds_pdf=horse.odds_pdf,
                    odds_latest=fresh,
                    delta=delta,
                    direction=_direction(delta, horse.odds_pdf, fresh),
                    non_runner_alert=alert,
                    note=note,
                )
            )
        return MarketWatchResult(
            as_of=when, signals=signals, source=source, warnings=warnings
        )

    def apply_fresh_odds(self, race: Race, result: MarketWatchResult) -> Race:
        """Réinjecte les cotes fraîches dans la course (effet de bord assumé)."""
        for signal in result.signals:
            horse = race.by_id(signal.horse_id)
            if horse is not None and signal.odds_latest is not None:
                horse.odds_latest = signal.odds_latest
        return race


def summarise(result: MarketWatchResult) -> str:
    """Résumé lisible du module, pour le rapport de livraison."""
    lines = [
        f"MarketWatch — relevé à {result.as_of.isoformat()} (source : {result.source})",
    ]
    for sig in result.signals:
        if sig.non_runner_alert:
            lines.append(f"  ⚠ {sig.display_name} : non-partant tardif")
        elif sig.delta is None:
            lines.append(f"  · {sig.display_name} : cote non comparable")
        else:
            arrow = "▼" if sig.delta < 0 else ("▲" if sig.delta > 0 else "=")
            lines.append(
                f"  {arrow} {sig.display_name} : {sig.odds_pdf} → {sig.odds_latest} "
                f"({sig.delta * 100:+.0f}%) {sig.direction}"
            )
    return "\n".join(lines)


def sequence_to_dict(signals: Sequence[MarketSignal]) -> list[dict[str, Any]]:
    return [s.as_dict() for s in signals]
