"""Mode vivant : surveille le marché jusqu'au départ et ne signale que les vrais changements.

Après l'analyse du matin, le programme relit toutes les ``interval`` secondes les cotes
directes PMU, recalcule l'ordre des chevaux (probabilités calibrées du moteur de décision)
et envoie sur Telegram un message COURT, avec seulement les numéros, quand :
  * l'ordre du top 5 change (avec une marge anti-bruit : deux chevaux voisins n'échangent
    leur place que si l'écart de probabilité dépasse ``margin``) ;
  * un non-partant apparaît.
Chaque passage est enregistré (data/live/...) : on pourra mesurer si les mises à jour
valent quelque chose. Aucun passage ne remplace le rapport du matin.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from hyperion.decision import calibrate, implied, load_params, openness, openness_class
from hyperion.history import API_BASE, fetch_json
from hyperion.pmu import fetch_participants, participants_url, resolve_race

MARGIN = 0.015  # 1,5 point de probabilité de victoire
MOVER_MIN = 0.10  # un mouvement de cote n'est cité comme cause que s'il dépasse 10 %
LIVE_DIR = Path("data/live")


def stable_order(prev: Sequence[int] | None, p: Mapping[int, float], margin: float = MARGIN) -> list[int]:
    """Ordre par probabilité décroissante, avec hystérésis par rapport à l'ordre précédent."""
    base = [n for n in (prev or []) if n in p]
    base += sorted((n for n in p if n not in base), key=lambda n: -p[n])
    if not prev:
        return sorted(p, key=lambda n: -p[n])
    changed = True
    while changed:
        changed = False
        for i in range(len(base) - 1):
            a, b = base[i], base[i + 1]
            if p[b] > p[a] + margin:
                base[i], base[i + 1] = b, a
                changed = True
    return base


def snapshot(payload: Mapping[str, Any], prev_order: Sequence[int] | None, now: dt.datetime) -> dict[str, Any]:
    odds: dict[int, float] = {}
    names: dict[int, str] = {}
    non_runners: list[int] = []
    for item in payload.get("participants", []):
        num = item.get("numPmu")
        if not isinstance(num, int):
            continue
        names[num] = str(item.get("nom") or f"n°{num}")
        if str(item.get("statut", "PARTANT")).upper() != "PARTANT":
            non_runners.append(num)
            continue
        rate = (item.get("dernierRapportDirect") or {}).get("rapport")
        if isinstance(rate, (int, float)) and rate > 1.0:
            odds[num] = float(rate)
    if len(odds) < 4:
        raise ValueError("moins de 4 cotes directes exploitables")
    prm = load_params()
    nums = sorted(odds)
    p_arr = calibrate(implied([odds[n] for n in nums]), prm["beta"])
    p = {n: float(x) for n, x in zip(nums, p_arr)}
    op = openness(p_arr)
    order = stable_order(prev_order, p)
    return {
        "at": now.isoformat(), "odds": odds, "p_win": {n: round(v, 4) for n, v in p.items()},
        "order": order, "top5": order[:5], "names": names, "non_runners": sorted(non_runners),
        "classe": openness_class(op["n_eff"], prm["n_eff_seuils"]),
    }


def _fmt_nums(nums: Sequence[int]) -> str:
    return " · ".join(str(n) for n in nums)


def _main_mover(prev: Mapping[str, Any] | None, new: Mapping[str, Any]) -> str | None:
    if not prev:
        return None
    best, best_move = None, 0.0
    for n, o in new["odds"].items():
        old = (prev.get("odds") or {}).get(n) or (prev.get("odds") or {}).get(str(n))
        if old:
            move = abs(math.log(o / old))
            if move > best_move:
                best, best_move = (n, old, o), move
    if best and best_move >= math.log(1 + MOVER_MIN):
        n, old, o = best
        return f"n°{n} {old:g} → {o:g}"
    return None


def detect(prev: Mapping[str, Any] | None, new: Mapping[str, Any], depart: dt.datetime | None = None) -> list[dict[str, str]]:
    """Alertes entre deux relevés (prev peut venir du rapport du matin)."""
    alerts: list[dict[str, str]] = []
    clock = dt.datetime.fromisoformat(new["at"]).strftime("%H:%M UTC")
    tail = ""
    if depart is not None:
        mins = (depart - dt.datetime.fromisoformat(new["at"])).total_seconds() / 60
        tail = f"\nDépart {depart:%H:%M} UTC (dans {max(0, round(mins))} min)"
    prev_nr = {int(n) for n in (prev or {}).get("non_runners", [])}
    for n in sorted(set(new["non_runners"]) - prev_nr):
        name = new["names"].get(n, f"n°{n}")
        alerts.append({"type": "non_partant", "text": f"⚠ {clock} · NON-PARTANT n°{n} {name}. Top 5 recalculé : {_fmt_nums(new['top5'])}{tail}"})
    if prev and prev.get("top5") and list(prev["top5"]) != list(new["top5"]):
        mover = _main_mover(prev, new)
        extra = [x for x in (f"cote {mover}" if mover else None,
                             f"course {new['classe']}" if new["classe"] != prev.get("classe") else None) if x]
        lines = [f"⚡ {clock} · nouvel ordre : {_fmt_nums(new['top5'])}", f"(avant : {_fmt_nums(prev['top5'])})"]
        if extra:
            lines.append(" · ".join(extra))
        alerts.append({"type": "ordre", "text": "\n".join(lines) + tail})
    return alerts


