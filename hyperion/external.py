"""Module 1.9 — ExternalConsensus (panel de référence de 12 sources).

Compare l'analyse interne aux pronostics de la presse hippique française,
À TITRE DE COMPARAISON UNIQUEMENT. Ce module n'influence jamais le score de
compétitivité ni le score de classement internes ; il ne nourrit l'indice de
confiance que par son TAUX DE CONVERGENCE.

Panel de référence (12 sources) : Genybet, Equidia, Canal Turf, PMU, France
Galop, Paris-Turf, ZEturf, Turf BZH, RueDesJoueurs, Betclic Turf, Turfomania,
Quinté du Jour. Les deux dernières ont été ajoutées car ce sont les seules
sources confirmées à publier un historique daté de pronostics et d'arrivées
directement exploitable.

Cette liste est une liste de RÉFÉRENCE, pas une liste fermée : toute source
pertinente trouvée en plus est ajoutée et distinguée du panel dans la sortie.

Stratégie d'extraction (honnête et robuste) : comme les partants sont déjà
connus, chaque source n'a pas besoin d'être « comprise ». On repère
simplement, dans le texte récupéré, l'ordre de première apparition de chaque
partant. Une source dont le taux de partants retrouvés est trop faible est
déclarée NON EXPLOITABLE et exclue — mieux vaut aucune donnée qu'une donnée
inventée.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from hyperion.config import REFERENCE_PANEL, Settings
from hyperion.models import Horse, Race

#: Taux minimal de partants retrouvés pour qu'une source soit exploitable.
MIN_COVERAGE = 0.6
#: Nombre de chevaux constituant le « Top » comparé.
TOP_N = 5


@dataclass
class SourcePick:
    """Pronostic d'une source externe."""

    source: str
    ranking: list[str] = field(default_factory=list)
    coverage: float = 0.0
    usable: bool = False
    note: str = ""
    fetched_at: dt.datetime | None = None
    is_panel_member: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "ranking": list(self.ranking),
            "coverage": round(self.coverage, 3),
            "usable": self.usable,
            "note": self.note,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "is_panel_member": self.is_panel_member,
        }


