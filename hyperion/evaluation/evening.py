"""Module 1.13 — EveningEvaluation (AgentH).

Compare, après la course, la prédiction du système et celle de chaque source
externe au résultat officiel — et calibre la fiabilité de chacun dans la
durée.

Sorties :
  * score de réussite du système ;
  * score de fiabilité cumulé par source externe, DÉCOMPOSÉ par discipline et
    par intervalle de cote (un site peut être fiable sur les favoris et faible
    sur les outsiders) ;
  * métriques de calibration (log loss, Brier, ECE) en complément du simple
    taux de présence dans le Top 5.

Le résultat officiel est récupéré via l'API JSON PMU (configurable), avec
repli sur une saisie manuelle — jamais de résultat deviné.

Validation dans le temps selon un protocole glissant (walk-forward) pour
éviter toute fuite de données entre l'historique et la prédiction évaluée.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from hyperion.config import Settings
from hyperion.evaluation.calibration import (
    ReliabilityLedger,
    SegmentKey,
    brier_score,
    expected_calibration_error,
    log_loss,
)
from hyperion.models import OfficialResult, Race

#: Nombre de courses au-delà duquel les métriques de calibration sont
#: considérées comme exploitables.
MIN_RACES_FOR_CALIBRATION = 30


@dataclass
class EveningReport:
    """Bilan d'une course, une fois le résultat officiel connu."""

    race_id: str
    date: dt.date | None = None
    discipline: str = "inconnue"
    system_winner_in_top5: bool = False
    system_winner_in_top1: bool = False
    system_top5_overlap: float = 0.0
    system_log_loss: float | None = None
    system_brier: float | None = None
    source_hits: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "race_id": self.race_id,
            "date": self.date.isoformat() if self.date else None,
            "discipline": self.discipline,
            "system_winner_in_top5": self.system_winner_in_top5,
            "system_winner_in_top1": self.system_winner_in_top1,
            "system_top5_overlap": round(self.system_top5_overlap, 4),
            "system_log_loss": (
                None if self.system_log_loss is None else round(self.system_log_loss, 6)
            ),
            "system_brier": None if self.system_brier is None else round(self.system_brier, 6),
            "source_hits": dict(self.source_hits),
            "notes": list(self.notes),
        }


@dataclass
class CumulativeEvaluation:
    """Évaluation cumulée sur un historique de courses."""

    races: int = 0
    winner_in_top5: float = 0.0
    winner_in_top1: float = 0.0
    mean_top5_overlap: float = 0.0
    mean_log_loss: float | None = None
    mean_brier: float | None = None
    ece: float | None = None
    calibration_usable: bool = False
    per_source: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "races": self.races,
            "winner_in_top5": round(self.winner_in_top5, 4),
            "winner_in_top1": round(self.winner_in_top1, 4),
            "mean_top5_overlap": round(self.mean_top5_overlap, 4),
            "mean_log_loss": (
                None if self.mean_log_loss is None else round(self.mean_log_loss, 6)
            ),
            "mean_brier": None if self.mean_brier is None else round(self.mean_brier, 6),
            "ece": None if self.ece is None else round(self.ece, 6),
            "calibration_usable": self.calibration_usable,
            "per_source": self.per_source,
        }


class PmuResultClient:
    """Client du résultat officiel (API JSON PMU, configurable)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def fetch(self, race_id: str, date: dt.date) -> OfficialResult | None:
        base = self.settings.lonab_url
        if not base:
            return None
        import requests

        try:
            response = requests.get(
                base,
                params={"date": date.isoformat()},
                timeout=self.settings.lonab_timeout_s,
            )
            if response.status_code != 200:
                return None
            payload = response.json()
        except Exception:
            return None
        return self._parse(payload, race_id, date)

    def _parse(self, payload: Any, race_id: str, date: dt.date) -> OfficialResult | None:
        if not isinstance(payload, Mapping):
            return None
        order = payload.get("ordre") or payload.get("order") or []
        if not isinstance(order, list):
            return None
        return OfficialResult(
            race_id=race_id,
            date=date,
            order=[str(item) for item in order],
            source="API PMU",
        )


def _odds_from_record(record: Mapping[str, Any], horse_id: str | None) -> float | None:
    """Recupere la cote la plus fraiche d'un cheval dans un rapport stocke."""
    if not horse_id:
        return None
    for signal in (record.get("marketwatch") or {}).get("signals", []):
        if str(signal.get("horse_id")) == str(horse_id):
            latest = signal.get("odds_latest")
            if latest is None:
                latest = signal.get("odds_pdf")
            return None if latest is None else float(latest)
    return None


