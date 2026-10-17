"""Lecture de la « musique » PMU (suite des dernières places d'un cheval).

Format réel (plus récente en premier) : ``3p2p4p4p2p(25)2p2p1p5p``.

* chiffre + lettre : place + discipline (p plat, h haies, s steeple, c cross,
  a attelé, m monté) ; ``0`` = non placé (au-delà de la 9e place) ;
* lettre d'incident (D disqualifié, A arrêté, T tombé, R restée) + discipline ;
* ``(25)`` : changement d'année (2025) — ce n'est PAS une place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"\((?P<year>\d{2})\)|(?P<place>[0-9DATRdatr])(?P<disc>[a-zA-Z])?")
_INCIDENTS = set("DATRdatr")


@dataclass(frozen=True)
class Music:
    #: 1 = gagné … 9, 0 = non placé ou incident ; plus récente en premier.
    places: tuple[int, ...]
    incidents: int = 0
    disciplines: tuple[str, ...] = ()

    @property
    def runs(self) -> int:
        return len(self.places)


def parse_music(raw: str | None) -> Music:
    """Décode une musique PMU. Tolère les valeurs vides ou inattendues."""
    if not raw:
        return Music(places=())
    places: list[int] = []
    discs: list[str] = []
    incidents = 0
    for match in _TOKEN.finditer(str(raw)):
        if match.group("year"):
            continue  # marqueur d'année, jamais une place
        token = match.group("place")
        if token in _INCIDENTS:
            incidents += 1
            places.append(0)
        else:
            places.append(int(token))
        if match.group("disc"):
            discs.append(match.group("disc").lower())
    return Music(places=tuple(places), incidents=incidents, disciplines=tuple(discs))


__all__ = ["Music", "parse_music"]
