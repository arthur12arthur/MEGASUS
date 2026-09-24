"""Module 1.13 — EveningEvaluation (AgentH) et métriques de calibration."""

from __future__ import annotations

from hyperion.evaluation.calibration import (
    N_BINS,
    ReliabilityLedger,
    ReliabilityRecord,
    SegmentKey,
    brier_score,
    expected_calibration_error,
    log_loss,
    odds_band,
)
from hyperion.evaluation.evening import (
    MIN_RACES_FOR_CALIBRATION,
    CumulativeEvaluation,
    EveningEvaluation,
    EveningReport,
    PmuResultClient,
    summarise,
)

__all__ = [
    "EveningEvaluation",
    "EveningReport",
    "CumulativeEvaluation",
    "PmuResultClient",
    "summarise",
    "ReliabilityLedger",
    "ReliabilityRecord",
    "SegmentKey",
    "log_loss",
    "brier_score",
    "expected_calibration_error",
    "odds_band",
    "MIN_RACES_FOR_CALIBRATION",
    "N_BINS",
]
