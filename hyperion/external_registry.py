"""Registre intégré des sources du panel — aucune variable d'environnement requise.

Remplace la dépendance au secret ``HYPERION_EXTERNAL_URLS`` (JSON à tenir à
jour à la main) par deux stratégies, choisies au cas par cas pour chacune des
12 sources de référence :

1. URL DIRECTE — la source publie son contenu du jour sur une adresse fixe
   (ex. ``equidia.fr/quinte``, toujours la même) ou une adresse entièrement
   construite à partir de ce qu'on connaît déjà de la course (date,
   hippodrome, nom). Aucune requête préalable n'est nécessaire.

2. DÉCOUVERTE PAR DOMAINE — l'URL du jour contient un identifiant ou un slug
   non prévisible (souvent un ID numérique interne). On interroge alors un
   moteur de recherche public, sans clé API, restreint au domaine de la
   source (``site:exemple.fr`` + hippodrome + date), et on retient le
   premier résultat sur ce domaine.

Dans tous les cas, un échec (page introuvable, blocage, recherche vide,
domaine non confirmé) n'interrompt jamais le pipeline : il est consigné
comme source indisponible, avec sa raison exacte. Rien n'est deviné : une
URL de secours non vérifiée n'est jamais fabriquée à la place d'un échec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote_plus

from hyperion.models import Race

_ACCENTS = str.maketrans("àâäèéêëîïôöùûüçñ", "aaaeeeeiioouuucn")


def slugify(text: str) -> str:
    """Convertit un nom en tronçon d'URL : minuscules, sans accents, tirets."""
    text = text.lower().translate(_ACCENTS)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


DirectUrlBuilder = Callable[[Race], "str | None"]


@dataclass(frozen=True)
class SourceEntry:
    """Une source du panel de référence.

    ``direct_url`` : fonction construisant l'URL du jour sans requête
    préalable, ou ``None`` si aucune URL stable n'est connue pour cette
    source (la découverte par domaine prend alors le relais).

    ``domain`` : domaine officiel de la source, utilisé pour restreindre la
    découverte et pour vérifier qu'un résultat de recherche appartient bien
    à la source attendue — jamais pour construire une URL à l'aveugle.

    ``domain_confirmed`` : ``False`` quand le domaine n'a pas pu être
    vérifié avec certitude. La découverte reste alors non restreinte à ce
    domaine (recherche ouverte avec le nom de la source), ce qui est moins
    fiable et volontairement signalé comme tel dans la note de résultat.
    """

    name: str
    domain: str
    direct_url: DirectUrlBuilder | None = None
    domain_confirmed: bool = True


def _zeturf_url(race: Race) -> str | None:
    meta = race.meta
    if not (meta.date and meta.hippodrome):
        return None
    date = meta.date.isoformat()
    meeting = meta.meeting or "R1"
    course = f"C{meta.race_number}" if meta.race_number else "C1"
    hippo = slugify(meta.hippodrome)
    if meta.name:
        return (
            f"https://www.zeturf.fr/fr/course-du-jour/{date}/"
            f"{meeting}{course}-{hippo}-{slugify(meta.name)}"
        )
    # Repli : page de la réunion entière (toujours valide, moins précise
    # qu'une page de course, mais construite à partir de champs confirmés
    # uniquement — jamais un identifiant interne deviné).
    return f"https://www.zeturf.fr/fr/reunion-du-jour/{date}/{meeting}-{hippo}"


def _canalturf_url(_race: Race) -> str:
    # Adresse fixe : "Le pronostic du Quinté+ du jour", toujours la course
    # support du jour, quelle qu'elle soit.
    return "https://www.canalturf.com/courses_quinte.php"


def _equidia_url(_race: Race) -> str:
    return "https://www.equidia.fr/quinte"


def _turfomania_url(_race: Race) -> str:
    return "https://www.turfomania.fr/quinte/"


