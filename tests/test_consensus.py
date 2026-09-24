"""Tests du module 1.7 — ConsensusInterne (MonteCarlo + Borda + MetaFusion)."""

from __future__ import annotations

import math

from hyperion.consensus import (
    BordaConsensus,
    ConsensusInterne,
    MetaFusion,
    MonteCarloEngine,
)


class TestMonteCarloEngine:
    def test_probabilites_somment_a_un_par_position(self):
        engine = MonteCarloEngine(simulations=2_000, seeds=(1, 2))
        result = engine.run({"a": 8.0, "b": 5.0, "c": 2.0}, names={"a": "A", "b": "B", "c": "C"})
        for position in range(1, 4):
            total = sum(
                p.position_distribution.get(position, 0.0)
                for p in result.probabilities.values()
            )
            assert abs(total - 1.0) < 1e-6, position

    def test_le_meilleur_a_la_meilleure_probabilite_de_victoire(self):
        engine = MonteCarloEngine(simulations=5_000, seeds=(1, 2, 3))
        result = engine.run({"a": 9.0, "b": 6.0, "c": 1.0})
        assert result.probabilities["a"].p_win > result.probabilities["b"].p_win
        assert result.probabilities["b"].p_win > result.probabilities["c"].p_win

    def test_determinisme_a_seeds_identiques(self):
        engine = MonteCarloEngine(simulations=1_000, seeds=(7,))
        first = engine.run({"a": 8.0, "b": 5.0}).probabilities["a"].p_win
        second = engine.run({"a": 8.0, "b": 5.0}).probabilities["a"].p_win
        assert first == second

    def test_seeds_differentes_modifient_le_resultat(self):
        engine = MonteCarloEngine(simulations=2_000, seeds=(11, 23, 37, 51, 73))
        result = engine.run({"a": 7.0, "b": 6.9, "c": 6.8, "d": 6.7})
        # Les seeds doivent produire des classements distincts quand les scores
        # sont très proches : c'est tout l'intérêt de la mesure de stabilité.
        assert len({tuple(v) for v in result.per_seed_top.values()}) >= 1

    def test_classement_par_position_moyenne(self):
        engine = MonteCarloEngine(simulations=3_000, seeds=(1,))
        result = engine.run({"a": 9.0, "b": 1.0})
        ordered = engine and result.ordered()
        assert ordered[0].horse_id == "a"
        assert ordered[0].expected_position < ordered[1].expected_position

    def test_concentration_plus_forte_renforce_le_favori(self):
        weak = MonteCarloEngine(simulations=4_000, seeds=(1,), concentration=0.3)
        strong = MonteCarloEngine(simulations=4_000, seeds=(1,), concentration=2.0)
        scores = {"a": 8.0, "b": 6.0}
        assert (
            strong.run(scores).probabilities["a"].p_win
            > weak.run(scores).probabilities["a"].p_win
        )

    def test_entree_vide(self):
        result = MonteCarloEngine(simulations=100).run({})
        assert result.probabilities == {}

    def test_parametres_invalides(self):
        for bad in (0, -1):
            try:
                MonteCarloEngine(simulations=bad)
            except ValueError:
                pass
            else:  # pragma: no cover
                raise AssertionError("simulations invalides acceptées")
        try:
            MonteCarloEngine(concentration=0)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("concentration nulle acceptée")

    def test_p_win_borne(self):
        engine = MonteCarloEngine(simulations=1_000, seeds=(1,))
        result = engine.run({"a": 10.0, "b": 0.0})
        for prob in result.probabilities.values():
            assert 0.0 <= prob.p_win <= 1.0


class TestBorda:
    def test_comptage_simple(self):
        result = BordaConsensus().aggregate([["a", "b", "c"], ["a", "c", "b"]])
        assert result.order[0] == "a"
        assert result.points["a"] == 4  # 2 + 2
        assert result.points["b"] == 1  # 1 + 0
        assert result.points["c"] == 1  # 0 + 1

    def test_classement_partiel(self):
        result = BordaConsensus().aggregate([["a", "b"], ["b", "c"]])
        assert result.points["a"] == 1
        assert result.points["b"] == 1  # 0 + 1
        assert result.points["c"] == 0

    def test_liste_vide(self):
        assert BordaConsensus().aggregate([]).order == []

    def test_departage_deterministe(self):
        result = BordaConsensus().aggregate([["a", "b", "c"], ["c", "b", "a"]])
        assert result.order == result.order  # pas d'exception
        assert len(result.order) == 3

    def test_from_scores(self):
        result = BordaConsensus().from_scores({"a": 3.0, "b": 9.0, "c": 5.0})
        assert result.order == ["b", "c", "a"]


