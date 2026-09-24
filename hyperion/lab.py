"""Labo Ouroboros — principe de non-régression.

Toute modification apportée à un module (recalibration de poids, ajout d'une
dimension par discipline, changement de seuil) doit être testée ICI, en
lecture seule et en mode shadow sur des courses passées, AVANT tout
déploiement sur un système en production.

Protocole glissant sans fuite de données (walk-forward) :

    pour chaque date frontière F de la grille :
        calibrer UNIQUEMENT sur les courses strictement antérieures à F
        tester UNIQUEMENT sur les courses postérieures ou égales à F
    agréger les résultats

On ne recalibre JAMAIS un paramètre en observant directement le résultat
qu'on cherche à prédire.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from hyperion.config import Settings
from hyperion.consensus.montecarlo import calibration_grid
from hyperion.evaluation.calibration import (
    brier_score,
    expected_calibration_error,
    log_loss,
)


@dataclass
class ShadowRun:
    """Résultat d'une exécution en mode shadow (aucun envoi, aucun stockage)."""

    race_id: str
    date: dt.date
    predicted_top: list[str] = field(default_factory=list)
    actual_order: list[str] = field(default_factory=list)
    winner_in_top5: bool = False
    winner_in_top1: bool = False
    top5_overlap: float = 0.0
    log_loss: float | None = None
    brier: float | None = None
    field_size: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "race_id": self.race_id,
            "date": self.date.isoformat(),
            "predicted_top": list(self.predicted_top),
            "actual_order": list(self.actual_order),
            "winner_in_top5": self.winner_in_top5,
            "winner_in_top1": self.winner_in_top1,
            "top5_overlap": round(self.top5_overlap, 4),
            "log_loss": None if self.log_loss is None else round(self.log_loss, 6),
            "brier": None if self.brier is None else round(self.brier, 6),
            "field_size": self.field_size,
        }


@dataclass
class OuroborosReport:
    """Rapport de non-régression comparant deux configurations."""

    baseline: dict[str, Any] = field(default_factory=dict)
    candidate: dict[str, Any] = field(default_factory=dict)
    folds: int = 0
    runs: list[ShadowRun] = field(default_factory=list)
    recommendation: str = ""

    @property
    def regression(self) -> bool:
        base = self.baseline.get("winner_in_top5", 0.0)
        cand = self.candidate.get("winner_in_top5", 0.0)
        return cand < base

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "candidate": self.candidate,
            "folds": self.folds,
            "regression": self.regression,
            "recommendation": self.recommendation,
            "runs": [r.as_dict() for r in self.runs],
        }


def aggregate(runs: Sequence[ShadowRun]) -> dict[str, Any]:
    """Agrège des exécutions shadow en métriques globales."""
    if not runs:
        return {"n": 0}
    n = len(runs)
    log_losses = [r.log_loss for r in runs if r.log_loss is not None]
    briers = [r.brier for r in runs if r.brier is not None]
    return {
        "n": n,
        "winner_in_top5": sum(1 for r in runs if r.winner_in_top5) / n,
        "winner_in_top1": sum(1 for r in runs if r.winner_in_top1) / n,
        "mean_top5_overlap": sum(r.top5_overlap for r in runs) / n,
        "mean_log_loss": (sum(log_losses) / len(log_losses)) if log_losses else None,
        "mean_brier": (sum(briers) / len(briers)) if briers else None,
    }


def walk_forward_folds(
    dates: Sequence[dt.date],
    min_train: int = 3,
) -> list[tuple[list[dt.date], list[dt.date]]]:
    """Découpe une série de dates en plis (entraînement, test) glissants."""
    ordered = sorted(set(dates))
    folds: list[tuple[list[dt.date], list[dt.date]]] = []
    for index in range(min_train, len(ordered)):
        folds.append((ordered[:index], ordered[index:]))
    return folds


