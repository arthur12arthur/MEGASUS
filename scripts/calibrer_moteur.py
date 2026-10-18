"""Calibre et VALIDE le moteur de décision sur l'historique réel (coupure temporelle 60/40)."""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
from scipy.optimize import minimize_scalar
GRID = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hyperion.decision import calibrate, implied, openness, place_probabilities  # noqa: E402
from hyperion.evaluation.realbacktest import actual_order, iter_history  # noqa: E402


def load(directory="data/history"):
    races = []
    for rec in sorted(iter_history(Path(directory)), key=lambda r: (r["date"], r["reunion"], r["course"])):
        parts = [p for p in rec["participants"] if str(p.get("statut", "PARTANT")).upper() == "PARTANT" and p.get("cote_direct")]
        truth = actual_order(rec)
        if len(parts) < 6 or len(truth) < 5 or rec["collecte"]["musique_contaminee"]:
            continue
        ids = [f"n{p['num']}" for p in parts]
        if truth[0] not in ids:
            continue
        pos = {h: i for i, h in enumerate(truth)}
        rank = np.array([pos.get(h, 99) for h in ids])  # 0 = gagnant
        races.append({"date": rec["date"], "q": implied([p["cote_direct"] for p in parts]), "rank": rank,
                      "quinte": bool(rec.get("is_quinte")), "disc": rec.get("discipline")})
    return races


def ll(beta, races):
    t = 0.0
    for r in races:
        p = calibrate(r["q"], beta)
        t -= np.log(p[int(np.argmin(r["rank"]))])
    return t / len(races)


def main():
    races = load()
    dates = sorted({r["date"] for r in races}); cut = dates[int(len(dates) * 0.6)]
    train = [r for r in races if r["date"] < cut]; test = [r for r in races if r["date"] >= cut]
    fit = minimize_scalar(lambda b: ll(b, train), bounds=(0.5, 1.5), method="bounded").x
    def brier_places(gamma, rows, beta):
        tot = 0.0
        for r in rows:
            pk = place_probabilities(calibrate(r["q"], beta), 1500, 3, ks=(3, 5), gamma=gamma)
            tot += sum(((pk[k] - (r["rank"] < k)) ** 2).mean() for k in (3, 5))
        return tot / len(rows)

    gamma = min(GRID, key=lambda g: brier_places(g, train[::2], fit))
    out = {"gamma_train": gamma, "courses": len(races), "train": len(train), "test": len(test), "coupure": cut, "beta_train": round(float(fit), 4),
           "ll_test_brut": round(ll(1.0, test), 5), "ll_test_calibre": round(ll(fit, test), 5)}
    # fiabilité des probabilités de victoire / top3 / top5 (test)
    rel = {}
    for k in (1, 3, 5):
        pred, obs = [], []
        for r in test:
            p = calibrate(r["q"], fit)
            pk = place_probabilities(p, 4000, 7, ks=(k,), gamma=gamma)[k]
            pred.extend(pk); obs.extend((r["rank"] < k).astype(float))
        pred, obs = np.array(pred), np.array(obs)
        bins = np.quantile(pred, np.linspace(0, 1, 6)); idx = np.clip(np.digitize(pred, bins[1:-1]), 0, 4)
        rel[f"top{k}"] = {"brier": round(float(((pred - obs) ** 2).mean()), 5),
                          "brier_naif": round(float(((obs.mean() - obs) ** 2).mean()), 5),
                          "tranches": [{"pred": round(float(pred[idx == b].mean()), 3), "obs": round(float(obs[idx == b].mean()), 3), "n": int((idx == b).sum())} for b in range(5)]}
    out["fiabilite"] = rel
    # indicateur d'ouverture : seuils = terciles sur l'apprentissage, validé sur le test
    neff = lambda r: openness(calibrate(r["q"], fit))["n_eff"]
    t1, t2 = np.quantile([neff(r) for r in train], [1 / 3, 2 / 3])
    cls = {"lisible": [], "intermédiaire": [], "ouverte": []}
    for r in test:
        n = neff(r); c = "lisible" if n <= t1 else ("ouverte" if n >= t2 else "intermédiaire")
        p = calibrate(r["q"], fit); order = np.argsort(-p); top5 = order[:5]
        cls[c].append({"fav": int(r["rank"][order[0]] == 0), "gagnant_top5": int((r["rank"][top5] == 0).any()),
                       "bons_top5": int((r["rank"][top5] < 5).sum()), "au_moins3": int((r["rank"][top5] < 5).sum() >= 3)})
    out["seuils_n_eff"] = [round(float(t1), 2), round(float(t2), 2)]
    out["par_classe_test"] = {c: {"n": len(v), "favori_gagne": round(np.mean([x["fav"] for x in v]), 3),
                                  "gagnant_dans_top5": round(np.mean([x["gagnant_top5"] for x in v]), 3),
                                  "bons_dans_top5": round(np.mean([x["bons_top5"] for x in v]), 2),
                                  "au_moins_3_bons": round(np.mean([x["au_moins3"] for x in v]), 3)} for c, v in cls.items()}
    # paramètres finaux : ajustés sur TOUT l'historique
    beta_all = minimize_scalar(lambda b: ll(b, races), bounds=(0.5, 1.5), method="bounded").x
    t1a, t2a = np.quantile([openness(calibrate(r["q"], beta_all))["n_eff"] for r in races], [1 / 3, 2 / 3])
    gamma_all = min(GRID, key=lambda g: brier_places(g, races[::3], beta_all))
    refc = {c: [] for c in ("lisible", "intermédiaire", "ouverte")}
    for r in races:
        pp = calibrate(r["q"], beta_all); nn = openness(pp)["n_eff"]
        c = "lisible" if nn <= t1a else ("ouverte" if nn >= t2a else "intermédiaire")
        top5 = np.argsort(-pp)[:5]
        refc[c].append(((r["rank"][top5] == 0).any(), (r["rank"][top5] < 5).sum() >= 3))
    reference_classes = {c: {"n": len(v), "gagnant_dans_top5": round(float(np.mean([a for a, _ in v])), 3),
                             "au_moins_3_bons": round(float(np.mean([b for _, b in v])), 3)} for c, v in refc.items()}
    Path("hyperion/decision_params.json").write_text(json.dumps({"reference_classes": reference_classes, "beta": round(float(beta_all), 4), "gamma": gamma_all, "n_eff_seuils": [round(float(t1a), 2), round(float(t2a), 2)], "courses_calibrage": len(races)}, indent=1))
    Path("data/backtest/moteur_validation.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
