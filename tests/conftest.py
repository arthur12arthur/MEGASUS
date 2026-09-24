"""Fixtures communes aux tests."""

from __future__ import annotations

import datetime as dt
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hyperion.models import Discipline, Horse, Race, RaceMeta, Shoeing  # noqa: E402
from hyperion.synthetic import SyntheticProvider  # noqa: E402


@pytest.fixture(autouse=True)
def _runs_hors_du_depot(tmp_path, monkeypatch):
    """Aucun test n'écrit dans data/runs/ du dépôt (réservé aux vraies analyses)."""
    monkeypatch.setenv("HYPERION_RUNS_DIR", str(tmp_path / "runs-isoles"))


@pytest.fixture
def race_date() -> dt.date:
    return dt.date(2026, 9, 20)


@pytest.fixture
def simple_race(race_date: dt.date) -> Race:
    """Course minimaliste mais complète, avec tous les champs renseignés."""
    horses = [
        Horse(
            horse_id="h1",
            name="Tonnerre de Mai",
            number=1,
            driver="KEITA M.",
            age=6,
            odds_pdf=3.5,
            gains=2_500_000,
            wins=8,
            places=14,
            runs=30,
            recent_places=(1, 2, 3, 1, 2),
            surface={"bon": 0.8, "mauvais": 0.4},
            distance={"2150": 0.7},
            shoeing=Shoeing(label="D4", adequacy=0.85),
            driver_stats={"win_rate": 0.22, "runs": 40},
            history_stats={"win_rate": 0.27, "runs": 30, "weeks_since_last_run": 3},
            comment="vient de s'imposer nettement",
        ),
        Horse(
            horse_id="h2",
            name="Éclair du Sahel",
            number=2,
            driver="SAWADOGO A.",
            age=5,
            odds_pdf=5.0,
            gains=1_200_000,
            wins=4,
            places=9,
            runs=25,
            recent_places=(2, 4, 1, 6, 3),
            surface={"bon": 0.6},
            distance={"2150": 0.5},
            shoeing=Shoeing(label="DP", adequacy=0.45),
            driver_stats={"win_rate": 0.15, "runs": 20},
            history_stats={"win_rate": 0.16, "runs": 25, "weeks_since_last_run": 8},
        ),
        Horse(
            horse_id="h3",
            name="Sagesse Noire",
            number=3,
            driver="OUEDRAOGO B.",
            age=7,
            odds_pdf=8.0,
            gains=600_000,
            wins=1,
            places=5,
            runs=28,
            recent_places=(6, 0, 8, 5, 7),
            surface={"bon": 0.4, "mauvais": 0.6},
            distance={"1600": 0.4},
            shoeing=Shoeing(label="F", adequacy=0.2),
            driver_stats={"win_rate": 0.08, "runs": 30},
            history_stats={"win_rate": 0.04, "runs": 28, "weeks_since_last_run": 20},
        ),
        Horse(
            horse_id="h4",
            name="Comète d'Afrique",
            number=4,
            driver="TRAORÉ I.",
            age=6,
            odds_pdf=11.0,
            gains=300_000,
            wins=0,
            places=3,
            runs=22,
            recent_places=(0, 0, 0, 0, 0),
            shoeing=Shoeing(label="D4", adequacy=0.5),
            driver_stats={"win_rate": 0.10, "runs": 15},
            history_stats={"win_rate": 0.0, "runs": 22, "weeks_since_last_run": 30},
        ),
        Horse(
            horse_id="h5",
            name="Mistral Royal",
            number=5,
            driver="KABORÉ S.",
            age=5,
            odds_pdf=14.0,
            gains=150_000,
            wins=0,
            places=1,
            runs=12,
            recent_places=(0, 7, 0, 0, 0),
        ),
        Horse(
            horse_id="h6",
            name="Zéphyr du Fleuve",
            number=6,
            driver="ZOUNGRANA P.",
            age=8,
            odds_pdf=22.0,
            gains=80_000,
            wins=0,
            places=0,
            runs=30,
            recent_places=(0, 0, 0, 0, 0),
            history_stats={"weeks_since_last_run": 40},
        ),
    ]
    return Race(
        meta=RaceMeta(
            operator="LONAB",
            country="Burkina Faso",
            race_country="France",
            meeting="R1",
            hippodrome="Paris-Vincennes",
            date=race_date,
            start_time=dt.datetime(
                race_date.year, race_date.month, race_date.day, 15, 0, tzinfo=dt.timezone.utc
            ),
            race_number=1,
            name="Prix de la République",
            distance_m=2150,
            terrain="bon",
            prize=1_500_000,
            discipline=Discipline.TROT_ATTELE,
            race_type="Trot attelé",
        ),
        horses=horses,
        race_id="20260920-paris-vincennes-r1c1",
    )


@pytest.fixture
def synthetic_provider() -> SyntheticProvider:
    return SyntheticProvider(seed=42)


@pytest.fixture
def synthetic(synthetic_provider: SyntheticProvider, race_date: dt.date):
    return synthetic_provider.race(race_date, index=1)


@pytest.fixture
def manual_panel(simple_race: Race) -> dict[str, list[str]]:
    """Panel de presse minimal, cohérent avec les cotes."""
    ordered = simple_race.order_by_odds()
    return {
        "Genybet": [h.name for h in ordered[:5]],
        "Equidia": [h.name for h in ordered[:6]],
        "Paris-Turf": [h.name for h in ordered[:4]],
    }


@pytest.fixture
def rng() -> random.Random:
    return random.Random(1234)