class Ouroboros:
    """Laboratoire de non-régression, en lecture seule."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def evaluate_shadow(
        self,
        race: Any,
        actual_order: Sequence[str],
        concentration: float | None = None,
    ) -> ShadowRun:
        """Exécute le classement sur une course passée et le compare au réel.

        ``race`` est une course dont les partants portent déjà leurs données
        historiques ; ``actual_order`` est l'arrivée officielle connue.
        """
        from hyperion.pipeline import run_pipeline

        # Tolérance : accepte une course brute ou un objet SyntheticRace.
        race = getattr(race, "race", race)
        settings = self.settings
        if concentration is not None:
            settings = Settings(**{**settings.__dict__, "mc_concentration": concentration})
        result = run_pipeline(race, settings=settings, now=_midnight(race))
        predicted = result.consensus.ranking[:5]
        actual = list(actual_order)
        winner = actual[0] if actual else None

        probabilities = result.consensus.probabilities
        log_loss_value = None
        brier_value = None
        if winner and probabilities is not None:
            p_win = {
                hid: p.p_win for hid, p in probabilities.probabilities.items()
            }
            log_loss_value = log_loss(winner, p_win)
            brier_value = brier_score(winner, p_win)

        overlap = 0.0
        if actual and predicted:
            union = set(actual[:5]) | set(predicted)
            overlap = len(set(actual[:5]) & set(predicted)) / len(union) if union else 0.0

        return ShadowRun(
            race_id=race.race_id or "inconnue",
            date=race.meta.date or dt.date.today(),
            predicted_top=predicted,
            actual_order=actual,
            winner_in_top5=bool(winner and winner in predicted),
            winner_in_top1=bool(winner and predicted and predicted[0] == winner),
            top5_overlap=overlap,
            log_loss=log_loss_value,
            brier=brier_value,
            field_size=len(race.runners),
        )

    def compare(
        self,
        baseline_runs: Sequence[ShadowRun],
        candidate_runs: Sequence[ShadowRun],
    ) -> OuroborosReport:
        """Compare une configuration de référence à une candidate."""
        baseline = aggregate(baseline_runs)
        candidate = aggregate(candidate_runs)
        report = OuroborosReport(
            baseline=baseline, candidate=candidate, runs=list(candidate_runs)
        )
        if report.regression:
            report.recommendation = (
                "RÉGRESSION détectée : la candidate fait moins bien que la "
                "référence sur le taux de gagnant dans le Top 5. Ne pas déployer."
            )
        elif not baseline_runs:
            report.recommendation = (
                "Aucune exécution de référence : impossible de conclure, "
                "accumuler davantage d'historique."
            )
        else:
            report.recommendation = (
                "Aucune régression détectée : la candidate peut être déployée, "
                "à documenter dans docs/ARCHITECTURE.md."
            )
        return report

    def calibrate_concentration(
        self,
        races: Sequence[Any],
        results: Mapping[str, Sequence[str]],
        grid: Sequence[float] | None = None,
    ) -> tuple[float, dict[float, float]]:
        """Choisit la concentration qui minimise la log-loss (protocole glissant).

        Les paramètres testés proviennent de ``calibration_grid()``. La
        calibration se fait UNIQUEMENT sur les courses fournies, qui doivent
        être antérieures à la course qu'on cherche à prédire.
        """
        grid = list(grid or calibration_grid())
        scores: dict[float, float] = {}
        for concentration in grid:
            losses: list[float] = []
            for race in races:
                actual = list(results.get(race.race_id or "", []))
                if not actual:
                    continue
                run = self.evaluate_shadow(race, actual, concentration=concentration)
                if run.log_loss is not None:
                    losses.append(run.log_loss)
            if losses:
                scores[concentration] = sum(losses) / len(losses)
        if not scores:
            return self.settings.mc_concentration, {}
        best = min(scores, key=lambda c: scores[c])
        return best, scores


def _midnight(race: Any) -> dt.datetime:
    """Horodatage neutre pour les exécutions shadow (avant le départ)."""
    date = race.meta.date or dt.date.today()
    return dt.datetime(date.year, date.month, date.day, 6, 0, tzinfo=dt.timezone.utc)


def expected_calibration(predictions: Mapping[str, float], outcomes: Mapping[str, bool]) -> float:
    """Raccourci vers l'ECE (Expected Calibration Error)."""
    return expected_calibration_error(predictions, outcomes)


__all__ = [
    "Ouroboros",
    "OuroborosReport",
    "ShadowRun",
    "aggregate",
    "walk_forward_folds",
    "expected_calibration",
]