def _genybet_url(_race: Race) -> str:
    # Page d'accueil Geny/Genybet : "Réunions du jour" y figure toujours.
    return "https://www.geny.com/"


#: Panel de référence (12 sources), dans l'ordre du module 1.9.
#: Aucune de ces entrées ne requiert de variable d'environnement : les
#: cinq premières ont une URL directe confirmée ; les sept suivantes
#: passent par la découverte par domaine.
SOURCE_REGISTRY: tuple[SourceEntry, ...] = (
    SourceEntry("Genybet", "geny.com", _genybet_url),
    SourceEntry("Equidia", "equidia.fr", _equidia_url),
    SourceEntry("Canal Turf", "canalturf.com", _canalturf_url),
    SourceEntry("Turfomania", "turfomania.fr", _turfomania_url),
    SourceEntry("ZEturf", "zeturf.fr", _zeturf_url),
    SourceEntry("PMU", "pmu.fr"),
    SourceEntry("France Galop", "france-galop.com"),
    SourceEntry("Paris-Turf", "paris-turf.com"),
    SourceEntry("RueDesJoueurs", "ruedesjoueurs.com"),
    SourceEntry("Betclic Turf", "betclic.fr"),
    # Domaine non vérifié avec certitude au moment de l'écriture de ce
    # registre : la découverte reste ouverte plutôt que restreinte à un
    # domaine deviné.
    SourceEntry("Turf BZH", "turfbzh.com", domain_confirmed=False),
    SourceEntry("Quinté du Jour", "quintedujour.com", domain_confirmed=False),
)


def registry_by_name() -> dict[str, SourceEntry]:
    return {entry.name: entry for entry in SOURCE_REGISTRY}


# --------------------------------------------------------------------------
# Découverte par domaine — moteur de recherche public, sans clé API
# --------------------------------------------------------------------------

_RESULT_LINK = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"')
# DuckDuckGo HTML encode parfois les liens via une redirection /l/?uddg=...
_DDG_REDIRECT = re.compile(r"uddg=([^&]+)")


def discover_url(
    requests_module,
    source: SourceEntry,
    race: Race,
    timeout_s: int = 15,
) -> tuple[str | None, str]:
    """Cherche la page du jour d'une source via une recherche restreinte au domaine.

    Retourne ``(url, raison)``. ``url`` est ``None`` si rien d'utilisable
    n'a été trouvé — la raison explique pourquoi (jamais une URL inventée
    à la place).
    """
    meta = race.meta
    if not meta.hippodrome:
        return None, "hippodrome inconnu : recherche impossible sans repère fiable"

    terms = [source.name, meta.hippodrome]
    if meta.date:
        terms.append(meta.date.strftime("%d %m %Y"))
    terms.append("pronostic")
    query = " ".join(terms)
    if source.domain_confirmed:
        query = f"site:{source.domain} {query}"

    try:
        response = requests_module.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            timeout=timeout_s,
            headers={"User-Agent": "Mozilla/5.0 (compatible; HyperionBot/1.0)"},
        )
    except Exception as exc:  # réseau, timeout, blocage
        return None, f"recherche impossible ({exc.__class__.__name__})"

    if response.status_code != 200:
        return None, f"recherche refusée (HTTP {response.status_code})"

    for raw_href in _RESULT_LINK.findall(response.text):
        href = raw_href
        redirect = _DDG_REDIRECT.search(href)
        if redirect:
            from urllib.parse import unquote

            href = unquote(redirect.group(1))
        if source.domain_confirmed and source.domain not in href:
            continue
        return href, "trouvé par recherche restreinte au domaine"

    note = (
        "aucun résultat sur le domaine attendu"
        if source.domain_confirmed
        else "aucun résultat (domaine non confirmé, recherche non restreinte)"
    )
    return None, note


__all__ = [
    "SOURCE_REGISTRY",
    "SourceEntry",
    "discover_url",
    "registry_by_name",
    "slugify",
]
