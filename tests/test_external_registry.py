"""Tests du registre intégré (hyperion/external_registry.py)."""

from __future__ import annotations

import requests

from hyperion.external_registry import (
    SOURCE_REGISTRY,
    SourceEntry,
    discover_url,
    registry_by_name,
    slugify,
)


class TestRegistre:
    def test_douze_sources(self):
        assert len(SOURCE_REGISTRY) == 12

    def test_noms_uniques(self):
        names = [e.name for e in SOURCE_REGISTRY]
        assert len(names) == len(set(names))

    def test_cinq_sources_ont_une_url_directe(self):
        directes = [e for e in SOURCE_REGISTRY if e.direct_url is not None]
        assert len(directes) == 5

    def test_sept_sources_sans_url_directe_passent_par_la_decouverte(self):
        sans_directe = [e for e in SOURCE_REGISTRY if e.direct_url is None]
        assert len(sans_directe) == 7


class TestSlugify:
    def test_accents_et_espaces(self):
        assert slugify("Prix de la Vierge de Lorette") == "prix-de-la-vierge-de-lorette"

    def test_apostrophes(self):
        assert slugify("Koureas d'Ela") == "koureas-d-ela"


class TestUrlsDirectes:
    def test_equidia_stable(self, simple_race):
        entry = registry_by_name()["Equidia"]
        assert entry.direct_url(simple_race) == "https://www.equidia.fr/quinte"

    def test_canalturf_stable(self, simple_race):
        entry = registry_by_name()["Canal Turf"]
        assert entry.direct_url(simple_race) == "https://www.canalturf.com/courses_quinte.php"

    def test_turfomania_stable(self, simple_race):
        entry = registry_by_name()["Turfomania"]
        assert entry.direct_url(simple_race) == "https://www.turfomania.fr/quinte/"

    def test_genybet_stable(self, simple_race):
        entry = registry_by_name()["Genybet"]
        assert entry.direct_url(simple_race) == "https://www.geny.com/"

    def test_zeturf_construite_avec_le_nom_de_la_course(self, simple_race):
        entry = registry_by_name()["ZEturf"]
        simple_race.meta.name = "Prix de la Vierge de Lorette"
        url = entry.direct_url(simple_race)
        assert url is not None
        assert simple_race.meta.date.isoformat() in url
        assert "paris-vincennes" in url
        assert "prix-de-la-vierge-de-lorette" in url

    def test_zeturf_replie_sur_la_reunion_sans_nom_de_course(self, simple_race):
        entry = registry_by_name()["ZEturf"]
        simple_race.meta.name = None
        url = entry.direct_url(simple_race)
        assert url is not None
        assert "reunion-du-jour" in url

    def test_zeturf_sans_hippodrome_rend_aucune_url(self, simple_race):
        entry = registry_by_name()["ZEturf"]
        simple_race.meta.hippodrome = None
        assert entry.direct_url(simple_race) is None  # jamais d'URL devinée


class TestDecouverte:
    def test_trouve_un_resultat_sur_le_bon_domaine(self, simple_race, monkeypatch):
        html = (
            '<a class="result__a" href="https://www.pmu.fr/pronostic/vincennes">'
            "PMU — pronostic</a>"
        )

        def fake_get(url, params=None, **kwargs):
            return type("R", (), {"status_code": 200, "text": html})()

        monkeypatch.setattr(requests, "get", fake_get)
        entry = registry_by_name()["PMU"]
        url, note = discover_url(requests, entry, simple_race)
        assert url == "https://www.pmu.fr/pronostic/vincennes"
        assert "trouvé" in note

    def test_ignore_les_resultats_hors_domaine(self, simple_race, monkeypatch):
        html = (
            '<a class="result__a" href="https://autre-site.fr/page">Hors sujet</a>'
            '<a class="result__a" href="https://www.pmu.fr/vraie-page">La bonne page</a>'
        )

        def fake_get(url, params=None, **kwargs):
            return type("R", (), {"status_code": 200, "text": html})()

        monkeypatch.setattr(requests, "get", fake_get)
        entry = registry_by_name()["PMU"]
        url, _ = discover_url(requests, entry, simple_race)
        assert url == "https://www.pmu.fr/vraie-page"

    def test_aucun_resultat_sur_le_domaine_est_signale_sans_inventer_d_url(
        self, simple_race, monkeypatch
    ):
        html = '<a class="result__a" href="https://autre-site.fr/page">Hors sujet</a>'

        def fake_get(url, params=None, **kwargs):
            return type("R", (), {"status_code": 200, "text": html})()

        monkeypatch.setattr(requests, "get", fake_get)
        entry = registry_by_name()["PMU"]
        url, note = discover_url(requests, entry, simple_race)
        assert url is None
        assert "domaine" in note

    def test_domaine_non_confirme_recherche_non_restreinte(self, simple_race, monkeypatch):
        """Turf BZH et Quinté du Jour : le domaine n'a pas pu être vérifié,
        donc la recherche n'est jamais restreinte à une adresse devinée."""
        html = '<a class="result__a" href="https://un-site-quelconque.fr/page">Résultat</a>'
        captured = {}

        def fake_get(url, params=None, **kwargs):
            captured["q"] = params["q"]
            return type("R", (), {"status_code": 200, "text": html})()

        monkeypatch.setattr(requests, "get", fake_get)
        entry = registry_by_name()["Turf BZH"]
        assert entry.domain_confirmed is False
        url, note = discover_url(requests, entry, simple_race)
        assert "site:" not in captured["q"]
        assert url == "https://un-site-quelconque.fr/page"
        assert "non confirmé" not in note  # trouvé quand même, donc pas une note d'échec

    def test_sans_hippodrome_la_decouverte_ne_cherche_pas_a_l_aveugle(self, simple_race):
        simple_race.meta.hippodrome = None
        entry = registry_by_name()["PMU"]
        url, note = discover_url(requests, entry, simple_race)
        assert url is None
        assert "hippodrome" in note

    def test_requete_reseau_en_echec_est_rapportee_non_bloquante(self, simple_race, monkeypatch):
        def fake_get(*args, **kwargs):
            raise RuntimeError("timeout")

        monkeypatch.setattr(requests, "get", fake_get)
        entry = registry_by_name()["PMU"]
        url, note = discover_url(requests, entry, simple_race)
        assert url is None
        assert "impossible" in note

    def test_reponse_non_200_est_rapportee(self, simple_race, monkeypatch):
        def fake_get(*args, **kwargs):
            return type("R", (), {"status_code": 403, "text": ""})()

        monkeypatch.setattr(requests, "get", fake_get)
        entry = registry_by_name()["PMU"]
        url, note = discover_url(requests, entry, simple_race)
        assert url is None
        assert "403" in note
