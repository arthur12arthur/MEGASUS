"""Module 1.6 — BaseScorer (grille figée, score de compétitivité).

Note chaque cheval du groupe filtré sur 5 dimensions pondérées, avec des
poids qui varient selon la discipline détectée (module 1.3). C'est l'UNIQUE
score de compétitivité du pipeline.

Règles d'or du module :
  * une dimension non déterminée vaut ``None`` et ses poids sont
    redistribués sur les dimensions déterminées — on n'estime JAMAIS une
    donnée absente ;
  * la cote n'entre jamais dans le calcul ;
  * la sous-composante ferrure enrichit uniquement ce module : elle ne
    modifie jamais directement le score de classement (1.7) ni le consensus
    externe (1.9) ;
  * lissage bayésien obligatoire sur les petits échantillons.

Lissage bayésien :
    efficacité_lissée = (n × observé + 3 × baseline) / (n + 3)
avec ``baseline`` = taux de réussite moyen du champ. Sans ce lissage, une
victoire isolée suffirait à qualifier un cheval de spécialiste.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from hyperion.analysis.discipline import DIMENSIONS, WEIGHTS
from hyperion.config import Settings
from hyperion.models import Discipline, Horse, Race

#: Points attribués à chaque place dans les courses récentes.
PLACE_POINTS: dict[int, float] = {
    1: 10.0,
    2: 8.0,
    3: 6.5,
    4: 5.0,
    5: 4.0,
    6: 3.0,
    7: 2.0,
    8: 1.0,
}
#: Pondération décroissante des sorties récentes (la plus récente pèse le plus).
RECENCY_WEIGHTS: tuple[float, ...] = (1.0, 0.8, 0.65, 0.5, 0.4)

#: Décomposition de la composante Technique en trot attelé.
TROT_TECHNIQUE_SPLIT = {"ferrure": 0.60, "driver": 0.25, "fiabilite": 0.15}
#: Décomposition de la composante Technique hors trot attelé.
OTHER_TECHNIQUE_SPLIT = {"driver": 0.50, "fiabilite": 0.50}

#: Décomposition de la composante Technique en trot monté (driver davantage
#: déterminant que la ferrure).
MONTE_TECHNIQUE_SPLIT = {"ferrure": 0.25, "driver": 0.55, "fiabilite": 0.20}

#: Force du a priori bayésien (nombre de courses « fantômes »).
BAYES_PRIOR_STRENGTH = 3.0

#: Fenêtre de fraîcheur optimale par discipline : (semaines optimales, spread).
FRESHNESS_WINDOWS: dict[Discipline, tuple[float, float]] = {
    Discipline.TROT_ATTELE: (4.0, 2.5),
    Discipline.TROT_MONTE: (4.0, 2.5),
    Discipline.PLAT: (5.0, 3.5),
    Discipline.OBSTACLE: (8.0, 5.0),
    Discipline.UNKNOWN: (5.0, 3.5),
}

#: Correspondance des aptitudes exprimées en clair.
APTITUDE_WORDS: dict[str, float] = {
    "bon": 1.0,
    "bonne": 1.0,
    "excellent": 1.0,
    "tres bon": 1.0,
    "moyen": 0.5,
    "moyenne": 0.5,
    "passable": 0.5,
    "mauvais": 0.15,
    "mauvaise": 0.15,
    "nul": 0.0,
    "zero": 0.0,
    "0": 0.0,
}


@dataclass
class HorseScore:
    """Score de compétitivité d'un cheval."""

    horse_id: str
    name: str
    dimensions: dict[str, float | None] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    competitiveness: float = 0.0
    undetermined: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "horse_id": self.horse_id,
            "name": self.name,
            "dimensions": dict(self.dimensions),
            "weights_used": dict(self.weights_used),
            "competitiveness": round(self.competitiveness, 4),
            "undetermined": list(self.undetermined),
            "detail": dict(self.detail),
        }


@dataclass
class ScorerResult:
    """Sortie du BaseScorer sur l'ensemble du groupe filtré."""

    discipline: Discipline
    weights: dict[str, float]
    scores: list[HorseScore] = field(default_factory=list)

    def score(self, horse_id: str) -> HorseScore | None:
        for item in self.scores:
            if item.horse_id == horse_id:
                return item
        return None

    def ordered(self) -> list[HorseScore]:
        """Classement par score de compétitivité décroissant."""
        return sorted(self.scores, key=lambda s: s.competitiveness, reverse=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "discipline": self.discipline.value,
            "weights": dict(self.weights),
            "scores": [s.as_dict() for s in self.ordered()],
        }


# --------------------------------------------------------------------------
# Fonctions de dimension
# --------------------------------------------------------------------------


def _log_scale(value: float, ceiling: float) -> float:
    if ceiling <= 0:
        return 0.0
    return min(1.0, math.log1p(max(0.0, value)) / math.log1p(max(0.0, ceiling)))


