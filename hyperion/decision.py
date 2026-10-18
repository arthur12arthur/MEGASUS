"""Moteur de décision fondé sur la cote (probabilités calibrées) — pas sur un avantage supposé.

Principe : la cote finale contient déjà l'information publique (backtest sur 2 054 courses
réelles). Le moteur en tire des probabilités calibrées, mesure l'OUVERTURE de la course
(dispersion des cotes) et chiffre ce que vaut une sélection : probabilité d'inclure le
gagnant, nombre attendu de bons chevaux, probabilité d'en avoir au moins k.
Il ne promet aucun gain : il quantifie le risque.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

PARAMS_PATH = Path(__file__).with_name("decision_params.json")
DEFAULT_PARAMS = {"beta": 1.0, "gamma": 1.0, "n_eff_seuils": [5.5, 8.0], "n_sim": 20000, "seed": 42}


def load_params(path: Path = PARAMS_PATH) -> dict:
    try:
        return {**DEFAULT_PARAMS, **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULT_PARAMS)


def implied(odds: Sequence[float]) -> np.ndarray:
    """Probabilités implicites normalisées (retire la marge de l'opérateur)."""
    inv = 1.0 / np.asarray(odds, float)
    return inv / inv.sum()


def calibrate(q: np.ndarray, beta: float) -> np.ndarray:
    """p ∝ q^beta : corrige le biais favori/outsider mesuré sur l'historique."""
    w = np.power(q, beta)
    return w / w.sum()


def simulate_orders(p: np.ndarray, n_sim: int = 20000, seed: int = 42, gamma: float = 1.0) -> np.ndarray:
    """Ordres d'arrivée simulés, forme (n_sim, n).

    Le gagnant est tiré selon ``p`` (bien calibrée). Les places suivantes sont tirées avec
    des poids ``p**gamma`` : gamma < 1 aplatit le classement derrière le gagnant, ce que
    l'historique impose (Plackett-Luce pur surestime les favoris pour les places).
    """
    rng = np.random.default_rng(seed)
    n = len(p)
    winner = np.argmax(np.log(p)[None, :] - np.log(-np.log(rng.random((n_sim, n)))), axis=1)
    rest = gamma * np.log(p)[None, :] - np.log(-np.log(rng.random((n_sim, n))))
    rest[np.arange(n_sim), winner] = np.inf
    return np.argsort(-rest, axis=1)


def place_probabilities(p: np.ndarray, n_sim: int = 20000, seed: int = 42, ks=(1, 3, 5), gamma: float = 1.0) -> dict[int, np.ndarray]:
    orders = simulate_orders(p, n_sim, seed, gamma)
    out = {}
    for k in ks:
        top = orders[:, : min(k, len(p))]
        counts = np.bincount(top.ravel(), minlength=len(p))
        out[k] = counts / n_sim
    return out


def openness(p: np.ndarray) -> dict[str, float]:
    srt = np.sort(p)[::-1]
    return {
        "n_eff": float(1.0 / np.sum(p**2)),
        "entropie_norm": float(-(p * np.log(p)).sum() / math.log(len(p))),
        "p_favori": float(srt[0]),
        "ratio_1_2": float(srt[0] / srt[1]) if len(srt) > 1 else 1.0,
    }


def openness_class(n_eff: float, seuils: Sequence[float]) -> str:
    if n_eff <= seuils[0]:
        return "lisible"
    if n_eff >= seuils[1]:
        return "ouverte"
    return "intermédiaire"


@dataclass
class Pick:
    horse_id: str
    number: int | None
    name: str
    odds: float
    p_win: float
    p_top3: float
    p_top5: float
    system_rank: int | None = None


@dataclass
class Decision:
    picks: list[Pick]
    ouverture: dict[str, float]
    classe: str
    p_gagnant_dans_selection: float
    nb_attendu_dans_top5: float
    p_au_moins: dict[int, float]
    cote_source: str
    cote_horodatage: str | None = None
    avertissements: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "classe": self.classe,
            "ouverture": {k: round(v, 4) for k, v in self.ouverture.items()},
            "p_gagnant_dans_selection": round(self.p_gagnant_dans_selection, 4),
            "nb_attendu_dans_top5": round(self.nb_attendu_dans_top5, 3),
            "p_au_moins": {str(k): round(v, 4) for k, v in self.p_au_moins.items()},
            "cote_source": self.cote_source,
            "cote_horodatage": self.cote_horodatage,
            "avertissements": list(self.avertissements),
            "selection": [
                {"numero": x.number, "cheval": x.name, "cote": x.odds, "p_gagne": round(x.p_win, 4),
                 "p_top3": round(x.p_top3, 4), "p_top5": round(x.p_top5, 4), "rang_systeme": x.system_rank}
                for x in self.picks
            ],
        }


