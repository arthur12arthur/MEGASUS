"""Module 1.10 — ConfidenceIndex.

Produit un indice de confiance global, CALCULÉ et non arbitraire, à partir de
quatre entrées :

  1. l'écart de score de compétitivité entre le rang 1 et le rang 2 ;
  2. la stabilité inter-seeds du MonteCarloEngine (module 1.7) ;
  3. le taux de convergence avec le consensus externe / p_implicite (1.9) ;
  4. la complétude des données ingérées (modules 1.1 et 1.2).

L'indice n'est JAMAIS présenté sans la liste des données manquantes qui
l'affectent : un indice élevé sur des données incomplètes serait trompeur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from hyperion.models import Discipline, Horse, Race

#: Poids des quatre entrées (somme = 1.0).
WEIGHTS: dict[str, float] = {
    "ecart_scores": 0.30,
    "stabilite_seeds": 0.25,
    "convergence_externe": 0.20,
    "completude_donnees": 0.25,
}

#: Écart de score au-delà duquel l'écart est considéré maximal.
GAP_SATURATION = 2.0

#: Plafonds : une moyenne ne doit pas masquer un maillon faible.
#: Index maximal (sur 10) tant que la condition est vraie.
CAP_FAIBLE = 4.9      # reste dans le niveau « faible »
CAP_MODEREE = 6.4     # ne dépasse pas le niveau « modérée »
MIN_SCORE_COVERAGE = 0.5   # part du poids du BaseScorer réellement déterminée
MIN_STABILITY = 0.6        # stabilité inter-seeds en dessous de laquelle on plafonne
MIN_COMPLETENESS = 0.5

#: Seuils qualitatifs.
LEVELS: tuple[tuple[float, str], ...] = (
    (8.0, "élevée"),
    (6.5, "bonne"),
    (5.0, "modérée"),
    (3.5, "faible"),
    (0.0, "très faible"),
)

#: Champs dont la présence est vérifiée pour la complétude.
CHECKED_FIELDS: dict[Discipline, tuple[str, ...]] = {
    Discipline.TROT_ATTELE: (
        "odds",
        "gains",
        "recent_places",
        "driver",
        "aptitude",
        "shoeing",
    ),
    Discipline.TROT_MONTE: (
        "odds",
        "gains",
        "recent_places",
        "driver",
        "aptitude",
        "shoeing",
    ),
    Discipline.PLAT: ("odds", "gains", "recent_places", "driver", "aptitude"),
    Discipline.OBSTACLE: ("odds", "gains", "recent_places", "driver", "aptitude"),
    Discipline.UNKNOWN: ("odds", "gains", "recent_places", "driver", "aptitude"),
}


@dataclass
class ConfidenceResult:
    """Sortie du module 1.10."""

    index: float = 0.0
    level: str = "très faible"
    components: dict[str, float] = field(default_factory=dict)
    missing_data: list[str] = field(default_factory=list)
    justification: list[str] = field(default_factory=list)
    caps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": round(self.index, 3),
            "level": self.level,
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "missing_data": list(self.missing_data),
            "justification": list(self.justification),
            "caps": list(self.caps),
        }


def _horse_field_present(horse: Horse, field_name: str, discipline: Discipline) -> bool:
    if field_name == "odds":
        return horse.odds is not None
    if field_name == "gains":
        return horse.gains is not None
    if field_name == "recent_places":
        return bool(horse.recent_places) or bool(horse.music)
    if field_name == "driver":
        return bool(horse.driver) or bool(horse.driver_stats)
    if field_name == "aptitude":
        return bool(horse.surface) or bool(horse.distance)
    if field_name == "shoeing":
        if not discipline.is_trot:
            return True  # non pertinent hors trot, ne pénalise pas
        return horse.shoeing is not None and horse.shoeing.adequacy is not None
    return False


def data_completeness(race: Race, discipline: Discipline | None = None) -> tuple[float, list[str]]:
    """Complétude des données (0..1) + liste des manques, cheval par cheval."""
    discipline = discipline or race.meta.discipline
    checked = CHECKED_FIELDS.get(discipline, CHECKED_FIELDS[Discipline.UNKNOWN])
    missing: list[str] = []
    if not race.runners:
        return 0.0, ["aucun partant"]

    total = 0
    present = 0
    for horse in race.runners:
        for field_name in checked:
            total += 1
            if _horse_field_present(horse, field_name, discipline):
                present += 1
            else:
                label = f"n°{horse.number} · {horse.name}" if horse.number is not None else horse.name
                missing.append(f"{label} : {field_name}")
    return (present / total if total else 0.0), missing


def score_coverage(scorer: Any) -> float | None:
    """Part du poids du BaseScorer effectivement déterminée (0..1), moyenne des chevaux.

    Le BaseScorer renormalise les poids sur les seules dimensions connues : si
    seule « historique » (28 %) est calculable, le classement repose sur 28 % du
    modèle, ce que la complétude des champs seule ne montre pas.
    """
    scores = getattr(scorer, "scores", None)
    weights = getattr(scorer, "weights", None)
    if not scores or not weights:
        return None
    total = sum(weights.values())
    if total <= 0:
        return None
    per_horse = [
        sum(w for name, w in weights.items() if s.dimensions.get(name) is not None) / total
        for s in scores
    ]
    return sum(per_horse) / len(per_horse)


class ConfidenceIndex:
    """Calcule l'indice de confiance global du pipeline."""

    def __init__(self, weights: Mapping[str, float] | None = None) -> None:
        self.weights = dict(weights or WEIGHTS)

    def compute(
        self,
        race: Race,
        competitiveness: Mapping[str, float],
        stability: float,
        convergence: float | None = None,
        discipline: Discipline | None = None,
        scoring_coverage: float | None = None,
        identification: Sequence[str] = (),
    ) -> ConfidenceResult:
        result = ConfidenceResult()
        ordered = sorted(competitiveness.values(), reverse=True)
        gap = ordered[0] - ordered[1] if len(ordered) >= 2 else 0.0
        gap_component = max(0.0, min(1.0, gap / GAP_SATURATION)) * 10.0

        stability_component = max(0.0, min(1.0, stability)) * 10.0
        convergence_component = (
            max(0.0, min(1.0, convergence)) * 10.0 if convergence is not None else 5.0
        )
        completeness, missing = data_completeness(race, discipline)
        completeness_component = completeness * 10.0

        result.components = {
            "ecart_scores": gap_component,
            "stabilite_seeds": stability_component,
            "convergence_externe": convergence_component,
            "completude_donnees": completeness_component,
        }
        total_weight = sum(self.weights.values())
        result.index = (
            sum(self.weights[name] * value for name, value in result.components.items())
            / total_weight
        )
        result.missing_data = missing

        # Plafonds : le maillon le plus faible borne l'indice.
        def cap(limit: float, reason: str) -> None:
            if result.index > limit:
                result.index = limit
            result.caps.append(f"indice plafonné à {limit:.1f} : {reason}")

        if identification:
            cap(CAP_FAIBLE, "course mal identifiée (" + " ; ".join(identification) + ")")
        if scoring_coverage is not None and scoring_coverage < MIN_SCORE_COVERAGE:
            cap(
                CAP_FAIBLE,
                f"le score ne repose que sur {scoring_coverage * 100:.0f}% du modèle "
                "(dimensions non déterminées)",
            )
        if stability < MIN_STABILITY:
            cap(CAP_MODEREE, f"top 3 instable entre seeds ({stability * 100:.0f}%)")
        if completeness < MIN_COMPLETENESS:
            cap(CAP_MODEREE, f"données incomplètes ({completeness * 100:.0f}%)")
        result.level = _level(result.index)

        result.justification = [
            f"écart de score rang 1 / rang 2 = {gap:.2f} "
            f"(saturé à {GAP_SATURATION}) → {gap_component:.1f}/10",
            f"stabilité inter-seeds = {stability * 100:.0f}% → "
            f"{stability_component:.1f}/10",
        ]
        if convergence is not None:
            result.justification.append(
                f"convergence Top 5 interne/externe = {convergence * 100:.0f}% → "
                f"{convergence_component:.1f}/10"
            )
        else:
            result.justification.append(
                "convergence externe indisponible → valeur neutre 5.0/10"
            )
        result.justification.append(
            f"complétude des données = {completeness * 100:.0f}% "
            f"({len(missing)} champ(s) manquant(s)) → {completeness_component:.1f}/10"
        )
        if scoring_coverage is not None:
            result.justification.append(
                f"couverture du score = {scoring_coverage * 100:.0f}% du poids du modèle"
            )
        return result


def _level(index: float) -> str:
    for threshold, label in LEVELS:
        if index >= threshold:
            return label
    return "très faible"


def summarise(result: ConfidenceResult) -> str:
    lines = [
        f"ConfidenceIndex — {result.index:.1f}/10 ({result.level})",
    ]
    lines.extend(f"  · {text}" for text in result.justification)
    lines.extend(f"  ⚠ {text}" for text in result.caps)
    if result.missing_data:
        shown = result.missing_data[:10]
        lines.append(f"  Données manquantes ({len(result.missing_data)}) :")
        lines.extend(f"    · {item}" for item in shown)
        if len(result.missing_data) > len(shown):
            lines.append(f"    · … et {len(result.missing_data) - len(shown)} autre(s)")
    return "\n".join(lines)


__all__ = ["ConfidenceIndex", "ConfidenceResult", "data_completeness", "score_coverage", "WEIGHTS"]
