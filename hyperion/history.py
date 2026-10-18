"""Collecte d'un historique RÉEL de courses (API publique PMU) pour le backtesting.

Un fichier JSON par jour : ``data/history/AAAA/MM/AAAAMMJJ.json``.

Chaque course conserve ce qui est nécessaire pour rejouer le pipeline ET
juger le résultat : méta de la course, terrain (pénétromètre), partants
(musique, stats, cotes de référence et « direct »), arrivée officielle.

Fuite d'information : si la collecte est faite longtemps après la course, la
musique/les gains peuvent déjà INCLURE la course visée. On mesure donc, pour
chaque course, la part des chevaux dont la 1re place de la musique est égale à
leur place d'arrivée (``collecte.musique_contaminee``). Un backtest sérieux
écarte ou corrige les courses contaminées.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from hyperion.music import parse_music

API_BASE = "https://online.turfinfo.api.pmu.fr/rest/client/7/programme"
#: Au-delà de cette part de chevaux « musique[0] == arrivée », la course est jugée contaminée
#: (le hasard donne ~15-20 %).
CONTAMINATION_THRESHOLD = 0.5


def fetch_json(url: str, session: Any, timeout_s: float = 30.0, retries: int = 3) -> Any:
    """GET JSON avec reprises ; lève ``RuntimeError`` après épuisement."""
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            response = session.get(url, timeout=timeout_s, headers={"Accept": "application/json"})
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # réseau, HTTP, JSON
            last = exc
            time.sleep(min(2.0 * (attempt + 1), 6.0))
    raise RuntimeError(f"{url} : {last}")


def _iso(ms: Any) -> str | None:
    try:
        return dt.datetime.fromtimestamp(float(ms) / 1000.0, dt.timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def list_courses(programme: Mapping[str, Any], scope: str = "france") -> list[dict[str, Any]]:
    """Courses terminées du programme, filtrées par ``scope`` : quinte | france | all."""
    prog = programme.get("programme", programme)
    out: list[dict[str, Any]] = []
    for reunion in prog.get("reunions", []):
        pays = (reunion.get("pays") or {}).get("code")
        hippo = reunion.get("hippodrome") or {}
        for course in reunion.get("courses", []):
            if not course.get("isArriveeDefinitive"):
                continue
            paris = {str(p.get("codePari")) for p in (course.get("paris") or [])}
            is_quinte = any("QUINTE" in code for code in paris)
            if scope == "quinte" and not is_quinte:
                continue
            if scope == "france" and pays != "FRA":
                continue
            piste = course.get("penetrometre") or {}
            out.append(
                {
                    "reunion": reunion.get("numOfficiel"),
                    "course": course.get("numOrdre"),
                    "hippodrome": hippo.get("libelleCourt"),
                    "hippodrome_code": hippo.get("code"),
                    "pays": pays,
                    "libelle": course.get("libelle"),
                    "discipline": course.get("discipline"),
                    "specialite": course.get("specialite"),
                    "distance": course.get("distance"),
                    "type_piste": course.get("typePiste"),
                    "categorie": course.get("categorieParticularite"),
                    "partants_declares": course.get("nombreDeclaresPartants"),
                    "heure_depart_utc": _iso(course.get("heureDepart")),
                    "terrain": {
                        "valeur": piste.get("valeurMesure"),
                        "intitule": piste.get("intitule"),
                    }
                    if piste
                    else None,
                    "is_quinte": is_quinte,
                    "arrivee": course.get("ordreArrivee") or [],
                }
            )
    return out


def _rate(block: Mapping[str, Any] | None) -> float | None:
    value = (block or {}).get("rapport")
    return float(value) if isinstance(value, (int, float)) and value > 1.0 else None


def reduce_participant(item: Mapping[str, Any]) -> dict[str, Any]:
    gains = item.get("gainsParticipant") or {}
    direct = item.get("dernierRapportDirect") or {}
    ref = item.get("dernierRapportReference") or {}
    return {
        "num": item.get("numPmu"),
        "nom": item.get("nom"),
        "statut": item.get("statut"),
        "age": item.get("age"),
        "sexe": item.get("sexe"),
        "driver": item.get("driver"),
        "driver_change": item.get("driverChange"),
        "entraineur": item.get("entraineur"),
        "musique": item.get("musique"),
        "courses": item.get("nombreCourses"),
        "victoires": item.get("nombreVictoires"),
        "places": item.get("nombrePlaces"),
        "gains_carriere": gains.get("gainsCarriere"),
        "gains_annee": gains.get("gainsAnneeEnCours"),
        "corde": item.get("placeCorde"),
        "oeilleres": item.get("oeilleres"),
        "handicap_poids": item.get("handicapPoids"),
        "handicap_valeur": item.get("handicapValeur"),
        "cote_direct": _rate(direct),
        "cote_direct_at": _iso(direct.get("dateRapport")),
        "cote_reference": _rate(ref),
        "cote_reference_at": _iso(ref.get("dateRapport")),
        "ordre_arrivee": item.get("ordreArrivee"),
    }


def contamination_ratio(participants: list[dict[str, Any]]) -> float | None:
    """Part des arrivants (1re-9e) dont la 1re place de la musique == leur arrivée."""
    hits = total = 0
    for p in participants:
        rank = p.get("ordre_arrivee")
        if not isinstance(rank, int) or not 1 <= rank <= 9:
            continue
        places = parse_music(p.get("musique")).places
        if not places:
            continue
        total += 1
        hits += int(places[0] == rank)
    return hits / total if total >= 4 else None


def collect_course(
    date: dt.date, ref: Mapping[str, Any], session: Any, now: dt.datetime | None = None
) -> dict[str, Any]:
    url = f"{API_BASE}/{date:%d%m%Y}/R{ref['reunion']}/C{ref['course']}/participants"
    payload = fetch_json(url, session)
    participants = [reduce_participant(p) for p in payload.get("participants", [])]
    ratio = contamination_ratio(participants)
    record = dict(ref)
    record.update(
        {
            "race_id": f"{date:%Y%m%d}-{str(ref['hippodrome_code']).lower()}-r{ref['reunion']}c{ref['course']}",
            "date": date.isoformat(),
            "participants": participants,
            "collecte": {
                "at": (now or dt.datetime.now(dt.timezone.utc)).isoformat(),
                "musique_part_egale_arrivee": ratio,
                "musique_contaminee": bool(ratio is not None and ratio >= CONTAMINATION_THRESHOLD),
            },
        }
    )
    return record


def day_path(out: Path, date: dt.date) -> Path:
    return out / f"{date:%Y}" / f"{date:%m}" / f"{date:%Y%m%d}.json"


def collect_day(
    date: dt.date,
    out: Path,
    session: Any,
    scope: str = "france",
    delay_s: float = 0.3,
    force: bool = False,
    log: Callable[[str], None] = print,
) -> int:
    """Collecte un jour ; renvoie le nombre de courses enregistrées (0 si déjà fait)."""
    path = day_path(out, date)
    if path.exists() and not force:
        return 0
    programme = fetch_json(f"{API_BASE}/{date:%d%m%Y}", session)
    refs = list_courses(programme, scope)
    records: list[dict[str, Any]] = []
    for ref in refs:
        try:
            records.append(collect_course(date, ref, session))
        except Exception as exc:  # une course en échec ne bloque pas la journée
            log(f"  ! R{ref['reunion']}C{ref['course']} ignorée : {exc}")
        time.sleep(delay_s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date": date.isoformat(), "scope": scope, "courses": records},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return len(records)


__all__ = ["collect_day", "collect_course", "list_courses", "contamination_ratio", "day_path"]
