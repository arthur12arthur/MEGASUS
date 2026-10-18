"""Quels signaux prédisent la victoire EN PLUS de la cote ? (logit conditionnel, validation temporelle)

Pour chaque course : P(i gagne) = softmax( b0*log(p_marché_i) + sum_k b_k * x_ik ).
Un signal est utile s'il améliore la log-vraisemblance sur des courses POSTÉRIEURES à
celles qui ont servi à l'estimer, par rapport au modèle « cote seule ».
Driver / entraîneur : taux de victoire calculé uniquement sur les jours PRÉCÉDENTS.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hyperion.config import Settings  # noqa: E402
from hyperion.evaluation.realbacktest import actual_order, build_race, iter_history  # noqa: E402
from hyperion.music import parse_music  # noqa: E402
from hyperion.pipeline import run_pipeline  # noqa: E402

FEATURES = [
    "musique_derniere", "musique_moy3", "musique_victoires5", "musique_non_places5",
    "taux_victoire", "taux_place", "log_courses", "log_gains_par_course",
    "age", "femelle", "poids_relatif", "corde_relative", "oeilleres", "driver_change",
    "mouvement_cote", "driver_force", "entraineur_force", "systeme",
]


def z(vals: list[float]) -> list[float]:
    a = np.array(vals, float)
    s = a.std()
    return list((a - a.mean()) / s) if s > 1e-9 else [0.0] * len(vals)


BASE = os.environ.get("BASE", "cote_direct")  # cote_direct (finale) ou cote_reference (matin)


def build_dataset(directory: Path):
    recs = [r for r in iter_history(directory)]
    recs.sort(key=lambda r: (r["date"], r["reunion"], r["course"]))
    stats = {"driver": defaultdict(lambda: [0, 0]), "entraineur": defaultdict(lambda: [0, 0])}
    pending: list[tuple[str, str, bool]] = []
    cur_date = None
    races, settings = [], Settings()

    def flush():
        for kind, name, won in pending:
            stats[kind][name][0] += 1
            stats[kind][name][1] += int(won)
        pending.clear()

    for rec in recs:
        if rec["date"] != cur_date:
            flush()
            cur_date = rec["date"]
        truth = actual_order(rec)
        parts = [p for p in rec["participants"] if str(p.get("statut", "PARTANT")).upper() == "PARTANT"]
        parts = [p for p in parts if p.get(BASE)]
        if len(parts) < 6 or not truth or rec["collecte"]["musique_contaminee"]:
            continue
        winner = truth[0]
        ids = [f"n{p['num']}" for p in parts]
        if winner not in ids:
            continue
        n = len(parts)
        try:
            res = run_pipeline(build_race(rec), settings)
            comp = {s.horse_id: s.competitiveness for s in res.scorer.scores}
        except Exception:
            continue
        inv = np.array([1.0 / p[BASE] for p in parts])
        q = inv / inv.sum()
        cols: dict[str, list[float]] = {f: [] for f in FEATURES}
        for p in parts:
            m = parse_music(p.get("musique")).places
            last5 = m[:5]
            runs = max(p.get("courses") or 0, 0)
            cols["musique_derniere"].append(min(m[0], 10) if m else 6.0)
            cols["musique_moy3"].append(np.mean([min(x, 10) if x else 10 for x in m[:3]]) if m else 6.0)
            cols["musique_victoires5"].append(sum(1 for x in last5 if x == 1))
            cols["musique_non_places5"].append(sum(1 for x in last5 if x == 0 or x > 3))
            cols["taux_victoire"].append((p.get("victoires") or 0) / runs if runs else 0.0)
            cols["taux_place"].append((p.get("places") or 0) / runs if runs else 0.0)
            cols["log_courses"].append(math.log1p(runs))
            g = p.get("gains_carriere") or 0
            cols["log_gains_par_course"].append(math.log1p(g / 100.0 / max(runs, 1)))
            cols["age"].append(p.get("age") or 5)
            cols["femelle"].append(1.0 if str(p.get("sexe", "")).upper().startswith("FEMELLE") else 0.0)
            cols["poids_relatif"].append(p.get("handicap_poids") or 0)
            cols["corde_relative"].append((p.get("corde") or 0) / n)
            cols["oeilleres"].append(0.0 if str(p.get("oeilleres") or "SANS_OEILLERES").startswith("SANS") else 1.0)
            cols["driver_change"].append(1.0 if p.get("driver_change") else 0.0)
            ref = p.get("cote_reference")
            move = math.log(p["cote_direct"] / ref) if (ref and p.get("cote_direct")) else 0.0
            cols["mouvement_cote"].append(0.0 if BASE == "cote_reference" else move)
            for kind, key, fld in (("driver", "driver_force", "driver"), ("entraineur", "entraineur_force", "entraineur")):
                s, w = stats[kind][p.get(fld) or "?"]
                cols[key].append(math.log(((w + 20 / n) / (s + 20)) * n))
            cols["systeme"].append(comp.get(f"n{p['num']}", 0.0))
        X = np.column_stack([z(cols[f]) if f not in ("femelle", "oeilleres", "driver_change") else cols[f] for f in FEATURES])
        races.append({"date": rec["date"], "q": q, "X": X, "y": ids.index(winner), "disc": rec.get("discipline")})
        for p in parts:
            pending.append(("driver", p.get("driver") or "?", f"n{p['num']}" == winner))
            pending.append(("entraineur", p.get("entraineur") or "?", f"n{p['num']}" == winner))
    return races


def nll(beta, rows, cols, l2=1.0):
    total, grad = 0.0, np.zeros_like(beta)
    for r in rows:
        feats = np.column_stack([np.log(r["q"])] + [r["X"][:, c] for c in cols]) if cols else np.log(r["q"])[:, None]
        u = feats @ beta
        u -= u.max()
        p = np.exp(u); p /= p.sum()
        total -= np.log(p[r["y"]] + 1e-12)
        grad -= feats[r["y"]] - p @ feats
    total += 0.5 * l2 * float(beta[1:] @ beta[1:])
    grad[1:] += l2 * beta[1:]
    return total, grad


def fit(rows, cols, l2=1.0):
    b0 = np.zeros(len(cols) + 1); b0[0] = 1.0
    res = minimize(lambda b: nll(b, rows, cols, l2), b0, jac=True, method="L-BFGS-B")
    return res.x


def per_race_ll(beta, rows, cols):
    out = []
    for r in rows:
        feats = np.column_stack([np.log(r["q"])] + [r["X"][:, c] for c in cols]) if cols else np.log(r["q"])[:, None]
        u = feats @ beta; u -= u.max()
        p = np.exp(u); p /= p.sum()
        out.append(np.log(p[r["y"]] + 1e-12))
    return np.array(out)


def main(directory="data/history", out=None):
    out = out or f"data/backtest/signals_{BASE}.json"
    races = build_dataset(Path(directory))
    dates = sorted({r["date"] for r in races})
    cut = dates[int(len(dates) * 0.6)]
    train = [r for r in races if r["date"] < cut]
    test = [r for r in races if r["date"] >= cut]
    print(f"{len(races)} courses ({len(train)} apprentissage < {cut}, {len(test)} test)")
    base = fit(train, [])
    base_ll = per_race_ll(base, test, [])
    rng = np.random.default_rng(42)
    rows_out = []
    for i, f in enumerate(FEATURES):
        b = fit(train, [i])
        d = per_race_ll(b, test, [i]) - base_ll
        boots = [rng.choice(d, len(d)).mean() for _ in range(1000)]
        lo, hi = np.percentile(boots, [2.5, 97.5])
        rows_out.append({"signal": f, "coef": round(float(b[1]), 4), "gain_ll_par_course": round(float(d.mean()), 5),
                         "ic95": [round(float(lo), 5), round(float(hi), 5)], "utile": bool(lo > 0)})
    rows_out.sort(key=lambda r: -r["gain_ll_par_course"])
    useful = [i for i, f in enumerate(FEATURES) if next(r for r in rows_out if r["signal"] == f)["utile"]]
    full = fit(train, list(range(len(FEATURES))), l2=5.0)
    d_full = per_race_ll(full, test, list(range(len(FEATURES)))) - base_ll
    sel = fit(train, useful, l2=2.0) if useful else base
    d_sel = per_race_ll(sel, test, useful) - base_ll if useful else np.zeros(len(test))
    top = lambda b, cols: float(np.mean([np.argmax(np.column_stack([np.log(r["q"])] + [r["X"][:, c] for c in cols]) @ b if cols else np.log(r["q"])) == r["y"] for r in test]))
    report = {
        "courses": len(races), "coupure": cut, "apprentissage": len(train), "test": len(test),
        "cote_seule": {"coef_log_cote": round(float(base[0]), 3), "ll_par_course": round(float(base_ll.mean()), 4),
                       "gagnant_en_tete": round(top(base, []), 4)},
        "signaux_un_a_un": rows_out,
        "tous_ensemble": {"gain_ll_par_course": round(float(d_full.mean()), 5), "gagnant_en_tete": round(top(full, list(range(len(FEATURES)))), 4)},
        "signaux_utiles_seuls": {"signaux": [FEATURES[i] for i in useful], "gain_ll_par_course": round(float(d_sel.mean()), 5)},
    }
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    r = main()
    print(json.dumps({k: r[k] for k in ("cote_seule", "tous_ensemble", "signaux_utiles_seuls")}, ensure_ascii=False, indent=1))
    for s in r["signaux_un_a_un"]:
        print(f"{s['signal']:22} gain {s['gain_ll_par_course']:+.5f}  IC95 [{s['ic95'][0]:+.5f},{s['ic95'][1]:+.5f}]  coef {s['coef']:+.3f} {'UTILE' if s['utile'] else ''}")