@dataclass
class ExternalConsensusResult:
    """Sortie du module 1.9."""

    picks: list[SourcePick] = field(default_factory=list)
    p_implicite: dict[str, float] = field(default_factory=dict)
    consensus_ranking: list[str] = field(default_factory=list)
    convergence: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def panel_consulted(self) -> list[str]:
        return [p.source for p in self.picks if p.is_panel_member and p.usable]

    @property
    def additional_found(self) -> list[str]:
        return [p.source for p in self.picks if not p.is_panel_member and p.usable]

    @property
    def failed_sources(self) -> list[str]:
        return [p.source for p in self.picks if not p.usable]

    def external_top(self, n: int = TOP_N) -> list[str]:
        return list(self.consensus_ranking[:n])

    def as_dict(self) -> dict[str, Any]:
        return {
            "panel_consulted": self.panel_consulted,
            "additional_found": self.additional_found,
            "failed_sources": self.failed_sources,
            "picks": [p.as_dict() for p in self.picks],
            "p_implicite": {k: round(v, 6) for k, v in self.p_implicite.items()},
            "consensus_ranking": list(self.consensus_ranking),
            "convergence": round(self.convergence, 4),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------
# Extraction depuis un texte brut
# --------------------------------------------------------------------------


_NAME_TRANSLATION = str.maketrans(
    "àâäèéêëîïôöùûüçñÀÂÄÈÉÊËÎÏÔÖÙÛÜÇÑ'-",
    "aaaeeeeiioouuucnaaaeeeeiioouuucn  ",
)


def _normalise_name(name: str) -> str:
    """Minuscule, sans accents, espaces compactés (insensible à la casse)."""
    return re.sub(r"\s+", " ", name.translate(_NAME_TRANSLATION).casefold()).strip()


def extract_ranking(text: str, horses: Sequence[Horse]) -> tuple[list[str], float]:
    """Repère l'ordre de première apparition de chaque partant dans un texte.

    Retourne ``(classement, couverture)``. La couverture est la proportion de
    partants retrouvés : sous le seuil, la source est jugée non exploitable.
    """
    if not text:
        return [], 0.0
    haystack = _normalise_name(text)
    found: list[tuple[int, str]] = []
    for horse in horses:
        needle = _normalise_name(horse.name)
        if len(needle) < 4:
            # Nom trop court : risque de faux positifs, on l'ignore.
            continue
        position = haystack.find(needle)
        if position >= 0:
            found.append((position, horse.horse_id))
    found.sort(key=lambda item: item[0])
    ranking = [horse_id for _, horse_id in found]
    coverage = len(ranking) / len(horses) if horses else 0.0
    return ranking, coverage


def implicit_probabilities(race: Race) -> dict[str, float]:
    """p_implicite_i = (1 / cote_i) / Σ_j (1 / cote_j).

    Utilise toujours la cote la plus fraîche disponible (MarketWatch).
    """
    inverses: dict[str, float] = {}
    for horse in race.runners:
        odds = horse.odds
        if odds and odds > 0:
            inverses[horse.horse_id] = 1.0 / odds
    total = sum(inverses.values())
    if total <= 0:
        return {}
    return {hid: value / total for hid, value in inverses.items()}


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


class ManualPanelProvider:
    """Provider manuel : les pronostics sont fournis dans un JSON.

    C'est le chemin fiable et reproductible — il évite de dépendre d'un
    scraping qui peut être bloqué (cas vécu : blocage IP des scrapers PMU sur
    GitHub Actions).
    """

    def __init__(self, payload: Mapping[str, Any] | None = None) -> None:
        self.payload = dict(payload or {})

    def fetch(
        self,
        race: Race,
        panel: Sequence[str] = REFERENCE_PANEL,
        as_of: dt.datetime | None = None,
    ) -> list[SourcePick]:
        when = as_of or dt.datetime.now(dt.timezone.utc)
        picks: list[SourcePick] = []
        entries = self.payload.get("sources") or self.payload
        if not isinstance(entries, Mapping):
            return picks
        # Le payload attendu est {source: [noms de chevaux dans l'ordre]}.
        for source, raw_ranking in entries.items():
            if not isinstance(raw_ranking, Sequence) or isinstance(raw_ranking, str):
                continue
            by_name = {h.name: h.horse_id for h in race.runners}
            by_id = {h.horse_id: h.horse_id for h in race.runners}
            ranking: list[str] = []
            for item in raw_ranking:
                text = str(item).strip()
                if text in by_id:
                    ranking.append(by_id[text])
                elif text in by_name:
                    ranking.append(by_name[text])
                else:
                    normalised = _normalise_name(text)
                    for horse in race.runners:
                        if _normalise_name(horse.name) == normalised:
                            ranking.append(horse.horse_id)
                            break
            coverage = len(ranking) / len(race.runners) if race.runners else 0.0
            picks.append(
                SourcePick(
                    source=str(source),
                    ranking=ranking,
                    coverage=coverage,
                    usable=coverage >= MIN_COVERAGE,
                    note="saisie manuelle" if coverage >= MIN_COVERAGE else "saisie incomplète",
                    fetched_at=when,
                    is_panel_member=str(source) in panel,
                )
            )
        return picks


class HttpPanelProvider:
    """Provider HTTP générique, avec repli gracieux.

    Récupère la page d'une source et en extrait l'ordre d'apparition des
    partants. Toute source en échec est marquée non exploitable : le pipeline
    continue avec les sources disponibles plutôt que d'échouer.
    """

    def __init__(
        self,
        url_templates: Mapping[str, str] | None = None,
        timeout_s: int = 20,
        max_retries: int = 2,
    ) -> None:
        self.url_templates = dict(url_templates or {})
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    def fetch(
        self,
        race: Race,
        panel: Sequence[str] = REFERENCE_PANEL,
        as_of: dt.datetime | None = None,
    ) -> list[SourcePick]:
        import requests  # import local : le module reste utilisable sans réseau

        when = as_of or dt.datetime.now(dt.timezone.utc)
        picks: list[SourcePick] = []
        context = {
            "date": race.meta.date.isoformat() if race.meta.date else "",
            "hippodrome": race.meta.hippodrome or "",
            "reunion": race.meta.meeting or "",
            "course": race.meta.race_number or "",
            "nom": race.meta.name or "",
        }
        for source in panel:
            template = self.url_templates.get(source)
            if not template:
                picks.append(
                    SourcePick(
                        source=source,
                        usable=False,
                        note="aucune URL configurée pour cette source",
                        fetched_at=when,
                        is_panel_member=True,
                    )
                )
                continue
            url = template.format(**context)
            text = self._get(requests, url)
            if text is None:
                picks.append(
                    SourcePick(
                        source=source,
                        usable=False,
                        note="récupération impossible (blocage, timeout ou 404)",
                        fetched_at=when,
                        is_panel_member=True,
                    )
                )
                continue
            ranking, coverage = extract_ranking(text, race.runners)
            picks.append(
                SourcePick(
                    source=source,
                    ranking=ranking,
                    coverage=coverage,
                    usable=coverage >= MIN_COVERAGE,
                    note=(
                        "extraction par ordre d'apparition des partants"
                        if coverage >= MIN_COVERAGE
                        else f"couverture insuffisante ({coverage:.0%})"
                    ),
                    fetched_at=when,
                    is_panel_member=True,
                )
            )
        return picks

    def _get(self, requests: Any, url: str) -> str | None:
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.get(
                    url,
                    timeout=self.timeout_s,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; HyperionBot/1.0)"},
                )
                if response.status_code == 200:
                    return response.text
            except Exception:
                pass
            if attempt < self.max_retries:
                import time

                time.sleep(1.5 * (attempt + 1))
        return None


