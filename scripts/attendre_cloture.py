"""Attend l'heure optimale avant de lancer l'analyse : DÉPART - HYPERION_LEAD_MIN minutes.

Les cotes sont d'autant plus justes qu'on est proche du départ. L'heure du départ de la
course Quinté du jour (France) est lue dans le programme PMU. En cas de doute (programme
illisible, pas de Quinté, heure déjà passée) : on lance IMMÉDIATEMENT, jamais d'échec.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hyperion.history import API_BASE, fetch_json  # noqa: E402


def quinte_start(programme: dict) -> dt.datetime | None:
    prog = programme.get("programme", programme)
    starts = []
    for reunion in prog.get("reunions", []):
        if (reunion.get("pays") or {}).get("code") != "FRA":
            continue
        for course in reunion.get("courses", []):
            paris = {str(p.get("codePari")) for p in (course.get("paris") or [])}
            if any("QUINTE" in code for code in paris) and course.get("heureDepart"):
                starts.append(dt.datetime.fromtimestamp(course["heureDepart"] / 1000, dt.timezone.utc))
    return min(starts) if starts else None


def wait_seconds(now: dt.datetime, start: dt.datetime | None, lead_min: int = 90, cap_min: int = 330) -> float:
    """Secondes à attendre (0 si l'heure est passée ou inconnue ; plafonné à cap_min)."""
    if start is None:
        return 0.0
    target = start - dt.timedelta(minutes=lead_min)
    return max(0.0, min((target - now).total_seconds(), cap_min * 60.0))


def main() -> int:
    lead = int(os.environ.get("HYPERION_LEAD_MIN", "90"))
    cap = int(os.environ.get("HYPERION_MAX_WAIT_MIN", "330"))
    now = dt.datetime.now(dt.timezone.utc)
    try:
        import requests

        session = requests.Session()
        session.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36"
        programme = fetch_json(f"{API_BASE}/{now:%d%m%Y}", session, 30.0, 3)
        start = quinte_start(programme)
    except Exception as exc:
        print(f"Programme PMU illisible ({exc}) : lancement immédiat.")
        return 0
    if start is None:
        print("Pas de Quinté français trouvé : lancement immédiat.")
        return 0
    secs = wait_seconds(now, start, lead, cap)
    print(f"Départ du Quinté : {start:%H:%M} UTC ; lancement visé : {start - dt.timedelta(minutes=lead):%H:%M} UTC ; attente {secs / 60:.0f} min.")
    end = time.monotonic() + secs
    while (left := end - time.monotonic()) > 0:
        time.sleep(min(left, 600))
        print(f"  … encore {max(0, end - time.monotonic()) / 60:.0f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