def decide(
    runners: Sequence[Mapping],
    *,
    selection_size: int = 5,
    system_ranking: Sequence[str] | None = None,
    cote_source: str = "cote",
    cote_horodatage: str | None = None,
    params: Mapping | None = None,
) -> Decision:
    """``runners`` : dicts {horse_id, number, name, odds}. Sélection = les mieux placés par p calibrée."""
    prm = {**load_params(), **(params or {})}
    rows = [r for r in runners if r.get("odds") and r["odds"] > 1.0]
    if len(rows) < 4:
        raise ValueError("moins de 4 chevaux avec une cote exploitable")
    odds = [r["odds"] for r in rows]
    p = calibrate(implied(odds), prm["beta"])
    orders = simulate_orders(p, prm["n_sim"], prm["seed"], prm["gamma"])
    pl = {k: np.bincount(orders[:, :min(k, len(p))].ravel(), minlength=len(p)) / prm["n_sim"] for k in (1, 3, 5)}
    chosen = list(np.argsort(-p)[:selection_size])
    top5 = orders[:, : min(5, len(p))]
    hits = np.isin(top5, chosen).sum(axis=1)
    winner_in = np.isin(orders[:, 0], chosen).mean()
    k_max = min(selection_size, 5)
    p_at_least = {k: float((hits >= k).mean()) for k in range(1, k_max + 1)}
    rank = {hid: i + 1 for i, hid in enumerate(system_ranking or [])}
    op = openness(p)
    picks = [
        Pick(rows[i]["horse_id"], rows[i].get("number"), rows[i]["name"], rows[i]["odds"],
             float(p[i]), float(pl[3][i]), float(pl[5][i]), rank.get(rows[i]["horse_id"]))
        for i in chosen
    ]
    return Decision(
        picks=picks, ouverture=op, classe=openness_class(op["n_eff"], prm["n_eff_seuils"]),
        p_gagnant_dans_selection=float(winner_in), nb_attendu_dans_top5=float(hits.mean()),
        p_au_moins=p_at_least, cote_source=cote_source, cote_horodatage=cote_horodatage,
    )


def _hhmm(stamp: str | None) -> str | None:
    if not stamp:
        return None
    try:
        import datetime as _dt

        return "relevée à " + _dt.datetime.fromisoformat(stamp).strftime("%Hh%M UTC")
    except ValueError:
        return stamp


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def render(decision: "Decision", params: Mapping | None = None) -> str:
    """Bloc de rapport en français, court (lisible sur mobile)."""
    prm = {**load_params(), **(params or {})}
    op = decision.ouverture
    ref = (prm.get("reference_classes") or {}).get(decision.classe)
    lines = [
        "DÉCISION — fondée sur la cote, pas sur un avantage supposé",
        f"Source : {decision.cote_source}" + (f" ({_hhmm(decision.cote_horodatage)})" if decision.cote_horodatage else ""),
        "",
        f"Course {decision.classe.upper()} — équivalent de {op['n_eff']:.1f} chevaux à armes égales ; "
        f"favori {_pct(op['p_favori'])}",
    ]
    if ref:
        lines.append(
            f"Sur {ref['n']} courses réelles de ce type : le gagnant était dans les 5 premiers de la cote "
            f"{_pct(ref['gagnant_dans_top5'])} du temps ; au moins 3 bons sur 5 : {_pct(ref['au_moins_3_bons'])}."
        )
    lines += ["", "Sélection (5 meilleures probabilités calibrées) :"]
    for i, x in enumerate(decision.picks, 1):
        sys_tag = f" · système #{x.system_rank}" if x.system_rank else ""
        num = f"n°{x.number} " if x.number is not None else ""
        lines.append(
            f"  {i}. {num}{x.name} — cote {x.odds:g} · gagne {_pct(x.p_win)} · "
            f"top 3 {_pct(x.p_top3)} · top 5 {_pct(x.p_top5)}{sys_tag}"
        )
    lines += [
        "",
        f"Cette sélection : gagnant inclus {_pct(decision.p_gagnant_dans_selection)} · "
        f"bons chevaux attendus {decision.nb_attendu_dans_top5:.1f}/5 · "
        f"au moins 3 bons : {_pct(decision.p_au_moins.get(3, 0.0))}",
        "",
        "Les cotes évoluent jusqu'au départ : l'analyse la plus fiable est la plus proche de la clôture. "
        "Ce moteur mesure un risque, il ne promet aucun gain.",
    ]
    lines.extend(f"⚠ {w}" for w in decision.avertissements)
    return "\n".join(lines)


__all__ = ["render", "decide", "Decision", "Pick", "implied", "calibrate", "place_probabilities", "openness", "load_params"]
