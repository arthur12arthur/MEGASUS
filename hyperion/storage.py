"""Module 1.11 — Storage.

Conserve les sorties du pipeline pour permettre l'évaluation du soir (1.13)
et les backtests. Stockage en JSON local, commité en Git : gratuit,
versionné, et lisible par n'importe quel outil.

Organisation : ``data/runs/AAAA/MM/JJ/<race_id>.json``

Alternative légère envisagée pour V11 si Firebase reste jugé trop coûteux à
dupliquer : un unique fichier JSONL par mois, plus simple à committer.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from hyperion.config import Settings


class JsonStore:
    """Stockage JSON versionné par course et par date."""

    def __init__(self, runs_dir: Path | None = None, settings: Settings | None = None) -> None:
        settings = settings or Settings()
        self.runs_dir = Path(runs_dir or settings.runs_dir)

    # -- chemins -----------------------------------------------------------

    def path_for(self, race_id: str, date: dt.date) -> Path:
        return self.runs_dir / f"{date:%Y}" / f"{date:%m}" / f"{date:%d}" / f"{race_id}.json"

    def save(self, record: Mapping[str, Any]) -> Path:
        race_id = str(record.get("race_id") or "course")
        date = self._date_of(record)
        path = self.path_for(race_id, date)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(record)
        payload.setdefault("race_id", race_id)
        payload.setdefault("date", date.isoformat())
        payload["stored_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    def load(self, race_id: str, date: dt.date) -> dict[str, Any] | None:
        path = self.path_for(race_id, date)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def records_for(self, date: dt.date) -> list[dict[str, Any]]:
        directory = self.runs_dir / f"{date:%Y}" / f"{date:%m}" / f"{date:%d}"
        if not directory.exists():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return out

    def all_records(self) -> list[dict[str, Any]]:
        if not self.runs_dir.exists():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(self.runs_dir.rglob("*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return out

    def save_result(self, result: Any) -> Path:
        """Enregistre un résultat officiel du soir (module 1.13)."""
        record = result.as_dict() if hasattr(result, "as_dict") else dict(result)
        return self.save({**record, "kind": "official_result"})

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _date_of(record: Mapping[str, Any]) -> dt.date:
        raw = record.get("date")
        if isinstance(raw, dt.date):
            return raw
        if isinstance(raw, str) and raw:
            try:
                return dt.date.fromisoformat(raw)
            except ValueError:
                pass
        meta = record.get("meta") or {}
        if isinstance(meta, Mapping):
            raw_meta = meta.get("date")
            if isinstance(raw_meta, str) and raw_meta:
                try:
                    return dt.date.fromisoformat(raw_meta)
                except ValueError:
                    pass
        return dt.datetime.now(dt.timezone.utc).date()


def load_sample(path: Path) -> dict[str, Any]:
    """Charge un fichier d'exemple (course + éventuel résultat du soir)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iter_dates(records: Iterable[Mapping[str, Any]]) -> list[dt.date]:
    out: list[dt.date] = []
    for record in records:
        raw = record.get("date")
        if isinstance(raw, dt.date):
            out.append(raw)
        elif isinstance(raw, str) and raw:
            try:
                out.append(dt.date.fromisoformat(raw))
            except ValueError:
                continue
    return sorted(set(out))


__all__ = ["JsonStore", "load_sample", "iter_dates"]
