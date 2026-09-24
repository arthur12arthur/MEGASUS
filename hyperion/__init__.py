"""Hyperion — système d'analyse et de prédiction hippique.

Architecture unique décrite dans ``docs/ARCHITECTURE.md`` (source unique de vérité).
Ce package en est l'implémentation Python du pipeline « course du jour » :

    1. DataIngestion  -> 2. DisciplineDetector -> 3. MarketWatch
    4. DataFilter     -> 5. BaseScorer         -> 6. ConsensusInterne
    7. HADES          -> 8. ExternalConsensus  -> 9. ConfidenceIndex
    10. Storage       -> 11. Delivery          -> 12. EveningEvaluation (J+1)

Le niveau supérieur (Orchestrateur / Confédération) agrège plusieurs systèmes
Hyperion indépendants.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__", "run_pipeline", "PipelineResult"]

from hyperion.pipeline import PipelineResult, run_pipeline  # noqa: E402
