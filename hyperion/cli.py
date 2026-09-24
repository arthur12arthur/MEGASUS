"""Interface en ligne de commande d'Hyperion.

Commandes :

    hyperion run        analyse une course (JSON) et affiche / stocke le rapport
    hyperion demo       analyse une course synthétique de démonstration
    hyperion backtest   backtest sur N courses synthétiques (labo Ouroboros)
    hyperion calibrate  calibre la concentration du Monte Carlo (walk-forward)
    hyperion evaluate   évaluation du soir sur un historique stocké

Toutes les commandes acceptent ``--json`` pour une sortie machine.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from hyperion.config import ROOT, Settings
from hyperion.pipeline import run_pipeline
from hyperion.synthetic import SyntheticProvider


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="sortie JSON exploitable")
    parser.add_argument("--date", help="date de la course (AAAA-MM-JJ)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hyperion",
        description="Hyperion — système d'analyse et de prédiction hippique",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="analyser une course depuis un JSON")
    run.add_argument("--input", required=True, help="chemin du journal structuré (JSON)")
    run.add_argument("--panel", help="JSON des pronostics du panel externe")
    run.add_argument("--odds", help="JSON des cotes les plus récentes")
    run.add_argument("--store", action="store_true", help="stocker le rapport dans data/runs")
    run.add_argument("--deliver", action="store_true", help="envoyer via Telegram/email")
    _add_common(run)

    demo = subparsers.add_parser("demo", help="démonstration sur course synthétique")
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--index", type=int, default=1)
    _add_common(demo)

    backtest = subparsers.add_parser("backtest", help="backtest sur courses synthétiques")
    backtest.add_argument("--n", type=int, default=30, help="nombre de courses")
    backtest.add_argument("--seed", type=int, default=42)
    backtest.add_argument("--concentration", type=float, default=None)
    _add_common(backtest)

    calibrate = subparsers.add_parser("calibrate", help="calibrer la concentration (MC)")
    calibrate.add_argument("--n", type=int, default=20)
    calibrate.add_argument("--seed", type=int, default=42)
    _add_common(calibrate)

    evaluate = subparsers.add_parser("evaluate", help="évaluation du soir")
    evaluate.add_argument("--results", help="JSON des résultats officiels")
    _add_common(evaluate)

    return parser


# --------------------------------------------------------------------------
# Commandes
# --------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    from hyperion.external import ManualPanelProvider
    from hyperion.ingestion import JsonFileProvider
    from hyperion.storage import JsonStore

    settings = Settings()
    race = JsonFileProvider(args.input).fetch(_parse_date(args.date))

    latest_odds: dict[str, float] = {}
    if args.odds:
        latest_odds = {
            str(k): float(v)
            for k, v in json.loads(Path(args.odds).read_text(encoding="utf-8")).items()
        }

    picks: list[Any] = []
    if args.panel:
        payload = json.loads(Path(args.panel).read_text(encoding="utf-8"))
        picks = ManualPanelProvider(payload).fetch(race)

    result = run_pipeline(race, settings, latest_odds=latest_odds, external_picks=picks)

    if args.json:
        print(json.dumps(result.record, ensure_ascii=False, indent=2, default=str))
    else:
        for block in result.blocks.values():
            print(block)
            print()

    if args.store:
        path = JsonStore(settings=settings).save(result.record)
        shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        print(f"Rapport stocké : {shown}", file=sys.stderr)

    if args.deliver:
        from hyperion.delivery import Delivery

        reports = Delivery(settings).deliver(
            subject=f"Hyperion — {race.race_id}",
            messages=list(result.blocks.values()),
        )
        for report in reports:
            print(f"[{report.channel}] {report.ok} — {report.detail}", file=sys.stderr)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from hyperion.synthetic import SyntheticProvider

    settings = Settings()
    provider = SyntheticProvider(seed=args.seed)
    synthetic = provider.race(dt.date.today(), index=args.index)
    # La demo simule une derive de cote realiste entre la publication du PDF
    # et l'instant de l'analyse : c'est tout l'interet de MarketWatch.
    drifted = _drift_odds(synthetic.market_odds, args.seed + args.index)
    result = run_pipeline(synthetic.race, settings, latest_odds=drifted)
    if args.json:
        payload = result.record
        payload["_verite_latente"] = synthetic.true_ranking()
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for block in result.blocks.values():
            print(block)
            print()
        print("VÉRITÉ (capacité latente, non vue par le système) :")
        for position, horse_id in enumerate(synthetic.true_ranking()[:5], start=1):
            horse = synthetic.race.by_id(horse_id)
            print(f"  {position}. {horse.name if horse else horse_id}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from hyperion.lab import Ouroboros, aggregate
    from hyperion.synthetic import SyntheticProvider

    provider = SyntheticProvider(seed=args.seed)
    ouroboros = Ouroboros()
    runs = []
    for index in range(args.n):
        synthetic = provider.race(dt.date(2026, 1, 1) + dt.timedelta(days=index), index=index + 1)
        actual = synthetic.simulate_outcome(seed=1000 + index)
        runs.append(ouroboros.evaluate_shadow(synthetic.race, actual))
    summary = aggregate(runs)
    if args.json:
        print(json.dumps({"summary": summary, "runs": [r.as_dict() for r in runs]},
                         ensure_ascii=False, indent=2, default=str))
    else:
        print(f"Backtest sur {args.n} courses synthétiques\n")
        print(f"  Gagnant dans le Top 5 : {summary.get('winner_in_top5', 0) * 100:.1f}%")
        print(f"  Gagnant en tête      : {summary.get('winner_in_top1', 0) * 100:.1f}%")
        print(f"  Recouvrement Top 5   : {summary.get('mean_top5_overlap', 0) * 100:.1f}%")
        if summary.get("mean_log_loss") is not None:
            print(f"  Log loss moyenne     : {summary['mean_log_loss']:.4f}")
        if summary.get("mean_brier") is not None:
            print(f"  Brier moyen          : {summary['mean_brier']:.4f}")
        # Référence : le marché (cote la plus basse) aurait-il fait mieux ?
        market_wins = sum(
            1 for index, run in enumerate(runs)
            if _market_top5(provider, args.seed, index) and run.actual_order
            and run.actual_order[0] in _market_top5(provider, args.seed, index)
        )
        if runs:
            print(f"\n  Référence marché (favori des cotes) : "
                  f"{market_wins / len(runs) * 100:.1f}%")
    return 0


def _drift_odds(odds: dict[str, float], salt: int) -> dict[str, float]:
    """Simule une evolution de cote entre la publication du PDF et l'analyse."""
    import random

    rng = random.Random(salt)
    out: dict[str, float] = {}
    for horse_id, value in odds.items():
        factor = 1.0 + rng.choice((-0.22, -0.10, 0.0, 0.12, 0.28))
        out[horse_id] = round(max(1.2, value * factor), 2)
    return out


