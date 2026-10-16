"""Garde-fou : une course mal identifiée doit être signalée, jamais présentée
comme une analyse normale."""

from __future__ import annotations

from hyperion.config import Settings
from hyperion.models import Discipline
from hyperion.pipeline import identification_warnings, run_pipeline


def test_course_complete_sans_avertissement(simple_race):
    simple_race.meta.hippodrome = "Vincennes"
    simple_race.meta.name = "Prix de Test"
    simple_race.meta.discipline = Discipline.TROT_ATTELE
    assert identification_warnings(simple_race) == []


def test_course_inconnue_signalee_dans_record_et_bloc(simple_race):
    simple_race.meta.hippodrome = None
    simple_race.meta.race_type = None
    simple_race.meta.name = None
    result = run_pipeline(simple_race, Settings())
    issues = result.record["identification"]
    assert any("hippodrome" in i for i in issues)
    assert any("nom de la course" in i for i in issues)
    assert "Identification incomplète" in result.blocks["1/5 · Course"]


def test_course_complete_pas_de_bandeau(simple_race):
    simple_race.meta.hippodrome = "Vincennes"
    simple_race.meta.name = "Prix de Test"
    result = run_pipeline(simple_race, Settings())
    assert "Identification incomplète" not in result.blocks["1/5 · Course"]