def final_message(new: Mapping[str, Any], last_change_at: str | None) -> str:
    clock = dt.datetime.fromisoformat(new["at"]).strftime("%H:%M UTC")
    since = (f" — inchangé depuis {dt.datetime.fromisoformat(last_change_at):%H:%M} UTC" if last_change_at else "")
    return f"🏁 {clock} · dernier point avant la clôture : {_fmt_nums(new['top5'])}{since}"


def target_from_record(record: Mapping[str, Any], session: Any, timeout_s: float = 30.0) -> dict[str, Any]:
    """Retrouve (réunion, course) et l'heure de départ dans le programme PMU du jour de la course."""
    meta = record.get("meta") or {}
    date = dt.date.fromisoformat(str(record.get("date") or meta.get("date")))
    programme = fetch_json(f"{API_BASE}/{date:%d%m%Y}", session, timeout_s, 3)
    found = resolve_race(programme, meta.get("hippodrome"), meta.get("race_number"), meta.get("name"))
    if found is None:
        raise ValueError("course introuvable dans le programme PMU")
    reunion, number = found
    course = next(c for r in programme["programme"]["reunions"] if r.get("numOfficiel") == reunion
                  for c in r["courses"] if c.get("numOrdre") == number)
    start = course.get("heureDepart")
    if not start:
        raise ValueError("heure de départ inconnue")
    return {"url": participants_url(date, reunion, number), "reunion": reunion, "course": number,
            "depart": dt.datetime.fromtimestamp(start / 1000, dt.timezone.utc)}


def baseline_from_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Relevé de départ = ce que le rapport du matin a annoncé (ordre par probabilité)."""
    sel = (record.get("decision") or {}).get("selection") or []
    nums = [s["numero"] for s in sel if s.get("numero") is not None]
    return {"top5": nums, "order": nums, "odds": {}, "non_runners": [], "classe": (record.get("decision") or {}).get("classe")} if nums else None


def run_watch(
    record: Mapping[str, Any], session: Any, *, now_fn: Callable[[], dt.datetime], sleep_fn: Callable[[float], None],
    send: Callable[[str], Any], interval_s: float = 600.0, stop_before_min: float = 12.0, final: bool = True,
    store_root: Path = LIVE_DIR, log: Callable[[str], None] = print,
) -> dict[str, Any]:
    try:
        target = target_from_record(record, session)
    except Exception as exc:
        return {"status": "ignore", "reason": str(exc)}
    depart = target["depart"]
    stop_at = depart - dt.timedelta(minutes=stop_before_min)
    if now_fn() >= stop_at:
        return {"status": "trop_tard", "reason": f"départ {depart:%H:%M} UTC : moins de {stop_before_min:g} min"}

    prev = baseline_from_record(record)
    prev_order = prev["order"] if prev else None
    history: list[dict[str, Any]] = []
    alerts_sent: list[dict[str, Any]] = []
    last_change_at: str | None = None
    failures = 0
    path = _store_path(record, store_root)

    while True:
        now = now_fn()
        try:
            snap = snapshot(fetch_participants(target["url"], session, 20.0, 2), prev_order, now)
            failures = 0
        except Exception as exc:
            failures += 1
            history.append({"at": now.isoformat(), "erreur": str(exc)})
            log(f"  ! passage en échec ({exc})")
            if failures >= 3:
                _save(path, record, target, history, alerts_sent)
                return {"status": "interrompu", "reason": "3 échecs consécutifs", "passages": len(history)}
        else:
            for alert in detect(prev, snap, depart):
                alert["at"] = snap["at"]
                alerts_sent.append(alert)
                send(alert["text"])
                last_change_at = snap["at"]
            history.append({k: snap[k] for k in ("at", "top5", "order", "classe", "non_runners", "odds", "p_win")})
            prev, prev_order = snap, snap["order"]
        _save(path, record, target, history, alerts_sent)
        now = now_fn()
        if now >= stop_at:
            if final and prev and prev.get("odds"):
                send(final_message(prev, last_change_at))
            break
        sleep_fn(max(1.0, min(interval_s, (stop_at - now).total_seconds())))
    return {"status": "ok", "passages": len(history), "alertes": len(alerts_sent)}


def _store_path(record: Mapping[str, Any], root: Path) -> Path:
    date = str(record.get("date") or "0000-00-00")
    return Path(root) / date[:4] / date[5:7] / f"{record.get('race_id')}.json"


def _save(path: Path, record: Mapping[str, Any], target: Mapping[str, Any], history: list, alerts: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "race_id": record.get("race_id"), "date": record.get("date"),
        "depart_utc": target["depart"].isoformat(), "passages": history, "alertes": alerts,
    }, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


__all__ = ["run_watch", "snapshot", "detect", "stable_order", "final_message"]