def score_historique(horse: Horse, field_max_gains: float | None) -> float | None:
    """Dimension « historique / classe ».

    Combine les gains (échelle log, car la distribution est à queue lourde) et
    le taux de réussite. Renvoie ``None`` si aucune des deux données n'existe.
    """
    if horse.gains is None and horse.wins is None and horse.runs is None:
        return None
    components: list[float] = []
    if horse.gains is not None:
        ceiling = field_max_gains if field_max_gains and field_max_gains > 0 else horse.gains
        components.append(_log_scale(horse.gains, ceiling))
    if horse.wins is not None and horse.runs:
        components.append(min(1.0, horse.wins / horse.runs / 0.30))
    if not components:
        return None
    return 10.0 * (sum(components) / len(components))


def score_forme(horse: Horse) -> float | None:
    """Dimension « forme récente » à partir de la musique du cheval."""
    places = horse.recent_places[: len(RECENCY_WEIGHTS)]
    if not places:
        if horse.music:
            places = _parse_music(horse.music)[: len(RECENCY_WEIGHTS)]
        if not places:
            return None
    total = 0.0
    weight_sum = 0.0
    for index, place in enumerate(places):
        weight = RECENCY_WEIGHTS[min(index, len(RECENCY_WEIGHTS) - 1)]
        total += weight * PLACE_POINTS.get(int(place), 0.0)
        weight_sum += weight
    if weight_sum == 0:
        return None
    return 10.0 * (total / weight_sum) / 10.0


def _parse_music(music: str) -> list[int]:
    """'1a2a3a' -> [1, 2, 3]. Les lettres séparent les sorties."""
    out: list[int] = []
    digits = ""
    for char in str(music):
        if char.isdigit():
            digits += char
        else:
            if digits:
                out.append(int(digits))
                digits = ""
    if digits:
        out.append(int(digits))
    return [p for p in out if 1 <= p <= 30]


def _aptitude_value(raw: Any) -> float | None:
    """Normalise une aptitude en 0..1. ``None`` si non déterminée."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
        if value > 1.0:
            return None  # probablement un compte, pas une aptitude
        return max(0.0, min(1.0, value))
    text = str(raw).strip().lower()
    if text in APTITUDE_WORDS:
        return APTITUDE_WORDS[text]
    try:
        value = float(text)
    except ValueError:
        return None
    return max(0.0, min(1.0, value))


def score_aptitude(horse: Horse) -> float | None:
    """Dimension « aptitude terrain / distance ». ``None`` si non déterminée."""
    surface_values = [
        v for v in (_aptitude_value(x) for x in _iter_mapping(horse.surface)) if v is not None
    ]
    distance_values = [
        v for v in (_aptitude_value(x) for x in _iter_mapping(horse.distance)) if v is not None
    ]
    if not surface_values and not distance_values:
        return None
    parts: list[tuple[float, float]] = []
    if surface_values:
        parts.append((0.6, sum(surface_values) / len(surface_values)))
    if distance_values:
        parts.append((0.4, sum(distance_values) / len(distance_values)))
    total_weight = sum(w for w, _ in parts)
    blended = sum(w * v for w, v in parts) / total_weight
    return 10.0 * blended


def _iter_mapping(mapping: Mapping[str, Any] | None) -> list[Any]:
    if not mapping:
        return []
    return list(mapping.values())


def bayesian_rate(observed: float | None, n: float | None, baseline: float) -> float:
    """Lissage bayésien : (n × observé + 3 × baseline) / (n + 3)."""
    if observed is None:
        return baseline
    n = max(0.0, float(n or 0.0))
    return (n * observed + BAYES_PRIOR_STRENGTH * baseline) / (n + BAYES_PRIOR_STRENGTH)


def _stat(stats: Mapping[str, float], key: str) -> float | None:
    value = stats.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score_technique(
    horse: Horse,
    discipline: Discipline,
    baseline_driver: float,
    baseline_reliability: float,
) -> tuple[float | None, dict[str, Any]]:
    """Dimension « facteur technique », spécifique à la discipline.

    En trot attelé : 60 % adéquation de la ferrure du jour, 25 % qualité du
    driver dans cette configuration, 15 % fiabilité historique dans cette
    configuration. Les deux dernières sont lissées bayésiennement.

    Retourne ``(score, détail)``. ``score`` vaut ``None`` si la dimension est
    entièrement non déterminée.
    """
    if discipline is Discipline.TROT_ATTELE:
        split = TROT_TECHNIQUE_SPLIT
    elif discipline is Discipline.TROT_MONTE:
        split = MONTE_TECHNIQUE_SPLIT
    else:
        split = OTHER_TECHNIQUE_SPLIT

    detail: dict[str, Any] = {"split": dict(split)}
    components: dict[str, float | None] = {}

    shoeing = horse.shoeing
    if "ferrure" in split:
        adequacy = shoeing.adequacy if shoeing else None
        components["ferrure"] = None if adequacy is None else 10.0 * adequacy
        detail["ferrure"] = adequacy

    if "driver" in split:
        observed = _stat(horse.driver_stats, "win_rate")
        n = _stat(horse.driver_stats, "runs")
        if observed is None and not n:
            # Aucune donnee : la composante reste non determinee. Le lissage
            # bayesien ne sert QUAND IL EXISTE un echantillon, meme petit.
            components["driver"] = None
        else:
            smoothed = bayesian_rate(observed, n, baseline_driver)
            components["driver"] = 10.0 * smoothed
        detail["driver"] = {"observed": observed, "runs": n}

    if "fiabilite" in split:
        observed = _stat(horse.history_stats, "win_rate")
        n = _stat(horse.history_stats, "runs")
        if observed is None and not n:
            components["fiabilite"] = None
        else:
            smoothed = bayesian_rate(observed, n, baseline_reliability)
            components["fiabilite"] = 10.0 * smoothed
        detail["fiabilite"] = {"observed": observed, "runs": n}

    known = {k: v for k, v in components.items() if v is not None}
    if not known:
        return None, detail
    total_weight = sum(split[k] for k in known)
    if total_weight <= 0:
        return None, detail
    value = sum(split[k] * v for k, v in known.items()) / total_weight
    detail["renormalise"] = total_weight < 1.0
    return value, detail


def score_fraicheur(horse: Horse, discipline: Discipline) -> float | None:
    """Dimension « fraîcheur » — distance à la fenêtre optimale d'absence."""
    weeks = _stat(horse.history_stats, "weeks_since_last_run")
    if weeks is None:
        weeks = horse.shoeing.weeks_since if horse.shoeing else None
    if weeks is None:
        return None
    optimal, spread = FRESHNESS_WINDOWS[discipline]
    z = (float(weeks) - optimal) / spread
    return 10.0 * math.exp(-0.5 * z * z)


