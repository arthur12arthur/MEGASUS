"""Évaluation du soir branchée sur l'arrivée officielle PMU (programme du jour).

Pour chaque rapport stocké : retrouver la course dans le programme PMU, lire l'arrivée
définitive, comparer à la sélection du moteur de décision, au classement du système et au
favori du marché, puis ENREGISTRER le bilan (data/evaluations/). Le cumul compare le
prédit à l'observé : c'est la boucle de contrôle du moteur.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from hyperion.history import API_BASE, fetch_json
from hyperion.models import OfficialResult
from hyperion.pmu import resolve_race

EVAL_DIR = Path("data/evaluations")


def is_analysis(record: Mapping[str, Any]) -> bool:
    return bool(record.get("classement")) and record.get("kind") != "official_result"


def _numbers_to_ids(record: Mapping[str, Any]) -> dict[int, str]:
    out: dict[int, str] = {}
    for sig in (record.get("marketwatch") or {}).get("signals", []):
        if sig.get("number") is not None and sig.get("horse_id"):
            out[int(sig["number"])] = str(sig["horse_id"])
    for row in record.get("classement") or []:
        if row.get("numero") is not None and row.get("horse_id"):
            out.setdefault(int(row["numero"]), str(row["horse_id"]))
    return out


def eval_path(record: Mapping[str, Any], root: Path = EVAL_DIR) -> Path:
    date = str(record.get("date") or (record.get("meta") or {}).get("date") or "0000-00-00")
    y, m = date[:4], date[5:7]
    return root / y / m / f"{record.get('race_id')}.json"


def fetch_official_results(
    records: Sequence[Mapping[str, Any]], session: Any, log=print
) -> dict[str, dict[str, Any]]:
    """{race_id: {"result": OfficialResult, "numbers": [n1, n2, ...]}} pour les courses terminées."""
    programmes: dict[str, Any] = {}
    out: dict[str, dict[str, Any]] = {}
    for rec in records:
        meta = rec.get("meta") or {}
        date_s = str(rec.get("date") or meta.get("date") or "")
        try:
            date = dt.date.fromisoformat(date_s)
        except ValueError:
            continue
        try:
            if date_s not in programmes:
                programmes[date_s] = fetch_json(f"{API_BASE}/{date:%d%m%Y}", session, 30.0, 3)
        except Exception as exc:
            log(f"  ! programme {date_s} indisponible : {exc}")
            continue
        found = resolve_race(programmes[date_s], meta.get("hippodrome"), meta.get("race_number"), meta.get("name"))
        if found is None:
            log(f"  ! {rec.get('race_id')} : course introuvable dans le programme PMU")
            continue
        reunion, course_no = found
        course = next(
            (c for r in programmes[date_s]["programme"]["reunions"] if r.get("numOfficiel") == reunion
             for c in r["courses"] if c.get("numOrdre") == course_no),
            None,
        )
        if not course or not course.get("isArriveeDefinitive") or not course.get("ordreArrivee"):
            continue  # pas encore courue ou arrivée non définitive : on n'évalue pas
        numbers = [int(n) for group in course["ordreArrivee"] for n in group]
        ids = _numbers_to_ids(rec)
        out[str(rec["race_id"])] = {
            "result": OfficialResult(
                race_id=str(rec["race_id"]), date=date,
                order=[ids.get(n, f"n{n}") for n in numbers], source="API PMU (programme)",
            ),
            "numbers": numbers,
        }
    return out


def evaluate_record(record: Mapping[str, Any], official: Mapping[str, Any]) -> dict[str, Any]:
    numbers: list[int] = official["numbers"]
    top5 = set(numbers[:5])
    dec = record.get("decision") or {}
    selection = [s.get("numero") for s in dec.get("selection", []) if s.get("numero") is not None]
    ranking = [r.get("numero") for r in record.get("classement") or []]
    odds = {
        s["number"]: (s.get("odds_latest") or s.get("odds_pdf"))
        for s in (record.get("marketwatch") or {}).get("signals", [])
        if s.get("number") is not None and (s.get("odds_latest") or s.get("odds_pdf"))
    }
    favourite = min(odds, key=odds.get) if odds else None
    good = len(top5 & set(selection)) if selection else None
    out: dict[str, Any] = {
        "race_id": record.get("race_id"), "date": record.get("date"),
        "course": (record.get("meta") or {}).get("name"),
        "arrivee_top5": numbers[:5],
        "systeme_gagnant_en_tete": bool(ranking and ranking[0] == numbers[0]),
        "systeme_gagnant_dans_top5": bool(numbers[0] in ranking[:5]),
        "favori_marche": favourite,
        "favori_marche_place": (numbers.index(favourite) + 1) if favourite in numbers else None,
    }
    if selection:
        crit = dec.get("criteres") or []
        out.update({
            "selection": selection, "bons_dans_top5": good, "gagnant_dans_selection": bool(numbers[0] in selection),
            "classe": dec.get("classe"),
            "attendu_bons": dec.get("nb_attendu_dans_top5"),
            "attendu_gagnant_inclus": dec.get("p_gagnant_dans_selection"),
            "attendu_au_moins_3": (dec.get("p_au_moins") or {}).get("3"),
            "au_moins_3_reel": bool(good is not None and good >= 3),
            "criteres_respectes": bool(crit) and all(c.get("respecte") for c in crit),
        })
    return out


def save_evaluation(ev: Mapping[str, Any], record: Mapping[str, Any], root: Path = EVAL_DIR) -> Path:
    path = eval_path(record, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_evaluations(root: Path = EVAL_DIR) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(root).rglob("*.json"))] if Path(root).exists() else []


def cumulative(evs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    with_dec = [e for e in evs if "selection" in e]
    def avg(key, rows):
        vals = [e[key] for e in rows if e.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None
    return {
        "courses": len(evs), "courses_avec_decision": len(with_dec),
        "bons_reels": avg("bons_dans_top5", with_dec), "bons_attendus": avg("attendu_bons", with_dec),
        "gagnant_inclus_reel": avg("gagnant_dans_selection", with_dec),
        "gagnant_inclus_attendu": avg("attendu_gagnant_inclus", with_dec),
        "au_moins_3_reel": avg("au_moins_3_reel", with_dec), "au_moins_3_attendu": avg("attendu_au_moins_3", with_dec),
        "systeme_en_tete": avg("systeme_gagnant_en_tete", evs),
        "dans_criteres": sum(1 for e in with_dec if e.get("criteres_respectes")),
    }


def _num(n: Any) -> str:
    return f"n°{n}"


def render_evening(ev: Mapping[str, Any], cum: Mapping[str, Any]) -> str:
    lines = [f"RÉSULTAT — {ev.get('course') or ev.get('race_id')}",
             "Arrivée : " + " · ".join(_num(n) for n in ev["arrivee_top5"])]
    if "selection" in ev:
        lines.append(
            f"Ta sélection ({', '.join(_num(n) for n in ev['selection'])}) : {ev['bons_dans_top5']} bons sur 5 "
            f"(attendu {ev['attendu_bons']:.1f}) · gagnant {'inclus' if ev['gagnant_dans_selection'] else 'absent'}"
        )
    if ev.get("favori_marche_place"):
        lines.append(f"Favori du marché {_num(ev['favori_marche'])} : arrivé {ev['favori_marche_place']}ᵉ")
    lines.append("Système : gagnant " + ("en tête" if ev["systeme_gagnant_en_tete"] else "dans le top 5" if ev["systeme_gagnant_dans_top5"] else "hors top 5"))
    if cum.get("courses_avec_decision"):
        lines += ["", f"Cumul ({cum['courses_avec_decision']} course(s) avec décision) :",
                  f"  bons chevaux {cum['bons_reels']:.2f}/5 réel contre {cum['bons_attendus']:.2f} attendu",
                  f"  gagnant inclus {cum['gagnant_inclus_reel'] * 100:.0f} % réel contre {cum['gagnant_inclus_attendu'] * 100:.0f} % attendu"]
        if cum["courses_avec_decision"] < 30:
            lines.append("  (trop peu de courses : à lire comme une tendance, pas comme une preuve)")
    return "\n".join(lines)


__all__ = ["fetch_official_results", "evaluate_record", "save_evaluation", "load_evaluations", "cumulative", "render_evening", "is_analysis", "eval_path"]
