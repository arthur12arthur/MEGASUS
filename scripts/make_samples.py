#!/usr/bin/env python3
"""Regenere les donnees d'exemple de data/samples/.

Usage :
    python scripts/make_samples.py [--seed 99] [--date 2026-09-20]

Les courses generees sont synthetiques et reproductibles : elles servent de
support aux tests, a la demonstration et au backtest de non-regression.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hyperion.models import Discipline  # noqa: E402
from hyperion.synthetic import SyntheticProvider  # noqa: E402

OUT = ROOT / "data" / "samples"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=99)
    parser.add_argument("--date", default="2026-09-20")
    args = parser.parse_args()

    date = dt.date.fromisoformat(args.date)
    OUT.mkdir(parents=True, exist_ok=True)

    provider = SyntheticProvider(seed=args.seed, discipline=Discipline.TROT_ATTELE)
    synthetic = provider.race(date, index=1)
    race = synthetic.race

    (OUT / f"journal_{date.isoformat()}.json").write_text(
        json.dumps(race.as_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    ordered = race.order_by_odds()
    # Chaque source doit designer assez de partants pour etre exploitable
    # (seuil MIN_COVERAGE = 60 % du champ).
    panel = {
        "Genybet": [h.name for h in ordered[:7]],
        "Equidia": [h.name for h in ordered[:8]],
        "Paris-Turf": [h.name for h in ordered[:6]][::-1],
        "ZEturf": [h.name for h in ordered[:7]],
        "Turfomania": [h.name for h in ordered[:8]],
    }
    (OUT / f"panel_{date.isoformat()}.json").write_text(
        json.dumps(panel, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    rng = random.Random(args.seed)
    odds = {
        h.horse_id: round(
            max(1.3, h.odds_pdf * (1 + rng.choice((-0.22, -0.08, 0.0, 0.10, 0.26)))), 2
        )
        for h in race.runners
    }
    (OUT / f"cotes_fraiches_{date.isoformat()}.json").write_text(
        json.dumps(odds, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    outcome = synthetic.simulate_outcome(seed=777)
    (OUT / f"resultats_{date.isoformat()}.json").write_text(
        json.dumps(
            [
                {
                    "race_id": race.race_id,
                    "date": date.isoformat(),
                    "order": outcome,
                    "source": "saisie manuelle (exemple)",
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Exemples regeneres dans {OUT} ({len(race.runners)} partants)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
