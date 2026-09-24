"""Contexte relais LONAB / PMU'B → courses françaises.

MEGASUS analyse la course **française** relayée par la LONAB (Loterie
Nationale Burkinabè) sous la marque PMU'B, pour le marché burkinabè.
La LONAB est une **source relais** : elle ne court pas les épreuves, elle
les republie et prend les paris localement. Trois conséquences concrètes,
toutes implémentées ici et rendues explicites dans le rapport :

1. **Deux pays, deux rôles.** ``RaceMeta.country`` est le pays du marché de
   paris (Burkina Faso : c'est lui qu'on vérifie pour ne pas analyser le
   programme d'un autre opérateur) ; ``RaceMeta.race_country`` est le pays où
   la course se déroule (France). Les hippodromes sont français.

2. **Deux fuseaux, une heure limite.** La course part à l'heure de Paris
   (``Europe/Paris`` : UTC+1 l'hiver, UTC+2 l'été) ; le programme LONAB
   affiche les heures en heure de Ouagadougou (``Africa/Ouagadougou``,
   UTC+0 toute l'année). L'écart est donc de 1 h en hiver et de 2 h en été.
   L'heure qui compte pour le parieur n'est pas le départ mais la
   **clôture des enjeux LONAB**, observée environ 10 minutes avant le départ
   (ex. départ 14h15, clôture 14h05). Ce délai est paramétrable
   (``HYPERION_LONAB_CLOSING_MINUTES``) et remplacé par l'heure de clôture
   imprimée dans le programme quand elle y figure.

3. **Le pari du jour dépend du jour.** Calendrier PMU'B publié par la LONAB :
   Tiercé mercredi et samedi ; Quarté lundi, mardi et jeudi ; 4+1 vendredi,
   dimanche et le dernier mardi du mois ; Couplé tous les jours. Le nombre
   de places à trouver (3, 4 ou 5) est annoncé dans le rapport.

Ce module est pur (aucun accès réseau) et entièrement testé.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from hyperion.models import Discipline

#: Pays où se déroulent les courses relayées par la LONAB.
RACE_COUNTRY = "France"
#: Fuseau des hippodromes français.
RACE_TZ = "Europe/Paris"
#: Fuseau du marché LONAB et des heures imprimées dans son programme.
RELAY_TZ = "Africa/Ouagadougou"
#: Délai observé entre la clôture des enjeux LONAB et le départ en France.
#: Valeur par défaut, à confirmer localement ; surchargeable par variable
#: d'environnement et remplacée par l'heure de clôture du programme si connue.
DEFAULT_CLOSING_MINUTES = 10


# --------------------------------------------------------------------------
# Pari PMU'B du jour
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LonabGame:
    """Pari principal PMU'B du jour."""

    name: str
    places: int  # nombre de premiers à trouver
    reason: str  # d'où vient l'information (programme ou calendrier)

    def describe(self) -> str:
        return f"{self.name} ({self.places} premiers) — {self.reason}"

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "places": self.places, "reason": self.reason}


_GAME_PLACES = {"Tiercé": 3, "Quarté": 4, "4+1": 5}
_WEEKDAY_GAME = {0: "Quarté", 1: "Quarté", 2: "Tiercé", 3: "Quarté", 4: "4+1", 5: "Tiercé", 6: "4+1"}
_WEEKDAY_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

_GAME_RE = re.compile(r"\b(4\s*\+\s*1|quart[ée]|tierc[ée])", re.IGNORECASE)


def _is_last_tuesday(date: dt.date) -> bool:
    if date.weekday() != 1:
        return False
    last_day = calendar.monthrange(date.year, date.month)[1]
    return date.day + 7 > last_day


def normalise_game(raw: str | None) -> str | None:
    """« QUARTE », « 4 + 1 », « tiercé » -> nom canonique, sinon ``None``."""
    if not raw:
        return None
    match = _GAME_RE.search(raw)
    if not match:
        return None
    token = match.group(1).lower().replace(" ", "")
    if token == "4+1":
        return "4+1"
    if token.startswith("quart"):
        return "Quarté"
    return "Tiercé"


