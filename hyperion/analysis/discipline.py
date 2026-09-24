"""Module 1.3 — DisciplineDetector.

Détermine la discipline de la course pour activer la bonne grille de
pondération du BaseScorer. Lecture directe du champ officiel « type de
course » ; repli par mots-clés si le champ est absent ou ambigu.

Ce module est le premier pas vers des sous-modèles complets par discipline :
il ne prétend pas calibrer trois modèles séparés, il choisit seulement la
grille de poids applicable.
"""

from __future__ import annotations

import re
from typing import Any

from hyperion.models import DISCIPLINE_KEYWORDS, Discipline

#: Correspondances explicites champ officiel -> discipline.
FIELD_MAP: dict[str, Discipline] = {
    "trot": Discipline.TROT_ATTELE,
    "trot attelé": Discipline.TROT_ATTELE,
    "trot attele": Discipline.TROT_ATTELE,
    "attelé": Discipline.TROT_ATTELE,
    "attele": Discipline.TROT_ATTELE,
    "autostart": Discipline.TROT_ATTELE,
    "trot monté": Discipline.TROT_MONTE,
    "trot monte": Discipline.TROT_MONTE,
    "monté": Discipline.TROT_MONTE,
    "monte": Discipline.TROT_MONTE,
    "plat": Discipline.PLAT,
    "course de plat": Discipline.PLAT,
    "obstacle": Discipline.OBSTACLE,
    "haies": Discipline.OBSTACLE,
    "steeple": Discipline.OBSTACLE,
    "steeple-chase": Discipline.OBSTACLE,
    "cross": Discipline.OBSTACLE,
}


_NAME_TRANSLATION = str.maketrans(
    "àâäèéêëîïôöùûüçñÀÂÄÈÉÊËÎÏÔÖÙÛÜÇÑ'-",
    "aaaeeeeiioouuucnaaaeeeeiioouuucn  ",
)


def _normalise(text: str) -> str:
    """Minuscule, sans accents, espaces compactés."""
    return re.sub(r"\s+", " ", text.translate(_NAME_TRANSLATION).casefold()).strip()


def detect_from_field(raw_field: Any) -> tuple[Discipline, str]:
    """Lit le champ officiel ``type de course``.

    Retourne ``(discipline, explication)``. Une valeur inconnue donne
    ``UNKNOWN`` — jamais une devinette hasardeuse à ce niveau.
    """
    if raw_field is None:
        return Discipline.UNKNOWN, "champ 'type de course' absent"
    text = _normalise(str(raw_field))
    if not text:
        return Discipline.UNKNOWN, "champ 'type de course' vide"
    if text in FIELD_MAP:
        return FIELD_MAP[text], f"champ officiel lu directement : '{raw_field}'"
    # Correspondance partielle (le champ peut être « Trot attelé 2150m »).
    for key, discipline in FIELD_MAP.items():
        if key in text:
            return discipline, f"champ officiel partiel : '{raw_field}' -> {key}"
    return Discipline.UNKNOWN, f"champ officiel non reconnu : '{raw_field}'"


def detect_from_keywords(text: str) -> tuple[Discipline, str]:
    """Repli par mots-clés sur le texte libre du journal."""
    if not text:
        return Discipline.UNKNOWN, "aucun texte à analyser"
    normalised = _normalise(text)
    for discipline, keywords in DISCIPLINE_KEYWORDS:
        for keyword in keywords:
            if keyword in normalised:
                return discipline, f"mot-clé '{keyword}' trouvé dans le texte"
    return Discipline.UNKNOWN, "aucun mot-clé de discipline trouvé"


def detect_discipline(race_type: Any = None, free_text: str = "") -> tuple[Discipline, str]:
    """Point d'entrée unique du module 1.3.

    Priorité au champ officiel ; repli par mots-clés seulement si le champ
    est absent, vide ou non reconnu.
    """
    discipline, why = detect_from_field(race_type)
    if discipline is not Discipline.UNKNOWN:
        return discipline, why
    if free_text:
        fallback, fallback_why = detect_from_keywords(free_text)
        if fallback is not Discipline.UNKNOWN:
            return fallback, f"repli par mots-clés ({fallback_why})"
        return Discipline.UNKNOWN, f"{why} ; {fallback_why}"
    return Discipline.UNKNOWN, why


def discipline_weights(discipline: Discipline) -> dict[str, float]:
    """Grille de pondération du BaseScorer pour cette discipline."""
    return dict(WEIGHTS[discipline])


#: Grilles de pondération — figées tant qu'un backtest élargi et séparé par
#: discipline n'a pas permis de les recalibrer (règle de non-régression).
WEIGHTS: dict[Discipline, dict[str, float]] = {
    Discipline.TROT_ATTELE: {
        "historique": 0.25,
        "forme": 0.20,
        "aptitude": 0.20,
        "technique": 0.20,
        "fraicheur": 0.15,
    },
    Discipline.TROT_MONTE: {
        "historique": 0.25,
        "forme": 0.20,
        "aptitude": 0.20,
        "technique": 0.20,
        "fraicheur": 0.15,
    },
    Discipline.PLAT: {
        "historique": 0.28,
        "forme": 0.22,
        "aptitude": 0.20,
        "technique": 0.15,
        "fraicheur": 0.15,
    },
    Discipline.OBSTACLE: {
        "historique": 0.25,
        "forme": 0.18,
        "aptitude": 0.22,
        "technique": 0.20,
        "fraicheur": 0.15,
    },
    Discipline.UNKNOWN: {
        "historique": 0.25,
        "forme": 0.20,
        "aptitude": 0.20,
        "technique": 0.20,
        "fraicheur": 0.15,
    },
}

DIMENSIONS: tuple[str, ...] = ("historique", "forme", "aptitude", "technique", "fraicheur")


def explain_weights(discipline: Discipline) -> str:
    weights = WEIGHTS[discipline]
    parts = [f"{name} {weights[name] * 100:.0f}%" for name in DIMENSIONS]
    return f"{discipline.label_fr} : " + " · ".join(parts)


__all__ = [
    "detect_discipline",
    "detect_from_field",
    "detect_from_keywords",
    "discipline_weights",
    "explain_weights",
    "WEIGHTS",
    "DIMENSIONS",
    "FIELD_MAP",
]
