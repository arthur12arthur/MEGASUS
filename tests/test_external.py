"""Tests du module 1.9 — ExternalConsensus."""

from __future__ import annotations

from hyperion.external import (
    ExternalConsensus,
    HttpPanelProvider,
    ManualPanelProvider,
    SourcePick,
    extract_ranking,
    implicit_probabilities,
)


class TestExtraction:
    def test_ordre_d_apparition(self, simple_race):
        text = "Voici notre pronostic : Comète d'Afrique devant Tonnerre de Mai puis Sagesse Noire."
        ranking, coverage = extract_ranking(text, simple_race.runners)
        assert ranking[0] == "h4"
        assert ranking[1] == "h1"
        assert coverage > 0

    def test_texte_vide(self, simple_race):
        assert extract_ranking("", simple_race.runners) == ([], 0.0)

    def test_nom_trop_court_ignore(self, simple_race):
        short = simple_race.runners[0]
        short.name = "Ab"
        ranking, _ = extract_ranking("Ab Cd Ef", simple_race.runners)
        assert "h1" not in ranking

    def test_accents_et_casse(self, simple_race):
        text = "ECLAIR DU SAHEL est notre favori"
        ranking, _ = extract_ranking(text, simple_race.runners)
        assert ranking[0] == "h2"


class TestProbabilitesImplicites:
    def test_somme_a_un(self, simple_race):
        probabilities = implicit_probabilities(simple_race)
        assert abs(sum(probabilities.values()) - 1.0) < 1e-9

    def test_favori_a_la_plus_forte_probabilite(self, simple_race):
        probabilities = implicit_probabilities(simple_race)
        assert max(probabilities, key=lambda h: probabilities[h]) == "h1"

    def test_utilise_la_cote_fraiche(self, simple_race):
        simple_race.by_id("h2").odds_latest = 1.5
        probabilities = implicit_probabilities(simple_race)
        assert max(probabilities, key=lambda h: probabilities[h]) == "h2"

    def test_sans_aucune_cote(self, simple_race):
        for horse in simple_race.horses:
            horse.odds_pdf = None
            horse.odds_latest = None
        assert implicit_probabilities(simple_race) == {}


class TestManualPanelProvider:
    def test_payload_par_noms(self, simple_race, manual_panel):
        picks = ManualPanelProvider(manual_panel).fetch(simple_race)
        usable = [p for p in picks if p.usable]
        assert len(usable) == 3
        assert all(p.is_panel_member for p in usable)

    def test_payload_par_identifiants(self, simple_race):
        picks = ManualPanelProvider({"Genybet": ["h1", "h2", "h3", "h4", "h5", "h6"]}).fetch(
            simple_race
        )
        assert picks[0].usable

    def test_source_incomplete_non_utilisable(self, simple_race):
        picks = ManualPanelProvider({"Genybet": ["Tonnerre de Mai"]}).fetch(simple_race)
        assert not picks[0].usable

    def test_payload_vide(self, simple_race):
        assert ManualPanelProvider({}).fetch(simple_race) == []

    def test_cle_sources(self, simple_race, manual_panel):
        payload = {"sources": manual_panel}
        picks = ManualPanelProvider(payload).fetch(simple_race)
        assert len(picks) == 3


class TestHttpPanelProvider:
    def test_sans_url_configuree(self, simple_race):
        picks = HttpPanelProvider({}).fetch(simple_race)
        assert picks
        assert all(not p.usable for p in picks)
        assert all("URL" in p.note for p in picks)

    def test_repli_gracieux_sur_erreur_reseau(self, simple_race, monkeypatch):
        class Boom:
            def get(self, *args, **kwargs):
                raise RuntimeError("bloqué")

        provider = HttpPanelProvider({"Genybet": "https://example.invalid/{date}"})
        picks = provider.fetch(simple_race)
        assert picks
        assert not picks[0].usable
        assert "impossible" in picks[0].note


class TestExternalConsensus:
    def test_classement_externe(self, simple_race, manual_panel):
        picks = ManualPanelProvider(manual_panel).fetch(simple_race)
        result = ExternalConsensus().compute(simple_race, picks, ["h1", "h2", "h3", "h4", "h5"])
        assert result.consensus_ranking
        assert result.external_top(5)

    def test_convergence_parfaite(self, simple_race):
        picks = [
            SourcePick(source="Genybet", ranking=["h1", "h2", "h3", "h4", "h5"], coverage=1.0, usable=True)
        ]
        result = ExternalConsensus().compute(simple_race, picks, ["h1", "h2", "h3", "h4", "h5"])
        assert abs(result.convergence - 1.0) < 1e-9

    def test_convergence_partielle(self, simple_race):
        """Le panel designe h1-h5, l'interne h1, h3, h4, h5, h6 : 4 sur 6."""
        picks = [
            SourcePick(
                source="Genybet",
                ranking=["h1", "h2", "h3", "h4", "h5"],
                coverage=1.0,
                usable=True,
            )
        ]
        result = ExternalConsensus().compute(simple_race, picks, ["h1", "h3", "h4", "h5", "h6"])
        assert abs(result.convergence - 4 / 6) < 1e-9

    def test_convergence_parfaite_sur_meme_top(self, simple_race):
        picks = [
            SourcePick(
                source="Genybet",
                ranking=["h6", "h5", "h4", "h3", "h2"],
                coverage=1.0,
                usable=True,
            )
        ]
        result = ExternalConsensus().compute(
            simple_race, picks, ["h6", "h5", "h4", "h3", "h2"]
        )
        assert abs(result.convergence - 1.0) < 1e-9

    def test_sans_source_exploitable(self, simple_race):
        result = ExternalConsensus().compute(simple_race, [], ["h1", "h2"])
        assert result.consensus_ranking == []
        assert any("aucune source" in note for note in result.notes)

    def test_deux_blocs_distincts(self, simple_race):
        picks = [
            SourcePick(source="Genybet", ranking=["h1", "h2"], coverage=1.0, usable=True, is_panel_member=True),
            SourcePick(source="Blog Turf", ranking=["h1", "h3"], coverage=1.0, usable=True, is_panel_member=False),
        ]
        result = ExternalConsensus().compute(simple_race, picks, ["h1"])
        assert result.panel_consulted == ["Genybet"]
        assert result.additional_found == ["Blog Turf"]

    def test_divergences(self, simple_race, manual_panel):
        picks = ManualPanelProvider(manual_panel).fetch(simple_race)
        consensus = ExternalConsensus()
        result = consensus.compute(simple_race, picks, ["h1", "h2", "h3", "h4", "h5"])
        divergences = consensus.divergences(result, ["h6", "h5", "h4", "h3", "h2"], simple_race)
        assert "interne_seulement" in divergences
        assert "externe_seulement" in divergences

    def test_n_influence_jamais_les_scores(self, simple_race):
        """Règle d'or : le consensus externe ne note pas les chevaux."""
        result = ExternalConsensus().compute(simple_race, [], ["h1"])
        assert not hasattr(result, "scores")
        assert result.p_implicite  # seule sortie chiffrée : probabilité implicite

    def test_serialisation(self, simple_race, manual_panel):
        picks = ManualPanelProvider(manual_panel).fetch(simple_race)
        result = ExternalConsensus().compute(simple_race, picks, ["h1"])
        data = result.as_dict()
        assert "panel_consulted" in data
        assert "p_implicite" in data
