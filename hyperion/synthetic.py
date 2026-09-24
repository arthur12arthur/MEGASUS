"""Générateur de courses synthétiques — support des backtests et du labo.

Produit des courses dont la « vérité » est connue (capacité latente de chaque
cheval) afin de mesurer objectivement si le pipeline retrouve les bons
chevaux. Le pipeline ne voit QUE les observables (gains, forme, aptitudes,
cote, ferrure) ; la capacité latente reste dans ``raw`` et n'est utilisée
qu'à l'évaluation.

Modèle génératif :

    A_i        ~ Normal(0, 1)                       capacité latente
    observables = A_i + bruit                       ce que voit le système
    cote_i     = marché bruité de A_i               estimation imparfaite
    arrivée    ~ Plackett-Luce(exp(k × A_i))        vérité de la course

Un système qui bat la cote doit retrouver A_i malgré le bruit des
observables : c'est exactement ce que ce générateur permet de mesurer.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from hyperion.models import Discipline, Horse, Race, RaceMeta, Shoeing

#: Noms de chevaux plausibles pour les courses synthétiques.
_PREFIXES = (
    "Tonnerre", "Éclair", "Vent", "Sable", "Aurore", "Comète", "Mistral",
    "Zéphyr", "Orage", "Sirocco", "Boréal", "Faucon", "Sagesse", "Victoire",
    "Audace", "Rapide", "Noble", "Brave", "Fidèle", "Vaillant",
)
_SUFFIXES = (
    "de Mai", "du Sahel", "Noir", "d'Afrique", "de l'Ouest", "Royal",
    "du Fleuve", "Étoilé", "de Kaya", "du Plateau", "Sans Pareil",
    "de Ouaga", "du Yatenga", "Lumière", "de l'Avenir",
)
_DRIVERS = ("KEITA M.", "SAWADOGO A.", "OUEDRAOGO B.", "TRAORÉ I.", "KABORÉ S.", "ZOUNGRANA P.")
_SURFACES = ("bon", "moyen", "mauvais")
_DISTANCES = (1400, 1600, 2100, 2150, 2500, 2700, 2850, 3200)


@dataclass
class SyntheticRace:
    """Course synthétique avec sa vérité connue."""

    race: Race
    latent: dict[str, float] = field(default_factory=dict)
    market_odds: dict[str, float] = field(default_factory=dict)

    def true_ranking(self) -> list[str]:
        return sorted(self.latent, key=lambda h: -self.latent[h])

    def simulate_outcome(self, seed: int, strength: float = 1.2) -> list[str]:
        """Arrivée réelle simulée (Plackett-Luce sur les capacités latentes)."""
        import math

        rng = random.Random(seed)
        remaining = list(self.latent.items())
        order: list[str] = []
        while remaining:
            weights = [
                math.exp(strength * self.latent[h]) for h, _ in remaining
            ]
            total = sum(weights)
            pick = rng.random() * total
            cumulative = 0.0
            index = len(remaining) - 1
            for position, weight in enumerate(weights):
                cumulative += weight
                if pick <= cumulative:
                    index = position
                    break
            order.append(remaining[index][0])
            remaining.pop(index)
        return order


class SyntheticProvider:
    """Génère des courses synthétiques reproductibles."""

    def __init__(
        self,
        seed: int = 42,
        min_horses: int = 8,
        max_horses: int = 16,
        discipline: Discipline = Discipline.TROT_ATTELE,
        noise: float = 0.45,
        market_noise: float = 0.25,
    ) -> None:
        self.seed = seed
        self.min_horses = min_horses
        self.max_horses = max_horses
        self.discipline = discipline
        self.noise = noise
        self.market_noise = market_noise

    def race(self, date: dt.date, index: int = 1) -> SyntheticRace:
        rng = random.Random(self.seed + index)
        n = rng.randint(self.min_horses, self.max_horses)
        latent = {
            f"cheval-{i + 1:02d}": rng.gauss(0.0, 1.0) for i in range(n)
        }
        names: dict[str, str] = {}
        horses: list[Horse] = []
        market_odds: dict[str, float] = {}

        for horse_id, ability in latent.items():
            name = self._name(rng)
            names[horse_id] = name
            horses.append(
                Horse(
                    horse_id=horse_id,
                    name=name,
                    number=len(horses) + 1,
                    driver=rng.choice(_DRIVERS),
                    age=rng.randint(4, 9),
                    odds_pdf=self._odds(rng, ability, self.market_noise),
                    gains=self._gains(rng, ability),
                    wins=max(0, int(round(rng.gauss(2.0 + 3.0 * ability, 1.2)))),
                    places=max(0, int(round(rng.gauss(6.0 + 4.0 * ability, 1.5)))),
                    runs=rng.randint(8, 40),
                    recent_places=self._form(rng, ability),
                    music=self._music(rng, ability),
                    surface={
                        "bon": self._aptitude(rng, ability),
                        "moyen": self._aptitude(rng, ability),
                        "mauvais": self._aptitude(rng, ability),
                    },
                    distance={
                        str(rng.choice(_DISTANCES)): self._aptitude(rng, ability)
                    },
                    shoeing=(
                        Shoeing(
                            label=rng.choice(["D4", "DP", "F", "D4 F"]),
                            adequacy=min(
                                1.0, max(0.0, 0.5 + 0.4 * ability + rng.gauss(0, 0.15 * self.noise))
                            ),
                        )
                        if self.discipline.is_trot
                        else None
                    ),
                    driver_stats={
                        "win_rate": min(
                            0.6,
                            max(0.0, 0.12 + 0.10 * ability + rng.gauss(0, 0.05 * self.noise)),
                        ),
                        "runs": rng.randint(5, 60),
                    },
                    history_stats={
                        "win_rate": min(
                            0.6,
                            max(0.0, 0.15 + 0.10 * ability + rng.gauss(0, 0.06 * self.noise)),
                        ),
                        "runs": rng.randint(3, 40),
                        "weeks_since_last_run": rng.randint(1, 20),
                    },
                    weight_kg=round(rng.uniform(55.0, 62.0), 1),
                    comment=self._comment(rng, ability),
                    raw={"latent_ability": ability},
                )
            )
            market_odds[horse_id] = horses[-1].odds_pdf or 10.0

        meta = RaceMeta(
            operator="LONAB",
            country="Burkina Faso",
            meeting=f"R{index}",
            hippodrome=rng.choice(["Ouagadougou", "Bobo-Dioulasso", "Kaya"]),
            date=date,
            start_time=dt.datetime(
                date.year, date.month, date.day, 15, 0, tzinfo=dt.timezone.utc
            ),
            race_number=index,
            name=f"Prix de l'Indépendance {index}",
            distance_m=rng.choice(_DISTANCES),
            terrain=rng.choice(["bon", "souple", "lourd"]),
            prize=round(rng.uniform(500_000, 2_000_000), -3),
            discipline=self.discipline,
            race_type=self.discipline.label_fr,
        )
        return SyntheticRace(
            race=Race(meta=meta, horses=horses, race_id=self._race_id(meta)),
            latent=latent,
            market_odds=market_odds,
        )

    def series(self, start: dt.date, count: int, step_days: int = 1) -> list[SyntheticRace]:
        """Série de courses consécutives (pour le protocole glissant)."""
        return [
            self.race(start + dt.timedelta(days=step_days * i), index=i + 1)
            for i in range(count)
        ]

    # -- génération des observables ---------------------------------------

    def _name(self, rng: random.Random) -> str:
        return f"{rng.choice(_PREFIXES)} {rng.choice(_SUFFIXES)}"

    def _odds(self, rng: random.Random, ability: float, market_noise: float) -> float:
        """Cote de marché : capacité latente + bruit.

        Cote FAIBLE = cheval FORT. Le bruit représente l'imperfection de
        l'estimation du marché — c'est exactement ce que le pipeline doit
        essayer de corriger.
        """
        strength = ability + rng.gauss(0.0, market_noise)
        return round(max(1.4, 4.0 * pow(2.71828, -0.9 * strength)), 1)

    def _gains(self, rng: random.Random, ability: float) -> float:
        noise = 0.9 * self.noise
        return round(max(0.0, rng.gauss(1.5 + 2.2 * ability, noise)) * 1_000_000, -3)

    def _form(self, rng: random.Random, ability: float) -> tuple[int, ...]:
        places: list[int] = []
        for _ in range(5):
            draw = rng.random()
            if draw < 0.08 + 0.20 * ability:
                places.append(1)
            elif draw < 0.22 + 0.28 * ability:
                places.append(2)
            elif draw < 0.40 + 0.28 * ability:
                places.append(3)
            elif draw < 0.60 + 0.22 * ability:
                places.append(rng.randint(4, 6))
            else:
                places.append(0)
        return tuple(places)

    def _music(self, rng: random.Random, ability: float) -> str:
        return "".join(f"{p}a" for p in self._form(rng, ability))

    def _aptitude(self, rng: random.Random, ability: float) -> float:
        value = 0.5 + 0.35 * ability + rng.gauss(0, 0.18 * self.noise)
        return round(min(1.0, max(0.0, value)), 3)

    def _comment(self, rng: random.Random, ability: float) -> str | None:
        if rng.random() < 0.45:
            return None
        options = [
            "vient de s'imposer nettement",
            "en constante progression",
            "confirmant sa belle forme",
            "décevant en dernier lieu",
            "à reprendre en confiance",
            "vient d'affronter les meilleurs",
        ]
        return rng.choice(options)

    def _race_id(self, meta: RaceMeta) -> str:
        stamp = (meta.date or dt.date.today()).strftime("%Y%m%d")
        return f"{stamp}-synthetique-c{meta.race_number}"


def save_synthetic(race: Race, path: Path | str, latent: Mapping[str, float] | None = None) -> Path:
    """Écrit une course synthétique en JSON (utilisable par les providers)."""
    payload = race.as_dict()
    if latent:
        payload["_synthetic_latent"] = dict(latent)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    import json

    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return path


__all__ = ["SyntheticProvider", "SyntheticRace", "save_synthetic"]
