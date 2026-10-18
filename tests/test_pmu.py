"""Musique PMU + enrichissement depuis l'API participants (fichier réel du 15/10/2026)."""

from __future__ import annotations

import json
from pathlib import Path

from hyperion.models import Horse, Race, RaceMeta
from hyperion.music import parse_music
from hyperion.pmu import enrich_race, extract_results, load_payload

FIXTURE = Path(__file__).parent / "fixtures" / "pmu_participants_20261015_R1C1.json"


def _race(payload) -> Race:
    horses = [
        Horse(horse_id=p["nom"].lower().replace(" ", "-"), name=p["nom"], number=p["numPmu"])
        for p in payload["participants"]
    ]
    return Race(meta=RaceMeta(), horses=horses)


class TestMusique:
    def test_marqueur_d_annee_n_est_pas_une_place(self):
        assert parse_music("3p2p4p4p2p(25)2p2p1p5p").places == (3, 2, 4, 4, 2, 2, 2, 1, 5)

    def test_zero_est_une_place_non_placee(self):
        assert parse_music("0p0p6p1p5p1p0p(25)7p0p").places == (0, 0, 6, 1, 5, 1, 0, 7, 0)

    def test_incidents_comptes_comme_non_places(self):
        m = parse_music("Dp1p(24)Ap3a")
        assert m.places == (0, 1, 0, 3) and m.incidents == 2

    def test_vide_et_ancien_format(self):
        assert parse_music(None).places == () and parse_music("").places == ()
        assert parse_music("1a2a3a").places == (1, 2, 3)


