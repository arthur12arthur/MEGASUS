"""Tests du module 1.6 — BaseScorer."""

from __future__ import annotations

from hyperion.analysis.scorer import (
    BaseScorer,
    bayesian_rate,
    score_aptitude,
    score_forme,
    score_fraicheur,
    score_historique,
    score_technique,
)
from hyperion.models import Discipline, Horse


class TestDimensions:
    def test_forme_depuis_places(self):
        horse = Horse(horse_id="h", name="X", recent_places=(1, 1, 1, 1, 1))
        assert score_forme(horse) == 10.0

    def test_forme_nulle_si_jamais_place(self):
        horse = Horse(horse_id="h", name="X", recent_places=(0, 0, 0, 0, 0))
        assert score_forme(horse) == 0.0

    def test_forme_depuis_musique(self):
        horse = Horse(horse_id="h", name="X", music="1a1a1a")
        assert score_forme(horse) == 10.0

    def test_forme_non_determinee_sans_donnee(self):
        horse = Horse(horse_id="h", name="X")
        assert score_forme(horse) is None

    def test_historique_non_determine_sans_donnee(self):
        horse = Horse(horse_id="h", name="X")
        assert score_historique(horse, None) is None

    def test_historique_echelle_log(self):
        weak = Horse(horse_id="a", name="A", gains=100_000)
        strong = Horse(horse_id="b", name="B", gains=5_000_000)
        low = score_historique(weak, 5_000_000)
        high = score_historique(strong, 5_000_000)
        assert low < high

    def test_aptitude_depuis_mots(self):
        horse = Horse(horse_id="h", name="X", surface={"a": "bon", "b": "mauvais"})
        value = score_aptitude(horse)
        assert value is not None and 0.0 < value < 10.0

    def test_aptitude_non_determinee(self):
        horse = Horse(horse_id="h", name="X")
        assert score_aptitude(horse) is None

    def test_aptitude_ignore_un_compte_suspect(self):
        # Une valeur > 1 est probablement un compte de victoires, pas une aptitude.
        horse = Horse(horse_id="h", name="X", surface={"victoires": 7})
        assert score_aptitude(horse) is None

    def test_fraicheur_optimale(self):
        horse = Horse(
            horse_id="h",
            name="X",
            history_stats={"weeks_since_last_run": 4},
        )
        assert score_fraicheur(horse, Discipline.TROT_ATTELE) == 10.0

    def test_fraicheur_trop_longue(self):
        horse = Horse(
            horse_id="h",
            name="X",
            history_stats={"weeks_since_last_run": 40},
        )
        assert score_fraicheur(horse, Discipline.TROT_ATTELE) < 2.0

    def test_fraicheur_non_determinee(self):
        horse = Horse(horse_id="h", name="X")
        assert score_fraicheur(horse, Discipline.TROT_ATTELE) is None


class TestLissageBayesien:
    def test_petit_echantillon_tire_vers_le_predit(self):
        # Une seule victoire sur une course ne fait pas un spécialiste.
        smoothed = bayesian_rate(1.0, 1, 0.12)
        assert smoothed < 0.4

    def test_grand_echantillon_converge(self):
        smoothed = bayesian_rate(0.30, 200, 0.12)
        assert smoothed > 0.29

    def test_valeur_absente_renvoie_le_predit(self):
        assert bayesian_rate(None, None, 0.12) == 0.12


class TestTechnique:
    def test_trot_decompose_la_ferrure(self):
        horse = Horse(
            horse_id="h",
            name="X",
            shoeing=type("S", (), {"adequacy": 1.0})(),
            driver_stats={"win_rate": 0.2, "runs": 100},
            history_stats={"win_rate": 0.2, "runs": 100},
        )
        score, detail = score_technique(horse, Discipline.TROT_ATTELE, 0.12, 0.12)
        assert score is not None
        assert detail["split"]["ferrure"] == 0.60

    def test_ferrure_absente_renormalise(self):
        horse = Horse(
            horse_id="h",
            name="X",
            shoeing=None,
            driver_stats={"win_rate": 0.2, "runs": 100},
            history_stats={"win_rate": 0.2, "runs": 100},
        )
        score, detail = score_technique(horse, Discipline.TROT_ATTELE, 0.12, 0.12)
        assert score is not None
        assert detail["renormalise"] is True

    def test_technique_entierement_non_determinee(self):
        horse = Horse(horse_id="h", name="X")
        score, _ = score_technique(horse, Discipline.PLAT, 0.12, 0.12)
        assert score is None


class TestBaseScorer:
    def test_scores_dans_l_intervalle(self, simple_race):
        selected = [h.horse_id for h in simple_race.runners]
        result = BaseScorer().score(simple_race, selected)
        for item in result.scores:
            assert 0.0 <= item.competitiveness <= 10.0
            for value in item.dimensions.values():
                if value is not None:
                    assert 0.0 <= value <= 10.0

    def test_le_favori_des_donnees_est_premier(self, simple_race):
        selected = [h.horse_id for h in simple_race.runners]
        result = BaseScorer().score(simple_race, selected)
        assert result.ordered()[0].horse_id == "h1"

    def test_poids_utilises_somment_a_un(self, simple_race):
        selected = [h.horse_id for h in simple_race.runners]
        result = BaseScorer().score(simple_race, selected)
        for item in result.scores:
            if item.weights_used:
                assert abs(sum(item.weights_used.values()) - 1.0) < 1e-9

    def test_dimension_non_determinee_redistribue_les_poids(self, simple_race):
        # h5 et h6 n'ont ni aptitude ni musique : leurs poids doivent être
        # redistribués, pas laissés à zéro.
        selected = [h.horse_id for h in simple_race.runners]
        result = BaseScorer().score(simple_race, selected)
        for horse_id in ("h5", "h6"):
            item = result.score(horse_id)
            assert item is not None
            if item.undetermined:
                assert "aptitude" in item.undetermined or "forme" in item.undetermined
                assert abs(sum(item.weights_used.values()) - 1.0) < 1e-9

    def test_la_cote_n_entre_jamais_dans_le_calcul(self, simple_race):
        """Règle d'or : le score de compétitivité ignore totalement la cote."""
        selected = [h.horse_id for h in simple_race.runners]
        first = BaseScorer().score(simple_race, selected)
        # On modifie massivement les cotes.
        for horse in simple_race.horses:
            horse.odds_pdf = (horse.odds_pdf or 1.0) * 5.0
            horse.odds_latest = None
        second = BaseScorer().score(simple_race, selected)
        for item in second.scores:
            assert abs(item.competitiveness - first.score(item.horse_id).competitiveness) < 1e-9

    def test_poids_varient_avec_la_discipline(self, simple_race):
        selected = [h.horse_id for h in simple_race.runners]
        trot = BaseScorer().score(simple_race, selected, Discipline.TROT_ATTELE)
        plat = BaseScorer().score(simple_race, selected, Discipline.PLAT)
        assert trot.weights != plat.weights

    def test_cheval_hors_selection_ignore(self, simple_race):
        result = BaseScorer().score(simple_race, ["h1"])
        assert [s.horse_id for s in result.scores] == ["h1"]
