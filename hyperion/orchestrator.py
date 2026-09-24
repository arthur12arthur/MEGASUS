"""Module 2.1 — Orchestrateur / Confédération.

Niveau supérieur : synthétise les sorties de plusieurs systèmes Hyperion
indépendants (V6, V7, V10, super agents Accio/GenSpark/Manus/Groq) en une
décision unique, en pondérant chaque source selon sa fiabilité historique
(alimentée par EveningEvaluation, module 1.13).

Logique de confédération : la confiance accordée à une source RÉDUIT le poids
des autres sur la même course. Un système très fiable n'a pas besoin d'être
moyenné avec quatre systèmes médiocres.

Dans un premier temps, les rapports bruts sont collés manuellement ; la
récupération automatique est prévue ensuite. Ce module accepte donc aussi
bien des ``SystemReport`` construits en code qu'un JSON collé.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from hyperion.config import Settings

#: Exposant de confederation : > 1 renforce le systeme le mieux calibre.
CONFEDERATION_POWER = 2.0

#: Poids minimal garanti a chaque source (aucune source n'est ignorée).
WEIGHT_FLOOR = 0.10


@dataclass
class SystemReport:
    """Rapport brut d'un système Hyperion indépendant."""

    system: str
    ranking: list[str] = field(default_factory=list)
    reliability: float = 0.5
    confidence: float | None = None
    race_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "ranking": list(self.ranking),
            "reliability": round(self.reliability, 4),
            "confidence": self.confidence,
            "race_id": self.race_id,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SystemReport:
        return cls(
            system=str(data.get("system", "inconnu")),
            ranking=[str(h) for h in data.get("ranking") or []],
            reliability=float(data.get("reliability", 0.5)),
            confidence=(
                None if data.get("confidence") is None else float(data["confidence"])
            ),
            race_id=data.get("race_id"),
            notes=[str(n) for n in data.get("notes") or []],
        )


@dataclass
class Synthesis:
    """Décision de consensus final de la confédération."""

    race_id: str | None
    ranking: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    agreement: float = 0.0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "race_id": self.race_id,
            "ranking": list(self.ranking),
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "agreement": round(self.agreement, 4),
            "notes": list(self.notes),
        }


def _borda_weighted(reports: Sequence[SystemReport], weights: Mapping[str, float]) -> dict[str, float]:
    points: dict[str, float] = {}
    for report in reports:
        weight = weights.get(report.system, 0.0)
        n = len(report.ranking)
        for position, horse_id in enumerate(report.ranking):
            points[horse_id] = points.get(horse_id, 0.0) + weight * (n - position)
    return points


class Orchestrator:
    """Confédération de systèmes Hyperion."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    # -- pondération -------------------------------------------------------

    def weights(self, reports: Sequence[SystemReport]) -> dict[str, float]:
        """Poids de chaque systeme, avec effet confederation.

        Le poids est proportionnel a la fiabilitude ELEVEE A LA PUISSANCE
        ``CONFEDERATION_POWER`` : plus l'exposant est fort, plus le systeme le
        mieux calibre domine la synthese. Un plancher garantit qu'aucune
        source n'est totalement reduite au silence - une source faible peut
        toujours apporter une information que les autres n'ont pas.

        La somme des poids vaut toujours exactement 1.
        """
        count = len(reports)
        if count == 0:
            return {}
        base = {r.system: max(0.0, float(r.reliability)) for r in reports}
        total = sum(base.values())
        if total <= 0:
            equal = 1.0 / count
            return {name: equal for name in base}
        normalised = {name: value / total for name, value in base.items()}
        sharpened = {
            name: value ** CONFEDERATION_POWER for name, value in normalised.items()
        }
        sharp_total = sum(sharpened.values())
        if sharp_total <= 0:  # pragma: no cover - garde defensif
            equal = 1.0 / count
            return {name: equal for name in base}
        sharpened = {name: value / sharp_total for name, value in sharpened.items()}
        return {
            name: (1.0 - WEIGHT_FLOOR) * value + WEIGHT_FLOOR / count
            for name, value in sharpened.items()
        }

    # -- synthèse ----------------------------------------------------------

    def synthesise(self, reports: Sequence[SystemReport]) -> Synthesis:
        reports = [r for r in reports if r.ranking]
        if not reports:
            return Synthesis(race_id=None, ranking=[], notes=["aucun rapport à synthétiser"])

        weights = self.weights(reports)
        points = _borda_weighted(reports, weights)
        ranking = sorted(points, key=lambda h: (-points[h], h))

        # Accord : proportion des paires de systèmes classant le même cheval
        # en tête du Top 3.
        top3_sets = [set(r.ranking[:3]) for r in reports if len(r.ranking) >= 3]
        agreement = 0.0
        if len(top3_sets) >= 2:
            intersections = 0
            pairs = 0
            for i, left in enumerate(top3_sets):
                for right in top3_sets[i + 1 :]:
                    pairs += 1
                    union = left | right
                    if union:
                        intersections += len(left & right) / len(union)
            agreement = intersections / pairs if pairs else 0.0

        notes: list[str] = []
        if agreement < 0.4:
            notes.append(
                "fort désaccord entre les systèmes — la synthèse est peu fiable, "
                "privilégier le système le mieux calibré"
            )
        distinct = {tuple(r.ranking[:3]) for r in reports}
        if len(distinct) > 1:
            notes.append(
                f"{len(distinct)} Top 3 différents parmi les systèmes — "
                "vérifier que chaque système a bien exécuté son protocole"
            )
        return Synthesis(
            race_id=reports[0].race_id,
            ranking=ranking,
            weights=weights,
            agreement=agreement,
            notes=notes,
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> list[SystemReport]:
        """Charge des rapports collés manuellement (JSON)."""
        data = json.loads(payload)
        if isinstance(data, Mapping):
            data = data.get("reports", [])
        return [SystemReport.from_dict(item) for item in data]


def summarise(synthesis: Synthesis, names: Mapping[str, str] | None = None) -> str:
    names = names or {}
    lines = [
        "Orchestrateur — synthèse de la confédération",
        f"  Accord inter-systèmes (Top 3) : {synthesis.agreement * 100:.0f}%",
    ]
    for system, weight in sorted(synthesis.weights.items(), key=lambda kv: -kv[1]):
        lines.append(f"    · {system} — poids {weight * 100:.0f}%")
    lines.append("  Classement consolidé :")
    for position, horse_id in enumerate(synthesis.ranking, start=1):
        lines.append(f"    {position}. {names.get(horse_id, horse_id)}")
    for note in synthesis.notes:
        lines.append(f"  ⚠ {note}")
    return "\n".join(lines)


__all__ = ["Orchestrator", "SystemReport", "Synthesis", "summarise"]
