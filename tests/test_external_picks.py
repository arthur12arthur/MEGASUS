"""Tests des pronostics externes : extraction par étiquettes + secours Gemini."""

from __future__ import annotations

import requests

from hyperion.config import Settings
from hyperion.external import HttpPanelProvider
from hyperion.external_picks import extract_labeled_picks, gemini_web_picks, strip_html


def _ids(race, numbers):
    by = {h.number: h.horse_id for h in race.runners}
    return [by[n] for n in numbers]


class TestEtiquettes:
    def test_belles_chances_dans_l_ordre(self, simple_race):
        page = "<p>Bases : 4</p><p>Belles chances : 1 - 3 - 2 - 5</p><p>Délaissés : 6</p>"
        ranking, note = extract_labeled_picks(page, simple_race)
        assert ranking == _ids(simple_race, [4, 1, 3, 2, 5])
        assert "étiqueté" in note

    def test_delaisses_exclus_et_outsiders_apres(self, simple_race):
        page = "Belles chances : 3 - 1 - 5 - 2 - 4 Outsiders : 6 Délaissés : 6"
        ranking, _ = extract_labeled_picks(page, simple_race)
        assert ranking == _ids(simple_race, [3, 1, 5, 2, 4, 6])

    def test_ordre_croissant_rejete(self, simple_race):
        assert extract_labeled_picks("Belles chances : 1 - 2 - 3 - 4 - 5", simple_race) is None

    def test_moins_de_cinq_numeros_rejete(self, simple_race):
        assert extract_labeled_picks("Bases : 3 - 1 - 2", simple_race) is None

    def test_numeros_hors_course_ignores(self, simple_race):
        assert extract_labeled_picks("Belles chances : 3 - 1 - 12 - 15 - 16", simple_race) is None

    def test_sans_etiquette_rien(self, simple_race):
        assert extract_labeled_picks("Tonnerre de Mai Éclair du Sahel 4 1 3 2 5", simple_race) is None

    def test_strip_html(self):
        assert strip_html("<script>x=1</script><b>Bases</b>&nbsp;: 3") == "Bases : 3"


class _Resp:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status


class TestFournisseurHttp:
    def _page(self, race, labels):
        names = " ".join(h.name for h in race.runners)
        return f"<html><body><p>{names}</p>{labels}</body></html>"

    def test_page_etiquetee_devient_exploitable(self, simple_race, monkeypatch):
        page = self._page(simple_race, "<p>Belles chances : 4 - 1 - 3 - 2 - 5</p>")
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(page))
        picks = HttpPanelProvider({}, gemini_fallback=False).fetch(simple_race, panel=("Equidia",))
        assert picks[0].usable and picks[0].ranking == _ids(simple_race, [4, 1, 3, 2, 5])

    def test_page_sans_etiquette_reste_rejetee(self, simple_race, monkeypatch):
        page = self._page(simple_race, "")
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(page))
        picks = HttpPanelProvider({}, gemini_fallback=False).fetch(simple_race, panel=("Equidia",))
        assert not picks[0].usable

    def test_etiquettes_ignorees_si_la_page_ne_parle_pas_de_la_course(self, simple_race, monkeypatch):
        page = "<html>Belles chances : 4 - 1 - 3 - 2 - 5</html>"
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(page))
        picks = HttpPanelProvider({}, gemini_fallback=False).fetch(simple_race, panel=("Equidia",))
        assert not picks[0].usable


class _Post:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def __call__(self, url, **kwargs):
        outer = self

        class R:
            status_code = outer.status

            def json(self_inner):
                return outer.payload

        return R()


def _gemini_body(sources_json, titles):
    return {
        "candidates": [{
            "content": {"parts": [{"text": sources_json}]},
            "groundingMetadata": {"groundingChunks": [{"web": {"title": t, "uri": "https://x/" + t}} for t in titles]},
        }]
    }


class TestSecoursGemini:
    def _settings(self, keys=("k",)):
        s = Settings()
        object.__setattr__(s, "gemini_keys", keys) if s.__dataclass_params__.frozen else setattr(s, "gemini_keys", keys)
        return s

    def test_source_etayee_acceptee(self, simple_race):
        body = _gemini_body('{"sources": {"Equidia": [4, 1, 3, 2, 5]}}', ["equidia.fr"])
        found = gemini_web_picks(simple_race, ["Equidia"], self._settings(), post=_Post(body))
        assert found["Equidia"][0] == _ids(simple_race, [4, 1, 3, 2, 5])
        assert "equidia.fr" in found["Equidia"][1]

    def test_source_non_etayee_rejetee(self, simple_race):
        body = _gemini_body('{"sources": {"Equidia": [4, 1, 3, 2, 5]}}', ["autresite.com"])
        assert gemini_web_picks(simple_race, ["Equidia"], self._settings(), post=_Post(body)) == {}

    def test_liste_croissante_rejetee(self, simple_race):
        body = _gemini_body('{"sources": {"Equidia": [1, 2, 3, 4, 5]}}', ["equidia.fr"])
        assert gemini_web_picks(simple_race, ["Equidia"], self._settings(), post=_Post(body)) == {}

    def test_sans_cle_rien(self, simple_race):
        assert gemini_web_picks(simple_race, ["Equidia"], self._settings(()), post=_Post({})) == {}

    def test_integration_fournisseur(self, simple_race, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp("", status=202))
        body = _gemini_body('{"sources": {"Equidia": [4, 1, 3, 2, 5]}}', ["equidia.fr"])
        provider = HttpPanelProvider({}, settings=self._settings(), gemini_post=_Post(body), max_retries=0)
        picks = {p.source: p for p in provider.fetch(simple_race, panel=("Equidia", "Genybet"))}
        assert picks["Equidia"].usable and picks["Equidia"].ranking == _ids(simple_race, [4, 1, 3, 2, 5])
        assert not picks["Genybet"].usable
