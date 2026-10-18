"""Collecte d'historique réel (fixtures = vraies réponses de l'API PMU du 17/10/2026)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from hyperion.history import (
    collect_day,
    contamination_ratio,
    day_path,
    list_courses,
    reduce_participant,
)

FIX = Path(__file__).parent / "fixtures"
PROGRAMME = json.loads((FIX / "pmu_programme_20261017_trim.json").read_text(encoding="utf-8"))
PARTICIPANTS = json.loads((FIX / "pmu_participants_20261017_R1C1.json").read_text(encoding="utf-8"))


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/participants"):
            return _Resp(PARTICIPANTS)
        return _Resp(PROGRAMME)


def test_programme_liste_les_courses_terminees_avec_meta():
    refs = list_courses(PROGRAMME, "all")
    assert refs and all(r["arrivee"] for r in refs)
    c3 = next(r for r in refs if r["course"] == 3)
    assert c3["hippodrome"] == "AUTEUIL" and c3["discipline"] and c3["distance"]
    assert c3["is_quinte"] is True
    assert c3["terrain"] and c3["terrain"]["intitule"]


def test_scope_quinte_ne_garde_que_la_quinte():
    refs = list_courses(PROGRAMME, "quinte")
    assert refs and all(r["is_quinte"] for r in refs)


def test_participant_reduit_sans_champ_inutile():
    item = PARTICIPANTS["participants"][0]
    red = reduce_participant(item)
    assert red["num"] == 1 and "musique" in red and "cote_direct" in red
    assert "commentaireApresCourse" not in json.dumps(red)


def test_contamination_detectee():
    clean = [{"ordre_arrivee": r, "musique": f"{(r % 7) + 2}p1p"} for r in range(1, 9)]
    dirty = [{"ordre_arrivee": r, "musique": f"{r}p1p"} for r in range(1, 9)]
    assert contamination_ratio(dirty) == 1.0
    assert contamination_ratio(clean) < 0.5
    assert contamination_ratio([]) is None


def test_collect_day_ecrit_un_fichier_et_reste_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("hyperion.history.time.sleep", lambda s: None)
    session = _Session()
    n = collect_day(dt.date(2026, 10, 17), tmp_path, session, scope="quinte", delay_s=0)
    assert n >= 1
    saved = json.loads(day_path(tmp_path, dt.date(2026, 10, 17)).read_text(encoding="utf-8"))
    course = saved["courses"][0]
    assert course["participants"] and course["arrivee"]
    assert "musique_contaminee" in course["collecte"]
    calls = len(session.calls)
    assert collect_day(dt.date(2026, 10, 17), tmp_path, session, scope="quinte") == 0
    assert len(session.calls) == calls  # déjà collecté : aucun appel réseau