class TestEnrichissement:
    def test_les_16_partants_sont_apparies(self):
        payload = load_payload(FIXTURE)
        enriched, report = enrich_race(_race(payload), payload)
        assert report.matched == 16 and not report.unmatched_pmu
        assert [w for w in report.warnings if "déjà courue" not in w] == []
        h = enriched.by_id("sweet-chop")
        assert h.driver == "A.LEMAITRE" and h.runs == 26 and h.wins == 2
        assert h.gains == 138092.0 and h.weight_kg == 63.5
        assert h.recent_places[:3] == (3, 2, 4)

    def test_cote_directe_alimente_marketwatch(self):
        payload = load_payload(FIXTURE)
        enriched, _ = enrich_race(_race(payload), payload)
        h = enriched.by_id("sweet-chop")
        assert h.odds_latest == 5.2 and h.odds_pdf == 8.7
        assert h.raw["pmu"]["odds_reference_at"] < h.raw["pmu"]["odds_direct_at"]

    def test_aucun_champ_de_resultat_ne_fuit(self):
        payload = load_payload(FIXTURE)
        enriched, _ = enrich_race(_race(payload), payload)
        blob = json.dumps([h.as_dict() for h in enriched.horses], default=str)
        blob += json.dumps([dict(h.raw) for h in enriched.horses], default=str)
        assert "ordreArrivee" not in blob and "DATAHIPPIQUE" not in blob
        # le résultat n'est lisible que par la voie d'évaluation dédiée
        assert extract_results(payload)[12] == 1

    def test_les_champs_du_pdf_ne_sont_pas_ecrases(self):
        payload = load_payload(FIXTURE)
        race = _race(payload)
        race.horses[0].driver = "DRIVER PDF"
        race.horses[0].odds_pdf = 9.9
        enriched, _ = enrich_race(race, payload)
        assert enriched.horses[0].driver == "DRIVER PDF" and enriched.horses[0].odds_pdf == 9.9
        assert race.horses[0].odds_latest is None  # course d'origine intacte

    def test_nom_different_refuse_avec_avertissement(self):
        payload = load_payload(FIXTURE)
        race = _race(payload)
        race.horses[1].name = "AUTRE CHEVAL"
        enriched, report = enrich_race(race, payload)
        assert report.matched == 15 and any("ignoré" in w for w in report.warnings)
        assert enriched.horses[1].driver is None

    def test_non_partant_pmu_est_marque(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["participants"][2]["statut"] = "NON_PARTANT"
        enriched, _ = enrich_race(_race(payload), payload)
        assert enriched.horses[2].non_runner and len(enriched.runners) == 15


class _Resp:
    def __init__(self, payload=None, error=None):
        self._payload, self._error = payload, error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


class _Session:
    def __init__(self, resp=None, boom=None):
        self.resp, self.boom, self.calls = resp, boom, []

    def get(self, url, **kw):
        self.calls.append(url)
        if self.boom:
            raise self.boom
        return self.resp


def _race_with_meta(payload):
    import datetime as dt

    race = _race(payload)
    race.meta = RaceMeta(meeting="R1", race_number=1, date=dt.date(2026, 10, 15))
    return race


class TestIntegrationPmu:
    def test_telechargement_ok(self):
        from hyperion.pmu import enrich_from_pmu

        payload = load_payload(FIXTURE)
        session = _Session(_Resp(payload))
        race, status = enrich_from_pmu(_race_with_meta(payload), session=session)
        assert status["status"] == "ok" and status["matched"] == 16
        assert session.calls[0].endswith("/15102026/R1/C1/participants")
        assert race.horses[0].driver == "A.LEMAITRE"

    def test_panne_reseau_ne_leve_jamais(self):
        from hyperion.pmu import enrich_from_pmu

        payload = load_payload(FIXTURE)
        original = _race_with_meta(payload)
        race, status = enrich_from_pmu(original, session=_Session(boom=ConnectionError("down")))
        assert status["status"] == "indisponible" and race is original

    def test_reponse_invalide_ne_leve_jamais(self):
        from hyperion.pmu import enrich_from_pmu

        payload = load_payload(FIXTURE)
        _, status = enrich_from_pmu(
            _race_with_meta(payload), session=_Session(_Resp({"autre": 1}))
        )
        assert status["status"] == "indisponible"

    def test_sans_reunion_appel_impossible_et_trace(self):
        from hyperion.pmu import enrich_from_pmu

        payload = load_payload(FIXTURE)
        session = _Session(_Resp(payload))
        _, status = enrich_from_pmu(_race(payload), session=session)
        assert status["status"] == "ignore" and not session.calls

    def test_course_deja_courue_signalee(self):
        from hyperion.pmu import enrich_from_pmu

        payload = load_payload(FIXTURE)
        _, status = enrich_from_pmu(_race_with_meta(payload), payload)
        assert any("déjà courue" in w for w in status["warnings"])

    def test_pipeline_voit_enfin_un_mouvement_de_cote(self):
        from hyperion.config import Settings
        from hyperion.pipeline import run_pipeline

        payload = load_payload(FIXTURE)
        race, _ = enrich_race(_race_with_meta(payload), payload)
        result = run_pipeline(race, Settings())
        assert any(s.delta is not None for s in result.market.signals)
        assert "pmu" not in json.dumps(result.record["identification"])


def test_analyse_a_posteriori_jamais_stockee():
    from hyperion.cli import is_post_race
    from hyperion.pmu import enrich_from_pmu

    payload = load_payload(FIXTURE)
    _, status = enrich_from_pmu(_race_with_meta(payload), payload)
    assert is_post_race(status)
    assert not is_post_race({"status": "ok", "warnings": []})
    assert not is_post_race(None)


class TestResolutionReunion:
    def _programme(self):
        return json.loads(
            (Path(__file__).parent / "fixtures" / "pmu_programme_20261017_trim.json").read_text(encoding="utf-8")
        )

    def test_retrouve_reunion_et_course(self):
        from hyperion.pmu import resolve_race

        assert resolve_race(self._programme(), "AUTEUIL", 3) == (1, 3)

    def test_refuse_si_le_nom_ne_correspond_pas(self):
        from hyperion.pmu import resolve_race

        assert resolve_race(self._programme(), "AUTEUIL", 3, name="PRIX TOTALEMENT AUTRE") is None

    def test_hippodrome_inconnu_ou_course_absente(self):
        from hyperion.pmu import resolve_race

        assert resolve_race(self._programme(), "CHANTILLY", 3) is None
        assert resolve_race(self._programme(), "AUTEUIL", 99) is None

    def test_enrich_from_pmu_sans_reunion_utilise_le_programme(self):
        import datetime as dt

        from hyperion.pmu import enrich_from_pmu

        prog = self._programme()
        payload = json.loads(
            (Path(__file__).parent / "fixtures" / "pmu_participants_20261017_R1C1.json").read_text(encoding="utf-8")
        )

        class S(_Session):
            def get(self, url, **kw):
                self.calls.append(url)
                return _Resp(payload if url.endswith("/participants") else prog)

        race = _race(payload)
        race.meta = RaceMeta(hippodrome="AUTEUIL", race_number=3, date=dt.date(2026, 10, 17))
        session = S()
        _, status = enrich_from_pmu(race, session=session)
        assert session.calls[0].endswith("/programme/17102026")
        assert session.calls[1].endswith("/R1/C3/participants")
        assert status["resolu_par"] in ("programme_pmu",) or status["status"] != "ok"