def lonab_game_for(date: dt.date | None, declared: str | None = None) -> LonabGame | None:
    """Pari principal du jour : celui du programme s'il est lisible, sinon le calendrier."""
    name = normalise_game(declared)
    if name:
        return LonabGame(name, _GAME_PLACES[name], "lu dans le programme")
    if date is None:
        return None
    if _is_last_tuesday(date):
        return LonabGame("4+1", 5, "calendrier LONAB : dernier mardi du mois")
    name = _WEEKDAY_GAME[date.weekday()]
    return LonabGame(name, _GAME_PLACES[name], f"calendrier LONAB : {_WEEKDAY_FR[date.weekday()]}")


# --------------------------------------------------------------------------
# Horaires : départ en France, clôture LONAB
# --------------------------------------------------------------------------


def localise(value: dt.datetime | None, assumed_tz: str = RELAY_TZ) -> dt.datetime | None:
    """Rend une heure consciente du fuseau ; une heure naïve est lue en ``assumed_tz``."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo(assumed_tz))
    return value


@dataclass(frozen=True)
class RaceSchedule:
    """Départ et clôture exprimés dans les deux fuseaux."""

    start: dt.datetime  # conscient du fuseau
    close: dt.datetime  # clôture des enjeux LONAB
    close_source: str  # « programme » ou « départ − N min »

    @property
    def start_relay(self) -> dt.datetime:
        return self.start.astimezone(ZoneInfo(RELAY_TZ))

    @property
    def start_france(self) -> dt.datetime:
        return self.start.astimezone(ZoneInfo(RACE_TZ))

    @property
    def close_relay(self) -> dt.datetime:
        return self.close.astimezone(ZoneInfo(RELAY_TZ))

    @property
    def offset_hours(self) -> float:
        """Écart Paris − Ouagadougou le jour de la course (1 h l'hiver, 2 h l'été)."""
        paris = self.start_france.utcoffset() or dt.timedelta(0)
        ouaga = self.start_relay.utcoffset() or dt.timedelta(0)
        return (paris - ouaga).total_seconds() / 3600.0

    def describe(self) -> str:
        return (
            f"départ {self.start_relay:%Hh%M} à Ouagadougou "
            f"({self.start_france:%Hh%M} heure de Paris, écart {self.offset_hours:.0f} h) · "
            f"clôture LONAB {self.close_relay:%Hh%M} ({self.close_source})"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_ouagadougou": self.start_relay.isoformat(),
            "start_paris": self.start_france.isoformat(),
            "close_lonab": self.close_relay.isoformat(),
            "close_source": self.close_source,
            "offset_hours": self.offset_hours,
        }


def schedule_for(
    start: dt.datetime | None,
    betting_close: dt.datetime | None = None,
    closing_minutes: int = DEFAULT_CLOSING_MINUTES,
    assumed_tz: str = RELAY_TZ,
) -> RaceSchedule | None:
    """Calcule l'horaire complet ; ``None`` si l'heure de départ est inconnue."""
    start = localise(start, assumed_tz)
    if start is None:
        return None
    close = localise(betting_close, assumed_tz)
    if close is not None and close <= start:
        return RaceSchedule(start=start, close=close, close_source="programme")
    close = start - dt.timedelta(minutes=max(0, closing_minutes))
    return RaceSchedule(start=start, close=close, close_source=f"départ − {closing_minutes} min")


# --------------------------------------------------------------------------
# Hippodromes français
# --------------------------------------------------------------------------

_TRANSLATE = str.maketrans("àâäéèêëîïôöùûüç'-", "aaaeeeeiioouuuc  ")


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name.translate(_TRANSLATE).casefold()).strip()


#: Référentiel des principaux hippodromes du PMU. Seuls les hippodromes
#: **mono-discipline** portent une discipline déductible sans ambiguïté ;
#: les autres (trot attelé ou monté, ou multi-disciplines) donnent ``None``.
FRENCH_HIPPODROMES: dict[str, tuple[str, Discipline | None, str]] = {
    "vincennes": ("Paris-Vincennes", None, "trot (attelé ou monté)"),
    "paris vincennes": ("Paris-Vincennes", None, "trot (attelé ou monté)"),
    "enghien": ("Enghien-Soisy", None, "trot et obstacle"),
    "cabourg": ("Cabourg", None, "trot (attelé ou monté)"),
    "caen": ("Caen", None, "trot (attelé ou monté)"),
    "laval": ("Laval", None, "trot (attelé ou monté)"),
    "cagnes sur mer": ("Cagnes-sur-Mer", None, "trot, plat et obstacle"),
    "cagnes": ("Cagnes-sur-Mer", None, "trot, plat et obstacle"),
    "vichy": ("Vichy", None, "trot et plat"),
    "longchamp": ("ParisLongchamp", Discipline.PLAT, "plat uniquement"),
    "parislongchamp": ("ParisLongchamp", Discipline.PLAT, "plat uniquement"),
    "chantilly": ("Chantilly", Discipline.PLAT, "plat uniquement"),
    "saint cloud": ("Saint-Cloud", Discipline.PLAT, "plat uniquement"),
    "deauville": ("Deauville", Discipline.PLAT, "plat uniquement"),
    "auteuil": ("Auteuil", Discipline.OBSTACLE, "obstacle uniquement"),
    "compiegne": ("Compiègne", None, "plat et obstacle"),
    "pau": ("Pau", None, "obstacle et plat"),
    "lyon parilly": ("Lyon-Parilly", None, "plusieurs disciplines"),
    "marseille borely": ("Marseille-Borély", None, "plusieurs disciplines"),
}

_TROT_ONLY = {"Paris-Vincennes", "Cabourg", "Caen", "Laval"}


def lookup_hippodrome(name: str | None) -> tuple[str, Discipline | None, str] | None:
    """Retrouve un hippodrome français ; tolère « R1 - PARIS-VINCENNES » ou « Hippodrome de Chantilly »."""
    if not name:
        return None
    key = _key(name)
    # Les clés longues d'abord : « paris vincennes » avant « vincennes ».
    for candidate in sorted(FRENCH_HIPPODROMES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(candidate)}\b", key):
            return FRENCH_HIPPODROMES[candidate]
    return None


def discipline_hint(hippodrome: str | None) -> tuple[Discipline | None, str]:
    """Indice de discipline tiré de l'hippodrome (dernier repli du module 1.3)."""
    found = lookup_hippodrome(hippodrome)
    if found is None:
        return None, "hippodrome absent du référentiel français"
    display, discipline, what = found
    if discipline is None:
        return None, f"{display} : {what} — pas de déduction possible"
    return discipline, f"{display} : {what}"