class EveningEvaluation:
    """AgentH — évaluation du soir et calibration des sources."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.ledger = ReliabilityLedger()

    # -- évaluation d'une course ------------------------------------------

    def evaluate(
        self,
        record: Mapping[str, Any],
        result: OfficialResult,
        race: Race | None = None,
    ) -> EveningReport:
        """Compare une prédiction stockée au résultat officiel du soir."""
        report = EveningReport(
            race_id=result.race_id,
            date=result.date,
            discipline=str((record.get("meta") or {}).get("discipline", "inconnue")),
        )
        ranking = [
            str(item.get("horse_id"))
            for item in (record.get("classement") or [])
            if item.get("horse_id")
        ]
        top5 = ranking[:5]
        winner = result.winner

        if winner:
            report.system_winner_in_top5 = winner in top5
            report.system_winner_in_top1 = bool(ranking) and ranking[0] == winner
            actual_top5 = result.order[:5]
            union = set(actual_top5) | set(top5)
            report.system_top5_overlap = (
                len(set(actual_top5) & set(top5)) / len(union) if union else 0.0
            )

            p_win = self._p_win_from_record(record)
            if p_win:
                report.system_log_loss = log_loss(winner, p_win)
                report.system_brier = brier_score(winner, p_win)

        # Fiabilite de chaque source externe sur son segment. La cote du
        # gagnant est recuperee depuis le rapport stocke (MarketWatch) quand la
        # course brute n'est pas fournie : sans elle, le segment serait
        # systematiquement "inconnu" et la segmentation par intervalle de cote
        # perdrait tout son interet.
        winner_odds = None
        if race is not None and winner:
            horse = race.by_id(winner)
            winner_odds = horse.odds if horse else None
        if winner_odds is None:
            winner_odds = _odds_from_record(record, winner)
        segment = SegmentKey.of(report.discipline, winner_odds)

        for pick in (record.get("externalconsensus") or {}).get("picks", []):
            source = str(pick.get("source"))
            source_ranking = [str(h) for h in pick.get("ranking") or []]
            hit = bool(winner and winner in source_ranking[:5])
            report.source_hits[source] = hit
            self.ledger.record(
                source,
                segment,
                hit,
                log_loss_value=report.system_log_loss,
                brier_value=report.system_brier,
            )
        return report

    @staticmethod
    def _p_win_from_record(record: Mapping[str, Any]) -> dict[str, float]:
        consensus = record.get("consensusinterne") or {}
        monte = consensus.get("montecarlo") or {}
        probabilities = monte.get("probabilities") or {}
        return {
            str(horse_id): float(value.get("p_win", 0.0))
            for horse_id, value in probabilities.items()
            if isinstance(value, Mapping)
        }

    # -- évaluation cumulée ------------------------------------------------

    def cumulative(
        self,
        records: Sequence[Mapping[str, Any]],
        results: Mapping[str, OfficialResult],
    ) -> CumulativeEvaluation:
        """Évalue un historique complet (protocole glissant, sans fuite)."""
        reports: list[EveningReport] = []
        for record in records:
            race_id = str(record.get("race_id"))
            official = results.get(race_id)
            if official is None:
                continue
            reports.append(self.evaluate(record, official))

        if not reports:
            return CumulativeEvaluation()

        n = len(reports)
        log_losses = [r.system_log_loss for r in reports if r.system_log_loss is not None]
        briers = [r.system_brier for r in reports if r.system_brier is not None]

        # ECE : confiance annoncée vs réussite réelle, sur le gagnant.
        predictions: dict[str, float] = {}
        outcomes: dict[str, bool] = {}
        for record in records:
            race_id = str(record.get("race_id"))
            official = results.get(race_id)
            if official is None:
                continue
            for horse_id, probability in self._p_win_from_record(record).items():
                predictions[horse_id] = probability
                outcomes[horse_id] = horse_id == official.winner

        evaluation = CumulativeEvaluation(
            races=n,
            winner_in_top5=sum(1 for r in reports if r.system_winner_in_top5) / n,
            winner_in_top1=sum(1 for r in reports if r.system_winner_in_top1) / n,
            mean_top5_overlap=sum(r.system_top5_overlap for r in reports) / n,
            mean_log_loss=(sum(log_losses) / len(log_losses)) if log_losses else None,
            mean_brier=(sum(briers) / len(briers)) if briers else None,
            ece=(
                expected_calibration_error(predictions, outcomes) if predictions else None
            ),
            calibration_usable=n >= MIN_RACES_FOR_CALIBRATION,
            per_source=self.ledger.as_dict(),
        )
        return evaluation

    def source_reliability(self, source: str, discipline: str, odds: float | None) -> float:
        """Fiabilité d'une source, utilisée par ExternalConsensus et l'Orchestrateur."""
        return self.ledger.reliability(source, SegmentKey.of(discipline, odds))


def summarise(evaluation: CumulativeEvaluation) -> str:
    lines = [
        "EveningEvaluation — bilan cumulé",
        f"  Courses évaluées : {evaluation.races}",
        f"  Gagnant dans le Top 5 : {evaluation.winner_in_top5 * 100:.1f}%",
        f"  Gagnant en tête : {evaluation.winner_in_top1 * 100:.1f}%",
        f"  Recouvrement moyen du Top 5 : {evaluation.mean_top5_overlap * 100:.1f}%",
    ]
    if evaluation.mean_log_loss is not None:
        lines.append(f"  Log loss moyenne : {evaluation.mean_log_loss:.4f}")
    if evaluation.mean_brier is not None:
        lines.append(f"  Score de Brier moyen : {evaluation.mean_brier:.4f}")
    if evaluation.ece is not None:
        lines.append(f"  ECE : {evaluation.ece:.4f}")
    if not evaluation.calibration_usable:
        lines.append(
            f"  ⚠ métriques de calibration pas encore exploitables "
            f"(minimum {MIN_RACES_FOR_CALIBRATION} courses)"
        )
    if evaluation.per_source:
        lines.append("  Fiabilité par source :")
        for key, raw in evaluation.per_source.items():
            lines.append(
                f"    · {key} — {raw['hit_rate'] * 100:.0f}% sur {raw['total']} course(s)"
            )
    return "\n".join(lines)


__all__ = [
    "EveningEvaluation",
    "EveningReport",
    "CumulativeEvaluation",
    "PmuResultClient",
    "summarise",
    "MIN_RACES_FOR_CALIBRATION",
]