# --------------------------------------------------------------------------
# Consensus externe
# --------------------------------------------------------------------------


class ExternalConsensus:
    """Compare l'analyse interne au panel externe (comparaison seulement)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def compute(
        self,
        race: Race,
        picks: Sequence[SourcePick],
        internal_ranking: Sequence[str] | None = None,
    ) -> ExternalConsensusResult:
        result = ExternalConsensusResult(picks=list(picks))
        result.p_implicite = implicit_probabilities(race)

        usable = [p for p in picks if p.usable and p.ranking]
        if usable:
            # Classement externe par points de Borda (comptage inverse).
            points: dict[str, float] = {}
            for pick in usable:
                n = len(pick.ranking)
                for position, horse_id in enumerate(pick.ranking):
                    points[horse_id] = points.get(horse_id, 0.0) + (n - position)
            result.consensus_ranking = sorted(
                points, key=lambda h: (-points[h], h)
            )
        else:
            result.notes.append(
                "aucune source externe exploitable — la comparaison est vide, "
                "l'analyse interne reste inchangée"
            )

        if internal_ranking:
            internal_top = list(internal_ranking[:TOP_N]
                                )
            external_top = result.external_top(TOP_N)
            union = set(internal_top) | set(external_top)
            if union:
                intersection = set(internal_top) & set(external_top)
                result.convergence = len(intersection) / len(union)
            result.notes.append(
                f"convergence Top {TOP_N} interne/externe : "
                f"{result.convergence * 100:.0f}%"
            )

        if len(result.panel_consulted) < self.settings.external_min_sources:
            result.notes.append(
                f"seulement {len(result.panel_consulted)} sources du panel "
                f"consultées (minimum visé : {self.settings.external_min_sources})"
            )
        return result

    def divergences(
        self,
        result: ExternalConsensusResult,
        internal_ranking: Sequence[str],
        race: Race,
        n: int = TOP_N,
    ) -> dict[str, list[str]]:
        """Chevaux dans un Top mais pas dans l'autre — écart à expliquer."""
        internal_top = set(list(internal_ranking)[:n])
        external_top = set(result.external_top(n))
        return {
            "interne_seulement": [h for h in internal_ranking[:n] if h in internal_top - external_top],
            "externe_seulement": [h for h in result.consensus_ranking[:n] if h in external_top - internal_top],
        }


def summarise(
    result: ExternalConsensusResult,
    race: Race,
    divergences: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """Résumé lisible — deux blocs distincts, comme spécifié."""
    names = {h.horse_id: h.name for h in race.runners}
    lines = ["ExternalConsensus — panel de référence (comparaison seulement)"]
    lines.append(f"  Sources du panel consultées : {', '.join(result.panel_consulted) or 'aucune'}")
    if result.additional_found:
        lines.append(
            f"  Sources additionnelles trouvées : {', '.join(result.additional_found)}"
        )
    if result.failed_sources:
        lines.append(f"  Sources indisponibles : {', '.join(result.failed_sources)}")
    if result.p_implicite:
        lines.append("  Probabilité implicite du marché (la plus forte d'abord) :")
        ranked = sorted(
            result.p_implicite, key=lambda h: result.p_implicite[h], reverse=True
        )
        for horse_id in ranked:
            lines.append(
                f"    · {names.get(horse_id, horse_id)} — "
                f"{result.p_implicite[horse_id] * 100:.1f}%"
            )
    if divergences:
        for label, horse_ids in divergences.items():
            if horse_ids:
                pretty = ", ".join(names.get(h, h) for h in horse_ids)
                lines.append(f"  {label.replace('_', ' ')} : {pretty}")
    lines.append(f"  Taux de convergence Top {TOP_N} : {result.convergence * 100:.0f}%")
    for note in result.notes:
        lines.append(f"  · {note}")
    return "\n".join(lines)


__all__ = [
    "ExternalConsensus",
    "ExternalConsensusResult",
    "SourcePick",
    "ManualPanelProvider",
    "HttpPanelProvider",
    "extract_ranking",
    "implicit_probabilities",
    "TOP_N",
    "MIN_COVERAGE",
]