def consistency_warnings(hippodrome: str | None, discipline: Discipline) -> list[str]:
    """Signale une discipline incompatible avec l'hippodrome (ex. plat à Vincennes)."""
    found = lookup_hippodrome(hippodrome)
    if found is None or discipline is Discipline.UNKNOWN:
        return []
    display, only, what = found
    trot = discipline in (Discipline.TROT_ATTELE, Discipline.TROT_MONTE)
    if only is not None and discipline is not only:
        return [f"incohérence : {discipline.label_fr} annoncé à {display} ({what})"]
    if display in _TROT_ONLY and not trot:
        return [f"incohérence : {discipline.label_fr} annoncé à {display} ({what})"]
    return []


def race_country_for(hippodrome: str | None) -> tuple[str, str]:
    """Pays de la course et justification. La LONAB relaie des courses françaises."""
    found = lookup_hippodrome(hippodrome)
    if found is not None:
        return RACE_COUNTRY, f"hippodrome français reconnu : {found[0]}"
    return RACE_COUNTRY, (
        "pays par défaut : la LONAB relaie les courses françaises "
        "(hippodrome non reconnu dans le référentiel)"
    )


__all__ = [
    "DEFAULT_CLOSING_MINUTES",
    "FRENCH_HIPPODROMES",
    "LonabGame",
    "RACE_COUNTRY",
    "RACE_TZ",
    "RELAY_TZ",
    "RaceSchedule",
    "consistency_warnings",
    "discipline_hint",
    "localise",
    "lonab_game_for",
    "lookup_hippodrome",
    "normalise_game",
    "race_country_for",
    "schedule_for",
]