# --------------------------------------------------------------------------
# Scorer
# --------------------------------------------------------------------------


class BaseScorer:
    """Applique la grille de pondération de la discipline détectée."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def score(
        self,
        race: Race,
        selected: Sequence[str],
        discipline: Discipline | None = None,
    ) -> ScorerResult:
        discipline = discipline or race.meta.discipline
        weights = dict(WEIGHTS[discipline])
        runners = [h for h in race.runners if h.horse_id in set(selected)]

        gains = [h.gains for h in runners if h.gains is not None]
        field_max_gains = max(gains) if gains else None

        driver_rates = [
            _stat(h.driver_stats, "win_rate") for h in runners if _stat(h.driver_stats, "win_rate")
        ]
        reliability_rates = [
            _stat(h.history_stats, "win_rate")
            for h in runners
            if _stat(h.history_stats, "win_rate")
        ]
        baseline_driver = _mean_or(driver_rates, 0.12)
        baseline_reliability = _mean_or(reliability_rates, 0.12)

        scores: list[HorseScore] = []
        for horse in runners:
            dims: dict[str, float | None] = {
                "historique": score_historique(horse, field_max_gains),
                "forme": score_forme(horse),
                "aptitude": score_aptitude(horse),
                "fraicheur": score_fraicheur(horse, discipline),
            }
            technique, detail = score_technique(
                horse, discipline, baseline_driver, baseline_reliability
            )
            dims["technique"] = technique

            undetermined = [name for name, value in dims.items() if value is None]
            active_weight = sum(weights[name] for name in dims if dims[name] is not None)
            if active_weight <= 0:
                competitiveness = 0.0
                weights_used = {}
            else:
                weights_used = {
                    name: weights[name] / active_weight
                    for name in dims
                    if dims[name] is not None
                }
                competitiveness = sum(
                    weights_used[name] * (dims[name] or 0.0) for name in weights_used
                )
            scores.append(
                HorseScore(
                    horse_id=horse.horse_id,
                    name=horse.name,
                    dimensions=dims,
                    weights_used=weights_used,
                    competitiveness=competitiveness,
                    undetermined=undetermined,
                    detail={"technique_detail": detail},
                )
            )

        return ScorerResult(discipline=discipline, weights=weights, scores=scores)


def _mean_or(values: Sequence[float], default: float) -> float:
    usable = [v for v in values if v is not None]
    return sum(usable) / len(usable) if usable else default


def summarise(result: ScorerResult, top: int = 5) -> str:
    """Résumé lisible du score de compétitivité."""
    lines = [
        f"BaseScorer — {result.discipline.label_fr} "
        f"(poids : {', '.join(f'{k} {v * 100:.0f}%' for k, v in result.weights.items())})",
    ]
    for rank, item in enumerate(result.ordered()[:top], start=1):
        dims = " · ".join(
            f"{name} {'—' if item.dimensions[name] is None else f'{item.dimensions[name]:.1f}'}"
            for name in DIMENSIONS
        )
        lines.append(f"  {rank}. {item.name} — compétitivité {item.competitiveness:.2f}")
        lines.append(f"       {dims}")
        if item.undetermined:
            lines.append(f"       non déterminé : {', '.join(item.undetermined)}")
    return "\n".join(lines)
