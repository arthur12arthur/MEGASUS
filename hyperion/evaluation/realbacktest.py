"""Backtest sur COURSES RÉELLES (historique PMU) — le système bat-il le marché ?

Pour chaque course : on reconstruit la course AVANT l'arrivée (partants, musique,
stats, cotes), on exécute le pipeline, puis on compare son classement à l'arrivée
officielle. Références obligatoires : la cote finale (le marché) et le hasard.

Aucune donnée de résultat n'entre dans la reconstruction de la course.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from hyperion.config import Settings
from hyperion.models import Discipline, Horse, Race, RaceMeta
from hyperion.music import parse_music
from hyperion.pipeline import run_pipeline

_DISCIPLINES = {
    "ATTELE": (Discipline.TROT_ATTELE, "trot attelé"),
    "MONTE": (Discipline.TROT_MONTE, "trot monté"),
    "PLAT": (Discipline.PLAT, "plat"),
    "HAIE": (Discipline.OBSTACLE, "obstacle"),
    "STEEPLECHASE": (Discipline.OBSTACLE, "obstacle"),
    "CROSS_COUNTRY": (Discipline.OBSTACLE, "obstacle"),
}
MIN_RUNNERS = 6


def iter_history(directory: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(Path(directory).rglob("*.json")):
        yield from json.loads(path.read_text(encoding="utf-8")).get("courses", [])


def build_race(record: Mapping[str, Any]) -> Race:
    discipline, race_type = _DISCIPLINES.get(record.get("discipline"), (Discipline.UNKNOWN, None))
    meta = RaceMeta(
        hippodrome=record.get("hippodrome"),
        date=dt.date.fromisoformat(record["date"]),
        race_number=record.get("course"),
        meeting=f"R{record.get('reunion')}",
        name=record.get("libelle"),
        distance_m=record.get("distance"),
        terrain=((record.get("terrain") or {}).get("intitule")),
        discipline=discipline,
        race_type=race_type,
    )
    horses: list[Horse] = []
    for p in record["participants"]:
        if str(p.get("statut", "PARTANT")).upper() != "PARTANT":
            continue
        music = parse_music(p.get("musique"))
        gains = p.get("gains_carriere")
        weight = p.get("handicap_poids")
        horses.append(
            Horse(
                horse_id=f"n{p['num']}",
                name=str(p.get("nom") or f"n{p['num']}"),
                number=p["num"],
                driver=(p.get("driver") or None),
                trainer=(p.get("entraineur") or None),
                age=p.get("age"),
                sex=p.get("sexe"),
                gains=gains / 100.0 if isinstance(gains, (int, float)) else None,
                runs=p.get("courses"),
                wins=p.get("victoires"),
                places=p.get("places"),
                recent_places=music.places or None,
                music=p.get("musique"),
                weight_kg=weight / 10.0 if isinstance(weight, (int, float)) and weight > 0 else None,
                odds_pdf=p.get("cote_reference"),
                odds_latest=p.get("cote_direct"),
            )
        )
    return Race(meta=meta, horses=horses)


def actual_order(record: Mapping[str, Any]) -> list[str]:
    """Arrivée officielle -> liste d'ids (ex æquo aplatis dans l'ordre fourni)."""
    order: list[str] = []
    for group in record.get("arrivee") or []:
        order.extend(f"n{n}" for n in group)
    return order


def _market_order(record: Mapping[str, Any], key: str) -> list[str]:
    rows = [
        (p[key], f"n{p['num']}")
        for p in record["participants"]
        if p.get(key) and str(p.get("statut", "PARTANT")).upper() == "PARTANT"
    ]
    return [hid for _, hid in sorted(rows)]


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def evaluate_race(record: Mapping[str, Any], settings: Settings) -> dict[str, Any] | None:
    truth = actual_order(record)
    race = build_race(record)
    runners = [h.horse_id for h in race.horses]
    if len(runners) < MIN_RUNNERS or len(truth) < 3:
        return None
    if record.get("collecte", {}).get("musique_contaminee"):
        return None
    winner = truth[0]
    result = run_pipeline(race, settings)
    system = [h for h in result.ranking if h in runners]
    market = _market_order(record, "cote_direct")
    reference = _market_order(record, "cote_reference")
    n = len(runners)
    top5 = set(truth[:5])
    top3 = set(truth[:3])

    def hit(order: list[str], k: int) -> int:
        return int(winner in order[:k])

    def overlap(order: list[str], k: int, ref: set[str]) -> int:
        return len(set(order[:k]) & ref)

    return {
        "race_id": record["race_id"],
        "discipline": record.get("discipline"),
        "n": n,
        "is_quinte": bool(record.get("is_quinte")),
        "system_top1": hit(system, 1), "system_top3": hit(system, 3), "system_top5": hit(system, 5),
        "market_top1": hit(market, 1), "market_top3": hit(market, 3), "market_top5": hit(market, 5),
        "ref_top1": hit(reference, 1), "ref_top5": hit(reference, 5),
        "random_top1": 1 / n, "random_top3": min(3, n) / n, "random_top5": min(5, n) / n,
        "system_overlap3": overlap(system, 3, top3), "market_overlap3": overlap(market, 3, top3),
        "system_overlap5": overlap(system, 5, top5), "market_overlap5": overlap(market, 5, top5),
        "random_overlap3": 3 * min(3, n) / n, "random_overlap5": 5 * min(5, n) / n,
        "confidence": result.confidence.index,
        "confidence_level": result.confidence.level,
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    out: dict[str, Any] = {"courses": n}
    if n == 0:
        return out
    for who in ("system", "market", "random"):
        for k in ("top1", "top3", "top5"):
            vals = [r[f"{who}_{k}"] for r in rows]
            out[f"{who}_{k}"] = round(sum(vals) / n, 4)
        for k in ("overlap3", "overlap5"):
            out[f"{who}_{k}"] = round(sum(r[f"{who}_{k}"] for r in rows) / n, 3)
    for who in ("system", "market"):
        hits = sum(r[f"{who}_top1"] for r in rows)
        lo, hi = wilson(hits, n)
        out[f"{who}_top1_ic95"] = [round(lo, 4), round(hi, 4)]
    # comparaison appariée système vs marché (gagnant dans le top 5)
    only_sys = sum(1 for r in rows if r["system_top5"] and not r["market_top5"])
    only_mkt = sum(1 for r in rows if r["market_top5"] and not r["system_top5"])
    out["top5_sys_seul"], out["top5_marche_seul"] = only_sys, only_mkt
    return out


def segment(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key))].append(r)
    return {g: summarise(v) for g, v in sorted(groups.items())}


def run_backtest(directory: Path, settings: Settings | None = None,
                 limit: int | None = None, seed: int = 42) -> dict[str, Any]:
    settings = settings or Settings()
    records = list(iter_history(directory))
    if limit and len(records) > limit:
        random.Random(seed).shuffle(records)
        records = records[:limit]
    rows, skipped = [], 0
    for rec in records:
        try:
            row = evaluate_race(rec, settings)
        except Exception:  # une course défaillante ne doit pas fausser/arrêter le backtest
            row = None
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "courses_lues": len(records),
        "courses_ignorees": skipped,
        "global": summarise(rows),
        "par_discipline": segment(rows, "discipline"),
        "par_niveau_confiance": segment(rows, "confidence_level"),
        "quinte": summarise([r for r in rows if r["is_quinte"]]),
    }


__all__ = ["run_backtest", "evaluate_race", "build_race", "actual_order", "summarise", "wilson"]
