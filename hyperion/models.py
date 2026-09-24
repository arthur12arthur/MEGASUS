"""Modèles de données canoniques du pipeline Hyperion.

Tous les modules communiquent exclusivement via ces structures. Aucun module
n'a le droit d'inventer une valeur manquante : un champ absent vaut ``None``
et se traduit par une dimension « non déterminée » en aval (règle d'or du
système : on n'estime jamais une donnée qu'on n'a pas).
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

# --------------------------------------------------------------------------
# Disciplines
# --------------------------------------------------------------------------


class Discipline(str, Enum):
    """Discipline de la course — active la grille de pondération du BaseScorer."""

    TROT_ATTELE = "trot_attele"
    TROT_MONTE = "trot_monte"
    PLAT = "plat"
    OBSTACLE = "obstacle"
    UNKNOWN = "unknown"

    @property
    def label_fr(self) -> str:
        return {
            Discipline.TROT_ATTELE: "Trot attelé",
            Discipline.TROT_MONTE: "Trot monté",
            Discipline.PLAT: "Plat",
            Discipline.OBSTACLE: "Obstacle",
            Discipline.UNKNOWN: "Indéterminée",
        }[self]

    @property
    def is_trot(self) -> bool:
        return self in (Discipline.TROT_ATTELE, Discipline.TROT_MONTE)


#: Mots-clés de repli quand le champ « type de course » est absent ou ambigu.
DISCIPLINE_KEYWORDS: tuple[tuple[Discipline, tuple[str, ...]], ...] = (
    (Discipline.TROT_ATTELE, ("attel", "attelage", "autostart", "voltige")),
    (Discipline.TROT_MONTE, ("monté", "monte", "monteg")),
    (Discipline.OBSTACLE, ("steeple", "haie", "haies", "obstacle", "cross")),
    (Discipline.PLAT, ("plat", "plat.", "handicap plat", "plat handicap")),
)


# --------------------------------------------------------------------------
# Ferrure (trot uniquement)
# --------------------------------------------------------------------------


@dataclass
class Shoeing:
    """État de ferrure du cheval.

    ``adequacy`` vaut ``None`` quand la configuration du jour n'est pas
    trouvée : elle reste alors « non déterminée », jamais estimée.
    """

    label: str | None = None
    date: _dt.date | None = None
    weeks_since: int | None = None
    adequacy: float | None = None  # 0.0 .. 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "date": self.date.isoformat() if self.date else None,
            "weeks_since": self.weeks_since,
            "adequacy": self.adequacy,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> Shoeing | None:
        if not data:
            return None
        raw_date = data.get("date")
        parsed: _dt.date | None = None
        if isinstance(raw_date, _dt.date):
            parsed = raw_date
        elif isinstance(raw_date, str) and raw_date:
            try:
                parsed = _dt.date.fromisoformat(raw_date)
            except ValueError:
                parsed = None
        adequacy = data.get("adequacy")
        return cls(
            label=data.get("label"),
            date=parsed,
            weeks_since=data.get("weeks_since"),
            adequacy=None if adequacy is None else _clip01(float(adequacy)),
        )


# --------------------------------------------------------------------------
# Cheval
# --------------------------------------------------------------------------


@dataclass
class Horse:
    """Un partant.

    ``odds_pdf`` est la cote figée du journal officiel ; ``odds_latest`` est la
    cote la plus récente connue (MarketWatch). La propriété :attr:`odds`
    retourne toujours la donnée la plus fraîche disponible.
    """

    horse_id: str
    name: str
    number: int | None = None
    driver: str | None = None
    trainer: str | None = None
    age: int | None = None
    sex: str | None = None

    odds_pdf: float | None = None
    odds_latest: float | None = None

    gains: float | None = None
    wins: int | None = None
    places: int | None = None
    runs: int | None = None

    #: Places des dernières sorties, 1 = gagné, 2 = 2e, ..., 0 = non placé.
    recent_places: tuple[int, ...] = ()

    music: str | None = None
    #: Aptitude par surface, ex. {"bon": 3, "mauvais": 1} ou {"psf": 0.8}.
    surface: Mapping[str, Any] = field(default_factory=dict)
    #: Aptitude par distance.
    distance: Mapping[str, Any] = field(default_factory=dict)

    shoeing: Shoeing | None = None
    #: Statistiques du driver dans la configuration du jour.
    driver_stats: Mapping[str, float] = field(default_factory=dict)
    #: Fiabilité historique du cheval dans la configuration du jour.
    history_stats: Mapping[str, float] = field(default_factory=dict)

    weight_kg: float | None = None
    non_runner: bool = False
    comment: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Tolérance de schéma : accepte une liste JSON pour les places récentes.
        if not isinstance(self.recent_places, tuple):
            object.__setattr__(self, "recent_places", _seq(self.recent_places))
        if self.horse_id is None or self.horse_id == "":
            object.__setattr__(self, "horse_id", _slugify(self.name))

    # -- propriétés utilitaires -------------------------------------------

    @property
    def odds(self) -> float | None:
        """Cote la plus fraîche disponible (jamais la cote périmée du PDF)."""
        if self.odds_latest is not None:
            return self.odds_latest
        return self.odds_pdf

    @property
    def odds_delta(self) -> float | None:
        """Variation relative de cote (négative = raccourcissement)."""
        if self.odds_pdf is None or self.odds_latest is None or self.odds_pdf <= 0:
            return None
        return (self.odds_latest - self.odds_pdf) / self.odds_pdf

    @property
    def is_favourite(self) -> bool:
        return False  # renseigné par le DataFilter

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "horse_id": self.horse_id,
            "name": self.name,
            "number": self.number,
            "driver": self.driver,
            "trainer": self.trainer,
            "age": self.age,
            "sex": self.sex,
            "odds_pdf": self.odds_pdf,
            "odds_latest": self.odds_latest,
            "gains": self.gains,
            "wins": self.wins,
            "places": self.places,
            "runs": self.runs,
            "recent_places": list(self.recent_places),
            "music": self.music,
            "surface": dict(self.surface),
            "distance": dict(self.distance),
            "shoeing": self.shoeing.as_dict() if self.shoeing else None,
            "driver_stats": dict(self.driver_stats),
            "history_stats": dict(self.history_stats),
            "weight_kg": self.weight_kg,
            "non_runner": self.non_runner,
            "comment": self.comment,
        }
        return data


# --------------------------------------------------------------------------
# Course
# --------------------------------------------------------------------------


@dataclass
class RaceMeta:
    """Métadonnées de la course — utilisées pour la vérification croisée."""

    #: Opérateur relais (LONAB / PMU'B) et pays du MARCHÉ DE PARIS.
    operator: str | None = None
    country: str | None = None
    #: Pays où la course se DÉROULE. La LONAB relaie des courses françaises.
    race_country: str | None = "France"
    meeting: str | None = None  # R1, R2, ...
    hippodrome: str | None = None
    date: _dt.date | None = None
    start_time: _dt.datetime | None = None  # conscient du fuseau horaire
    race_number: int | None = None
    name: str | None = None
    distance_m: int | None = None
    terrain: str | None = None
    prize: float | None = None
    discipline: Discipline = Discipline.UNKNOWN
    #: Champ officiel « type de course », conservé pour la traçabilité du
    #: DisciplineDetector (module 1.3).
    race_type: str | None = None
    source_url: str | None = None
    #: Clôture des enjeux LONAB si le programme l'imprime (sinon départ − N min).
    betting_close: _dt.datetime | None = None
    #: Pari PMU'B annoncé par le programme (Tiercé, Quarté, 4+1).
    bet_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "operator": self.operator,
            "country": self.country,
            "meeting": self.meeting,
            "hippodrome": self.hippodrome,
            "date": self.date.isoformat() if self.date else None,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "race_number": self.race_number,
            "name": self.name,
            "distance_m": self.distance_m,
            "terrain": self.terrain,
            "prize": self.prize,
            "discipline": self.discipline.value,
            "race_type": self.race_type,
            "source_url": self.source_url,
            "race_country": self.race_country,
            "betting_close": self.betting_close.isoformat() if self.betting_close else None,
            "bet_type": self.bet_type,
        }


@dataclass
class Race:
    """Course complète : métadonnées + partants."""

    meta: RaceMeta
    horses: list[Horse] = field(default_factory=list)
    race_id: str | None = None

    # -- helpers -----------------------------------------------------------

    @property
    def runners(self) -> list[Horse]:
        """Partants réellement au départ (non-partants exclus)."""
        return [h for h in self.horses if not h.non_runner]

    def by_id(self, horse_id: str) -> Horse | None:
        for horse in self.horses:
            if horse.horse_id == horse_id:
                return horse
        return None

    def names_map(self) -> dict[str, str]:
        """Identifiant de cheval -> nom affichable."""
        return {h.horse_id: h.name for h in self.horses}

    def order_by_odds(self) -> list[Horse]:
        """Partants triés par cote croissante ; les chevaux sans cote en dernier."""
        return sorted(
            self.runners,
            key=lambda h: (h.odds is None, h.odds if h.odds is not None else 0.0),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "race_id": self.race_id,
            "meta": self.meta.as_dict(),
            "horses": [h.as_dict() for h in self.horses],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Race:
        meta_raw = dict(data.get("meta") or {})
        meta_raw["discipline"] = Discipline(meta_raw.get("discipline", "unknown"))
        for key in ("start_time", "betting_close"):
            value = meta_raw.get(key)
            if isinstance(value, str) and value:
                try:
                    meta_raw[key] = _dt.datetime.fromisoformat(value)
                except ValueError:
                    meta_raw[key] = None
        # Enregistrements antérieurs : pas de race_country -> France (relais LONAB).
        meta_raw = {k: v for k, v in meta_raw.items() if k in RaceMeta.__dataclass_fields__}
        if isinstance(meta_raw.get("date"), str) and meta_raw["date"]:
            try:
                meta_raw["date"] = _dt.date.fromisoformat(meta_raw["date"])
            except ValueError:
                meta_raw["date"] = None
        horses = [Horse(**{**_drop_unknown(Horse, h), }) for h in data.get("horses", [])]
        for horse, raw in zip(horses, data.get("horses", [])):
            horse.shoeing = Shoeing.from_dict(raw.get("shoeing"))
        return cls(meta=RaceMeta(**meta_raw), horses=horses, race_id=data.get("race_id"))


# --------------------------------------------------------------------------
# Résultats officiels (soir)
# --------------------------------------------------------------------------


@dataclass
class OfficialResult:
    """Arrivée officielle d'une course."""

    race_id: str
    date: _dt.date
    #: Ordre d'arrivée : liste d'identifiants de chevaux, index 0 = gagnant.
    order: list[str] = field(default_factory=list)
    non_runners: list[str] = field(default_factory=list)
    source: str | None = None

    @property
    def winner(self) -> str | None:
        return self.order[0] if self.order else None

    def position_of(self, horse_id: str) -> int | None:
        try:
            return self.order.index(horse_id) + 1
        except ValueError:
            return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "race_id": self.race_id,
            "date": self.date.isoformat() if self.date else None,
            "order": list(self.order),
            "non_runners": list(self.non_runners),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OfficialResult:
        raw_date = data.get("date")
        if isinstance(raw_date, str) and raw_date:
            raw_date = _dt.date.fromisoformat(raw_date)
        return cls(
            race_id=data["race_id"],
            date=raw_date,
            order=list(data.get("order", [])),
            non_runners=list(data.get("non_runners", [])),
            source=data.get("source"),
        )


# --------------------------------------------------------------------------
# Utilitaires internes
# --------------------------------------------------------------------------


def _clip01(value: float) -> float:
    if math.isnan(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _drop_unknown(cls: type, mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Ne garde que les champs connus du dataclass (tolérance aux schémas riches)."""
    known = {f for f in cls.__dataclass_fields__ if f != "raw"}  # type: ignore[attr-defined]
    return {k: v for k, v in mapping.items() if k in known}


def first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def mean(values: Iterable[float]) -> float:
    seq = [float(v) for v in values]
    return sum(seq) / len(seq) if seq else 0.0


def _seq(values: Sequence[Any] | None) -> tuple[int, ...]:
    if not values:
        return ()
    out: list[int] = []
    for value in values:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            out.append(0)
    return tuple(out)


def _slugify(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in (text or "").strip()]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "cheval"
