"""Enrichissement des partants à partir de l'API publique PMU (``participants``).

Principes
---------
* Les champs du PDF LONAB ne sont JAMAIS écrasés : on ne comble que ce qui manque.
* Les champs de RÉSULTAT (``ordreArrivee``, ``commentaireApresCourse``) ne
  sont jamais lus par :func:`enrich_race` : aucune fuite d'information vers le
  score. Ils servent uniquement à l'évaluation, via :func:`extract_results`.
* La cote « direct » alimente ``odds_latest`` (MarketWatch) ; la cote de
  référence est conservée dans ``raw['pmu']`` avec son horodatage.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from hyperion.models import Horse, Race
from hyperion.music import parse_music

API_BASE = "https://online.turfinfo.api.pmu.fr/rest/client/7/programme"


def participants_url(date: dt.date, meeting: int, race_number: int) -> str:
    """URL de l'endpoint ``participants`` (à valider en ligne : non testé ici)."""
    return f"{API_BASE}/{date:%d%m%Y}/R{meeting}/C{race_number}/participants"


@dataclass
class EnrichmentReport:
    matched: int = 0
    unmatched_pmu: list[str] = field(default_factory=list)
    unmatched_race: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "unmatched_pmu": list(self.unmatched_pmu),
            "unmatched_race": list(self.unmatched_race),
            "warnings": list(self.warnings),
        }


def _norm(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


def _iso(ms: Any) -> str | None:
    try:
        return dt.datetime.fromtimestamp(float(ms) / 1000.0, dt.timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _rate(block: Mapping[str, Any] | None) -> float | None:
    value = (block or {}).get("rapport")
    return float(value) if isinstance(value, (int, float)) and value > 1.0 else None


def load_payload(source: Path | str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return source
    return json.loads(Path(source).read_text(encoding="utf-8"))


def extract_results(payload: Mapping[str, Any]) -> dict[int, int]:
    """Ordre d'arrivée {numéro: rang} — réservé à l'ÉVALUATION, jamais au score."""
    out: dict[int, int] = {}
    for item in payload.get("participants", []):
        rank, number = item.get("ordreArrivee"), item.get("numPmu")
        if isinstance(rank, int) and rank > 0 and isinstance(number, int):
            out[number] = rank
    return out


def _enriched(horse: Horse, item: Mapping[str, Any]) -> Horse:
    music = parse_music(item.get("musique"))
    direct = _rate(item.get("dernierRapportDirect"))
    reference = _rate(item.get("dernierRapportReference"))
    gains = (item.get("gainsParticipant") or {}).get("gainsCarriere")
    weight = item.get("handicapPoids")
    changes: dict[str, Any] = {}

    def fill(attr: str, value: Any) -> None:
        if value in (None, "", ()):
            return
        if getattr(horse, attr) in (None, "", ()):
            changes[attr] = value

    fill("driver", (item.get("driver") or "").strip() or None)
    fill("trainer", (item.get("entraineur") or "").strip() or None)
    fill("age", item.get("age") if isinstance(item.get("age"), int) else None)
    fill("sex", item.get("sexe"))
    fill("gains", gains / 100.0 if isinstance(gains, (int, float)) else None)  # centimes -> €
    fill("runs", item.get("nombreCourses"))
    fill("wins", item.get("nombreVictoires"))
    fill("places", item.get("nombrePlaces"))
    fill("recent_places", music.places)
    fill("music", item.get("musique"))
    fill("weight_kg", weight / 10.0 if isinstance(weight, (int, float)) and weight > 0 else None)
    fill("odds_pdf", reference)  # cote de référence PMU seulement si le PDF n'en a pas

    if direct is not None:
        changes["odds_latest"] = direct
    if str(item.get("statut", "PARTANT")).upper() != "PARTANT":
        changes["non_runner"] = True

    raw = dict(horse.raw)
    raw["pmu"] = {
        "odds_direct": direct,
        "odds_direct_at": _iso((item.get("dernierRapportDirect") or {}).get("dateRapport")),
        "odds_reference": reference,
        "odds_reference_at": _iso((item.get("dernierRapportReference") or {}).get("dateRapport")),
        "draw": item.get("placeCorde"),
        "driver_change": item.get("driverChange"),
        "blinkers": item.get("oeilleres"),
        "music_incidents": music.incidents,
        "statut": item.get("statut"),
    }
    changes["raw"] = raw
    return replace(horse, **changes)


def enrich_race(race: Race, payload: Mapping[str, Any]) -> tuple[Race, EnrichmentReport]:
    """Retourne une NOUVELLE course enrichie + un rapport (la course d'origine est intacte)."""
    report = EnrichmentReport()
    by_number = {p.get("numPmu"): p for p in payload.get("participants", [])}
    seen: set[Any] = set()
    horses: list[Horse] = []
    for horse in race.horses:
        item = by_number.get(horse.number)
        if item is None:
            report.unmatched_race.append(horse.name)
            horses.append(horse)
            continue
        if _norm(item.get("nom")) != _norm(horse.name):
            report.warnings.append(
                f"n°{horse.number} : nom PDF « {horse.name} » ≠ nom PMU « {item.get('nom')} » — ignoré"
            )
            report.unmatched_race.append(horse.name)
            horses.append(horse)
            continue
        seen.add(horse.number)
        report.matched += 1
        horses.append(_enriched(horse, item))
    report.unmatched_pmu = [
        str(p.get("nom")) for n, p in by_number.items() if n not in seen
    ]
    return replace(race, horses=horses), report


__all__ = [
    "EnrichmentReport",
    "enrich_race",
    "extract_results",
    "load_payload",
    "participants_url",
]
