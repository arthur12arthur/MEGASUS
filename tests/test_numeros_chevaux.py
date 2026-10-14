"""Non-regression: chaque mention d'un cheval dans le rapport porte son numero.

Bug corrige le 2026-10-14 : le rapport (blocs Telegram/email et JSON) affichait
le nom des chevaux sans leur numero de corde/dossard, rendant impossible le
rapprochement avec l'arrivee officielle (qui se lit par numero).
"""

import datetime as dt

from hyperion.config import Settings
from hyperion.models import Horse, Race, RaceMeta
from hyperion.pipeline import run_pipeline

RACE = Race(
    meta=RaceMeta(operator="LONAB", country="Burkina Faso", date=dt.date(2026, 10, 14),
                  hippodrome="Compiegne", race_type="Trot attele", race_number=1,
                  start_time=dt.datetime(2026, 10, 14, 15, 15, tzinfo=dt.timezone.utc)),
    horses=[
        Horse(horse_id="h1", name="Sagesse du Vivier", number=1, odds_pdf=6.0, gains=12000),
        Horse(horse_id="h2", name="Brave Royal", number=2, odds_pdf=1.8, gains=45000),
        Horse(horse_id="h3", name="Tonnerre de Bretagne", number=3, odds_pdf=2.6, gains=38000),
    ],
    race_id="test-numeros",
)


def test_chaque_cheval_du_classement_porte_son_numero_dans_les_blocs():
    result = run_pipeline(RACE, Settings())
    text = "\n".join(result.blocks.values())
    for horse in RACE.horses:
        assert f"n°{horse.number} · {horse.name}" in text, (
            f"{horse.name} apparait sans son numero dans le rapport texte"
        )


def test_le_classement_json_porte_le_numero():
    result = run_pipeline(RACE, Settings())
    numbers = {h.horse_id: h.number for h in RACE.horses}
    for row in result.record["classement"]:
        assert row["numero"] == numbers[row["horse_id"]]
    for row in result.record["competitivite"]:
        assert row["numero"] is not None


def test_les_donnees_manquantes_portent_le_numero():
    result = run_pipeline(RACE, Settings())
    block = result.blocks["5/5 · Confiance"]
    assert "Données manquantes" in block
    after = block.split("Données manquantes", 1)[1]
    champs = ("recent_places", "driver", "aptitude", "shoeing")
    for line in after.splitlines():
        if any(champ in line for champ in champs):
            assert line.strip().startswith("·") and "n°" in line, line
