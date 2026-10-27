"""Mode vivant : relevés réguliers, alertes seulement si l'ordre change, trace enregistrée."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from hyperion.live import detect, final_message, run_watch, snapshot, stable_order

FIX = Path(__file__).parent / "fixtures"
PROGRAMME = json.loads((FIX / "pmu_programme_20261017_trim.json").read_text(encoding="utf-8"))
COURSE3 = next(c for c in PROGRAMME["programme"]["reunions"][0]["courses"] if c["numOrdre"] == 3)
DEPART = dt.datetime.fromtimestamp(COURSE3["heureDepart"] / 1000, dt.timezone.utc)
UTC = dt.timezone.utc


def payload(odds: dict[int, float], non_runners=()):
    return {"participants": [
        {"numPmu": n, "nom": f"CHEVAL {n}", "statut": "NON_PARTANT" if n in non_runners else "PARTANT",
         "dernierRapportDirect": {"rapport": o}} for n, o in odds.items()]}


class _Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        if isinstance(self.data, Exception):
            raise self.data

    def json(self):
        return self.data


class _Session:
    def __init__(self, payloads):
        self.payloads, self.i = list(payloads), 0

    def get(self, url, **kw):
        if url.endswith("/participants"):
            data = self.payloads[min(self.i, len(self.payloads) - 1)]
            self.i += 1
            return _Resp(data)
        return _Resp(PROGRAMME)


class _Clock:
    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += dt.timedelta(seconds=s)


RECORD = {"kind": "analysis", "race_id": "20261017-auteuil-c3", "date": "2026-10-17",
          "meta": {"hippodrome": "AUTEUIL", "race_number": 3, "name": COURSE3["libelle"], "date": "2026-10-17"},
          "classement": [{"horse_id": "a", "numero": 1}],
          "decision": {"classe": "intermédiaire", "selection": [{"numero": n} for n in (1, 2, 3, 4, 5)]}}
BASE = {1: 3.0, 2: 4.0, 3: 6.0, 4: 8.0, 5: 12.0, 6: 20.0, 7: 30.0, 8: 50.0}


def _run(payloads, minutes_before=60, tmp=None, **kw):
    clock = _Clock(DEPART - dt.timedelta(minutes=minutes_before))
    sent: list[str] = []
    status = run_watch(RECORD, _Session(payloads), now_fn=clock.now, sleep_fn=clock.sleep, send=sent.append,
                       store_root=tmp, log=lambda m: None, **kw)
    return status, sent


def test_hysteresis_ignore_le_bruit_et_applique_les_vrais_changements():
    p = {1: 0.300, 2: 0.295, 3: 0.2}
    assert stable_order([1, 2, 3], p) == [1, 2, 3]          # écart 0,5 point : on ne bouge pas
    assert stable_order([2, 1, 3], p) == [2, 1, 3]          # pas de va-et-vient
    assert stable_order([1, 2, 3], {1: 0.2, 2: 0.4, 3: 0.2}) == [2, 1, 3]  # vrai changement


def test_aucune_alerte_si_rien_ne_change(tmp_path):
    status, sent = _run([payload(BASE)], tmp=tmp_path)
    assert status["status"] == "ok" and status["alertes"] == 0
    assert len(sent) == 1 and sent[0].startswith("🏁") and "1 · 2 · 3 · 4 · 5" in sent[0]


def test_alerte_courte_avec_numeros_quand_l_ordre_change(tmp_path):
    moved = {**BASE, 5: 2.0}  # le 5 devient grand favori
    status, sent = _run([payload(BASE), payload(moved)], tmp=tmp_path)
    alertes = [m for m in sent if m.startswith("⚡")]
    assert status["alertes"] == 1 and len(alertes) == 1
    assert "nouvel ordre : 5 · 1 · 2 · 3 · 4" in alertes[0] and "(avant : 1 · 2 · 3 · 4 · 5)" in alertes[0]
    assert "cote n°5 12 → 2" in alertes[0]


def test_non_partant_declenche_une_alerte_immediate(tmp_path):
    status, sent = _run([payload(BASE), payload({k: v for k, v in BASE.items()}, non_runners=(2,))], tmp=tmp_path)
    assert any("NON-PARTANT n°2" in m for m in sent)


def test_trop_tard_pas_de_surveillance(tmp_path):
    status, sent = _run([payload(BASE)], minutes_before=5, tmp=tmp_path)
    assert status["status"] == "trop_tard" and sent == []


def test_trace_enregistree_a_chaque_passage(tmp_path):
    _run([payload(BASE)], tmp=tmp_path)
    saved = json.loads(next(Path(tmp_path).rglob("*.json")).read_text(encoding="utf-8"))
    assert saved["race_id"] == "20261017-auteuil-c3" and len(saved["passages"]) >= 2
    assert saved["passages"][0]["top5"] == [1, 2, 3, 4, 5]


def test_trois_echecs_consecutifs_arretent_proprement(tmp_path):
    boom = [ConnectionError("down")]

    class _Bad(_Session):
        def get(self, url, **kw):
            if url.endswith("/participants"):
                raise ConnectionError("down")
            return _Resp(PROGRAMME)

    clock = _Clock(DEPART - dt.timedelta(minutes=60))
    status = run_watch(RECORD, _Bad([]), now_fn=clock.now, sleep_fn=clock.sleep, send=lambda m: None,
                       store_root=tmp_path, log=lambda m: None)
    assert status["status"] == "interrompu"


def test_course_introuvable_est_ignoree():
    rec = dict(RECORD, meta=dict(RECORD["meta"], hippodrome="CHANTILLY"))
    clock = _Clock(DEPART - dt.timedelta(minutes=60))
    status = run_watch(rec, _Session([payload(BASE)]), now_fn=clock.now, sleep_fn=clock.sleep, send=lambda m: None,
                       log=lambda m: None)
    assert status["status"] == "ignore"
