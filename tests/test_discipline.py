"""Tests du module 1.3 — DisciplineDetector."""

from __future__ import annotations

from hyperion.analysis.discipline import (
    WEIGHTS,
    detect_discipline,
    detect_from_field,
    detect_from_keywords,
    discipline_weights,
    explain_weights,
)
from hyperion.models import Discipline


class TestDetectFromField:
    def test_lecture_directe(self):
        discipline, why = detect_from_field("Trot attelé")
        assert discipline is Discipline.TROT_ATTELE
        assert "directement" in why

    def test_lecture_avec_suffixe(self):
        discipline, _ = detect_from_field("Trot attelé 2150m")
        assert discipline is Discipline.TROT_ATTELE

    def test_trot_monte(self):
        discipline, _ = detect_from_field("trot monté")
        assert discipline is Discipline.TROT_MONTE

    def test_plat(self):
        discipline, _ = detect_from_field("Plat")
        assert discipline is Discipline.PLAT

    def test_obstacle_steeple(self):
        discipline, _ = detect_from_field("Steeple-chase")
        assert discipline is Discipline.OBSTACLE

    def test_champ_absent_renvoie_unknown(self):
        discipline, why = detect_from_field(None)
        assert discipline is Discipline.UNKNOWN
        assert "absent" in why

    def test_champ_inconnu_renvoie_unknown(self):
        discipline, why = detect_from_field("Discipline martienne")
        assert discipline is Discipline.UNKNOWN
        assert "non reconnu" in why


class TestDetectFromKeywords:
    def test_mot_cle_attelage(self):
        discipline, why = detect_from_keywords("Course d'attelage de 2150m")
        assert discipline is Discipline.TROT_ATTELE
        assert "attel" in why

    def test_mot_cle_haies(self):
        discipline, _ = detect_from_keywords("Épreuve de haies")
        assert discipline is Discipline.OBSTACLE

    def test_texte_vide(self):
        discipline, why = detect_from_keywords("")
        assert discipline is Discipline.UNKNOWN
        assert why


class TestDetectDiscipline:
    def test_priorite_au_champ_officiel(self):
        discipline, why = detect_discipline("Plat", free_text="trot attelé")
        assert discipline is Discipline.PLAT
        assert "directement" in why

    def test_repli_par_mots_cles(self):
        discipline, why = detect_discipline(None, free_text="prix de trot attelé")
        assert discipline is Discipline.TROT_ATTELE
        assert "repli" in why

    def test_sans_rien(self):
        discipline, why = detect_discipline(None, free_text="")
        assert discipline is Discipline.UNKNOWN


class TestWeights:
    def test_somme_a_un(self):
        for discipline, weights in WEIGHTS.items():
            assert abs(sum(weights.values()) - 1.0) < 1e-9, discipline

    def test_chaque_discipline_a_une_grille(self):
        for discipline in Discipline:
            weights = discipline_weights(discipline)
            assert len(weights) == 5

    def test_le_trot_privilegie_la_technique(self):
        trot = WEIGHTS[Discipline.TROT_ATTELE]
        plat = WEIGHTS[Discipline.PLAT]
        assert trot["technique"] > plat["technique"]
        assert plat["historique"] > trot["historique"]

    def test_explication_lisible(self):
        text = explain_weights(Discipline.TROT_ATTELE)
        assert "Trot attelé" in text
        assert "historique" in text
