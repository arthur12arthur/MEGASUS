"""Lecture d'un rapport (enregistrement JSON OU texte collé) pour le moteur de décision.

Le texte collé est celui que Hyperion envoie sur Telegram/e-mail : on y lit la liste
« Probabilité implicite du marché » (tous les partants) ou, à défaut, les lignes de
sélection avec leur cote. Rien n'est inventé : si aucune cote n'est lisible, on le dit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_IMPLIED = re.compile(r"^\s*[·•\-]?\s*n°(?P<num>\d+)\s*[·\-]\s*(?P<name>.+?)\s*[—–-]\s*(?P<pct>\d+(?:[.,]\d+)?)\s*%\s*$")
_PICK = re.compile(r"n°(?P<num>\d+)\s+(?P<name>.+?)\s+—\s+cote\s+(?P<odds>\d+(?:[.,]\d+)?)")


class ReportUnreadable(ValueError):
    """Aucune cote exploitable dans le rapport fourni."""


def latest_run(root: Path = Path("data/runs")) -> Path:
    files = sorted(Path(root).rglob("*.json"))
    if not files:
        raise ReportUnreadable(f"aucun rapport dans {root}")
    return files[-1]


def read_record(path: Path | str) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Retourne (partants avec cote, source, méta). Cote directe PMU > cote du PDF."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    source = "cote du PDF LONAB (relevé du matin)"
    for sig in (data.get("marketwatch") or {}).get("signals", []):
        odds = sig.get("odds_latest") or sig.get("odds_pdf")
        if sig.get("odds_latest"):
            source = "cote directe PMU"
        if odds and odds > 1.0:
            rows.append({"horse_id": sig.get("horse_id"), "number": sig.get("number"),
                         "name": sig.get("name"), "odds": float(odds)})
    if not rows:
        raise ReportUnreadable("le rapport ne contient aucune cote")
    meta = {"race_id": data.get("race_id"), "date": data.get("date"),
            "ranking": [r.get("horse_id") for r in (data.get("classement") or [])]}
    return rows, source, meta


def read_text(text: str) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Lit un rapport collé. Priorité : liste des probabilités implicites (tous les partants)."""
    rows = []
    for line in text.splitlines():
        m = _IMPLIED.match(line)
        if m:
            pct = float(m["pct"].replace(",", "."))
            if pct > 0:
                rows.append({"horse_id": f"n{m['num']}", "number": int(m["num"]),
                             "name": m["name"].strip(), "odds": 100.0 / pct})
    if len(rows) >= 4:
        return rows, "probabilités implicites du rapport collé", {}
    rows = []
    for line in text.splitlines():
        m = _PICK.search(line)
        if m:
            rows.append({"horse_id": f"n{m['num']}", "number": int(m["num"]), "name": m["name"].strip(),
                         "odds": float(m["odds"].replace(",", "."))})
    if len(rows) >= 4:
        return rows, "sélection du rapport collé (champ partiel)", {}
    raise ReportUnreadable("aucune liste de cotes ou de probabilités lisible dans ce texte")


__all__ = ["ReportUnreadable", "latest_run", "read_record", "read_text"]
