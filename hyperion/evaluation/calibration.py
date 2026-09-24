"""Métriques de calibration (module 1.13).

Trois mesures complémentaires du simple taux de présence dans le Top 5 :

  * **Log loss** — pénalise lourdement une confiance élevée attribuée à un
    cheval qui perd. C'est la mesure la plus exigeante : un système qui
    annonce 90 % de chances de victoire et qui a tort est sévèrement puni.
  * **Score de Brier** — moyenne des carrés des écarts entre probabilité
    annoncée et issue réelle. Plus stable que la log loss sur petits
    échantillons.
  * **ECE (Expected Calibration Error)** — compare, par bacile de confiance,
    la confiance moyenne annoncée au taux de réussite réel. Un ECE faible
    signifie que « 70 % annoncé » correspond vraiment à 7 succès sur 10.

Ces métriques ne sont pertinentes qu'une fois un historique suffisant
accumulé — bien au-delà des 8 courses d'un premier backtest. Elles sont donc
activées progressivement, jamais comme prérequis immédiat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

#: Nombre de bacs utilisés pour l'ECE.
N_BINS = 10
#: Garde-fou numérique pour le log de zéro.
EPSILON = 1e-15


def log_loss(winner: str, p_win: Mapping[str, float]) -> float:
    """Log loss binaire du cheval réellement gagnant.

    ``p_win`` doit contenir une probabilité de victoire pour chaque partant.
    """
    probability = min(max(float(p_win.get(winner, 0.0)), EPSILON), 1.0 - EPSILON)
    return -math.log(probability)


def brier_score(winner: str, p_win: Mapping[str, float]) -> float:
    """Score de Brier multi-classes : moyenne des carrés des écarts."""
    if not p_win:
        return 0.0
    total = 0.0
    for horse_id, probability in p_win.items():
        target = 1.0 if horse_id == winner else 0.0
        total += (float(probability) - target) ** 2
    return total / len(p_win)


def expected_calibration_error(
    predictions: Mapping[str, float],
    outcomes: Mapping[str, bool],
    n_bins: int = N_BINS,
) -> float:
    """ECE : distance entre confiance annoncée et réussite réelle, par bacile.

    ``predictions`` : cheval -> probabilité annoncée (0..1).
    ``outcomes`` : cheval -> le cheval a-t-il réellement gagné ?
    """
    if not predictions:
        return 0.0
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for horse_id, probability in predictions.items():
        clamped = min(max(float(probability), 0.0), 1.0)
        index = min(int(clamped * n_bins), n_bins - 1)
        bins[index].append((clamped, bool(outcomes.get(horse_id, False))))

    total = len(predictions)
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        mean_confidence = sum(c for c, _ in bucket) / len(bucket)
        accuracy = sum(1 for _, hit in bucket if hit) / len(bucket)
        ece += (len(bucket) / total) * abs(mean_confidence - accuracy)
    return ece


@dataclass
class SegmentKey:
    """Segment de fiabilité : discipline × intervalle de cote.

    Un site peut être fiable sur les favoris et faible sur les outsiders : la
    fiabilité n'a de sens que segmentée.
    """

    discipline: str
    odds_band: str

    @classmethod
    def of(cls, discipline: str, odds: float | None) -> SegmentKey:
        return cls(discipline=discipline or "inconnue", odds_band=odds_band(odds))

    def as_dict(self) -> dict[str, Any]:
        return {"discipline": self.discipline, "odds_band": self.odds_band}


def odds_band(odds: float | None) -> str:
    """Intervalle de cote normalisé."""
    if odds is None:
        return "inconnu"
    if odds < 3:
        return "<3/1"
    if odds < 6:
        return "3-6/1"
    if odds < 11:
        return "6-11/1"
    if odds < 21:
        return "11-21/1"
    return ">21/1"


@dataclass
class ReliabilityRecord:
    """Fiabilité cumulée d'une source sur un segment."""

    source: str
    segment: SegmentKey
    total: int = 0
    hits: int = 0
    log_loss_sum: float = 0.0
    brier_sum: float = 0.0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0

    @property
    def mean_log_loss(self) -> float | None:
        return self.log_loss_sum / self.total if self.total else None

    @property
    def mean_brier(self) -> float | None:
        return self.brier_sum / self.total if self.total else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "segment": self.segment.as_dict(),
            "total": self.total,
            "hits": self.hits,
            "hit_rate": round(self.hit_rate, 4),
            "mean_log_loss": (
                None if self.mean_log_loss is None else round(self.mean_log_loss, 6)
            ),
            "mean_brier": None if self.mean_brier is None else round(self.mean_brier, 6),
        }


class ReliabilityLedger:
    """Historique cumulé de fiabilité par source et par segment."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], ReliabilityRecord] = {}

    def record(
        self,
        source: str,
        segment: SegmentKey,
        hit: bool,
        log_loss_value: float | None = None,
        brier_value: float | None = None,
    ) -> ReliabilityRecord:
        key = (source, segment.discipline, segment.odds_band)
        entry = self._records.setdefault(
            key, ReliabilityRecord(source=source, segment=segment)
        )
        entry.total += 1
        entry.hits += 1 if hit else 0
        entry.log_loss_sum += log_loss_value or 0.0
        entry.brier_sum += brier_value or 0.0
        return entry

    def get(self, source: str, segment: SegmentKey) -> ReliabilityRecord | None:
        return self._records.get((source, segment.discipline, segment.odds_band))

    def reliability(self, source: str, segment: SegmentKey) -> float:
        """Fiabilité d'une source, avec repli sur le taux global de la source."""
        entry = self.get(source, segment)
        if entry is not None and entry.total >= 5:
            return entry.hit_rate
        return self.global_reliability(source)

    def global_reliability(self, source: str) -> float:
        totals = [
            r for key, r in self._records.items() if key[0] == source
        ]
        total = sum(r.total for r in totals)
        hits = sum(r.hits for r in totals)
        return hits / total if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            f"{key[0]}|{key[1]}|{key[2]}": r.as_dict()
            for key, r in sorted(self._records.items())
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReliabilityLedger:
        ledger = cls()
        for key, raw in data.items():
            source, discipline, band = key.split("|")
            segment = SegmentKey(discipline=discipline, odds_band=band)
            entry = ReliabilityRecord(source=source, segment=segment)
            entry.total = int(raw.get("total", 0))
            entry.hits = int(raw.get("hits", 0))
            entry.log_loss_sum = float(raw.get("mean_log_loss") or 0.0) * entry.total
            entry.brier_sum = float(raw.get("mean_brier") or 0.0) * entry.total
            ledger._records[(source, discipline, band)] = entry
        return ledger


__all__ = [
    "log_loss",
    "brier_score",
    "expected_calibration_error",
    "SegmentKey",
    "ReliabilityRecord",
    "ReliabilityLedger",
    "odds_band",
    "N_BINS",
]