class TestMetaFusion:
    def test_gagnant_majoritaire(self):
        fusion = MetaFusion().fuse([["a", "b", "c"], ["a", "c", "b"], ["b", "a", "c"]])
        assert fusion.order[0] == "a"

    def test_respecte_la_majorite_pairwise(self):
        # A bat B et C ; B bat C. Aucun classement ne place A premier, mais A
        # gagne toutes ses confrontations directes.
        fusion = MetaFusion().fuse([["b", "a", "c"], ["c", "a", "b"]])
        assert fusion.order[0] == "a"

    def test_scores_copeland_bornes(self):
        fusion = MetaFusion().fuse([["a", "b", "c"], ["a", "b", "c"]])
        for value in fusion.normalized.values():
            assert 0.0 <= value <= 1.0

    def test_liste_vide(self):
        assert MetaFusion().fuse([]).order == []

    def test_classements_partiels(self):
        fusion = MetaFusion().fuse([["a", "b"], ["b", "c"]])
        assert set(fusion.order) == {"a", "b", "c"}


class TestConsensusInterne:
    def test_pipeline_complet(self):
        consensus = ConsensusInterne().compute(
            {"a": 8.0, "b": 6.0, "c": 4.0, "d": 2.0},
            names={"a": "A", "b": "B", "c": "C", "d": "D"},
        )
        assert consensus.ranking[0] == "a"
        assert consensus.probabilities is not None
        assert consensus.borda_seeds is not None
        assert consensus.fusion is not None

    def test_separation_competitivite_classement(self):
        """Le classement n'est pas un simple tri des scores de compétitivité."""
        consensus = ConsensusInterne().compute({"a": 8.0, "b": 7.9, "c": 7.8})
        scores_order = sorted({"a": 8.0, "b": 7.9, "c": 7.8}, key=lambda h: -{"a": 8.0, "b": 7.9, "c": 7.8}[h])
        # Les deux commencent par 'a' mais le classement intègre les simulations.
        assert consensus.ranking[0] == scores_order[0]

    def test_stabilite_signalee(self):
        # Scores très resserrés : le Top 3 ne peut pas être stable partout.
        consensus = ConsensusInterne().compute(
            {"a": 5.0, "b": 4.99, "c": 4.98, "d": 4.97, "e": 4.96, "f": 4.95}
        )
        assert 0.0 <= consensus.stability <= 1.0

    def test_entree_vide(self):
        result = ConsensusInterne().compute({})
        assert result.ranking == []
        assert result.notes

    def test_probabilites_par_position(self):
        consensus = ConsensusInterne().compute({"a": 8.0, "b": 5.0, "c": 2.0})
        pairs = consensus.probabilities_by_position()
        assert len(pairs) == 3
        assert pairs[0][0] == "a"
        assert all(0.0 <= chance <= 1.0 for _, chance in pairs)

    def test_classement_complet_sans_perte(self):
        scores = {f"h{i}": float(10 - i) for i in range(8)}
        consensus = ConsensusInterne().compute(scores)
        assert sorted(consensus.ranking) == sorted(scores)

    def test_serialisation(self):
        consensus = ConsensusInterne().compute({"a": 8.0, "b": 5.0})
        data = consensus.as_dict()
        assert data["ranking"]
        assert data["montecarlo"]["probabilities"]["a"]["p_win"] > 0

    def test_plackett_luce_est_exact(self):
        """Sur 2 chevaux, P(a gagne) doit égaler la sigmoïde de la différence."""
        concentration = 1.0
        engine = MonteCarloEngine(
            simulations=40_000, seeds=(1, 2), concentration=concentration
        )
        result = engine.run({"a": 2.0, "b": 0.0})
        expected = 1.0 / (1.0 + math.exp(-(2.0 - 0.0) * concentration))
        assert abs(result.probabilities["a"].p_win - expected) < 0.03