def _market_top5(provider: SyntheticProvider, seed: int, index: int) -> list[str]:
    synthetic = provider.race(dt.date(2026, 1, 1) + dt.timedelta(days=index), index=index + 1)
    return [h.horse_id for h in synthetic.race.order_by_odds()[:5]]


def cmd_calibrate(args: argparse.Namespace) -> int:
    from hyperion.lab import Ouroboros
    from hyperion.synthetic import SyntheticProvider

    provider = SyntheticProvider(seed=args.seed)
    races = [
        provider.race(dt.date(2026, 1, 1) + dt.timedelta(days=i), index=i + 1)
        for i in range(args.n)
    ]
    results = {
        synthetic.race.race_id: synthetic.simulate_outcome(seed=2000 + i)
        for i, synthetic in enumerate(races)
    }
    ouroboros = Ouroboros()
    best, scores = ouroboros.calibrate_concentration(
        [s.race for s in races], results
    )
    if args.json:
        print(json.dumps({"best": best, "scores": scores}, indent=2))
    else:
        print("Calibration de la concentration (protocole glissant)\n")
        for concentration, score in sorted(scores.items()):
            marker = "  <-- retenu" if concentration == best else ""
            print(f"  concentration {concentration:<5} : log loss {score:.4f}{marker}")
        print(f"\nConcentration retenue : {best}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from hyperion.evaluation import EveningEvaluation
    from hyperion.models import OfficialResult
    from hyperion.storage import JsonStore

    store = JsonStore()
    records = store.all_records()
    results: dict[str, OfficialResult] = {}
    if args.results:
        payload = json.loads(Path(args.results).read_text(encoding="utf-8"))
        for item in payload:
            official = OfficialResult.from_dict(item)
            results[official.race_id] = official

    evaluation = EveningEvaluation().cumulative(records, results)
    if args.json:
        print(json.dumps(evaluation.as_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        from hyperion.evaluation import summarise

        print(summarise(evaluation))
    return 0


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------


def _parse_date(raw: str | None) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        raise SystemExit(f"date invalide : {raw} (format attendu AAAA-MM-JJ)")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "run": cmd_run,
        "demo": cmd_demo,
        "backtest": cmd_backtest,
        "calibrate": cmd_calibrate,
        "evaluate": cmd_evaluate,
    }
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
