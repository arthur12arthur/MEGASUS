"""Récupération AUTOMATIQUE du journal LONAB du jour (sans lien fourni).

Reprend la méthode validée en réel sur Pegasus : on parcourt la page publique
« programme PMU'B » de la LONAB ligne par ligne, on retient UNIQUEMENT la ligne
dont le titre porte exactement la date demandée, puis on suit son vrai lien
« Télécharger ». On ne prend jamais « le premier PDF trouvé » et on ne
reconstruit jamais l'adresse du fichier.

Les partants sont lus avec le motif observé sur un vrai PDF LONAB
(numéro, nom, gains, cote « x/1 »). Rien n'est deviné : une ligne non
reconnue est rapportée, une donnée absente reste ``None``.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from urllib.parse import urljoin

from hyperion.config import Settings
from hyperion.ingestion import (
    IngestionError,
    _race_id,
    _slug,
    extract_pdf_bytes,
    parse_journal_text,
)
from hyperion.models import Horse, Race, RaceMeta

PROGRAMME_URL = "https://lonab.bf/fr/programme-pmub"
MAX_PAGES = 20

_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}


@dataclass(frozen=True)
class JournalLink:
    date: dt.date
    title: str
    pdf_url: str


def parse_title_date(title: str) -> dt.date | None:
    """« journal hippique PMU'B du 14 septembre 2026 » -> 2026-09-14."""
    found = re.search(r"du\s+(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})", title, re.I)
    if not found:
        return None
    month = _MONTHS.get(found.group(2).lower())
    if not month:
        return None
    try:
        return dt.date(int(found.group(3)), month, int(found.group(1)))
    except ValueError:
        return None


def find_journal_link(html: str, wanted: dt.date, base_url: str = PROGRAMME_URL) -> JournalLink:
    from bs4 import BeautifulSoup  # dépendance déjà requise par le projet

    soup = BeautifulSoup(html, "html.parser")
    for row in soup.find_all("tr"):
        title_node = row.find(class_=re.compile(r"views-field-title"))
        link = row.find("a", string=lambda s: bool(s and "télécharger" in s.lower()))
        if title_node is None or link is None or not link.get("href"):
            continue
        title = " ".join(title_node.get_text(" ", strip=True).split())
        if parse_title_date(title) == wanted:
            return JournalLink(wanted, title, urljoin(base_url, link["href"]))
    raise LookupError(f"journal PMU'B introuvable pour {wanted.isoformat()}")


# Format observé sur un vrai journal : numéro, nom, colonnes, gains, cote « x/1 ».
_LINE = re.compile(r"^\s*(?P<num>\d{1,2})\s+(?P<rest>[A-ZÀ-ÖØ-Ý0-9][^\n]+?)\s*$", re.M)
_GAINS_ODDS = re.compile(
    r"(?<![\d.])(?P<gains>\d{1,3}(?: \d{3})?)\s{2,}(?P<odds>\d+(?:[.,]\d+)?)/1"
)
_DRIVER = re.compile(r"[A-Z]{1,3}(?:\.[A-Z]{0,3})+")


def parse_real_horses(text: str) -> tuple[list[Horse], list[str]]:
    horses: list[Horse] = []
    unparsed: list[str] = []
    seen: set[int] = set()
    for raw in text.splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        num = int(m.group("num"))
        rest = m.group("rest")
        p = _GAINS_ODDS.search(rest)
        if not (1 <= num <= 99) or not p:
            if 1 <= num <= 99 and len(rest.split()) >= 2:
                unparsed.append(raw.strip()[:100])
            continue
        if num in seen:  # ligne de tableau répétée : on garde la première
            continue
        before = rest[: p.start("gains")].strip()
        tokens = before.split()
        cut = next((i for i, t in enumerate(tokens) if _DRIVER.fullmatch(t)), None)
        cols = [c.strip() for c in re.split(r"\s{2,}", before) if c.strip()]
        if cut:
            name = " ".join(tokens[:cut])
        else:
            name = cols[0] if cols else (tokens[0] if tokens else f"Numéro {num}")
        seen.add(num)
        horses.append(Horse(
            horse_id=_slug(name), name=name, number=num,
            odds_pdf=float(p.group("odds").replace(",", ".")),
            gains=float(p.group("gains").replace(" ", "")),
            raw={"line": raw.strip()},
        ))
    return horses, unparsed


def _pdf_text(payload: bytes) -> str:
    """pdftotext -layout conserve les colonnes ; pypdf en repli."""
    import subprocess

    base = extract_pdf_bytes(payload)
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", "-", "-"], input=payload,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout.decode("utf-8", errors="replace")
        return out if len(out) >= len(base) else base
    except (OSError, subprocess.CalledProcessError):
        return base


class LonabAutoProvider:
    """Trouve, télécharge et analyse le journal du jour sans URL fournie."""

    def __init__(self, settings: Settings | None = None, session=None,
                 programme_url: str = PROGRAMME_URL) -> None:
        self.settings = settings or Settings()
        self.programme_url = programme_url
        if session is None:
            import requests

            session = requests.Session()
            session.headers["User-Agent"] = "Mozilla/5.0 (compatible; HyperionBot/1.0)"
        self.session = session

    def locate(self, wanted: dt.date) -> JournalLink:
        from bs4 import BeautifulSoup

        url = self.programme_url
        for _ in range(MAX_PAGES):
            page = self.session.get(url, timeout=self.settings.lonab_timeout_s)
            page.raise_for_status()
            try:
                return find_journal_link(page.text, wanted, url)
            except LookupError:
                nxt = BeautifulSoup(page.text, "html.parser").find("a", rel="next")
                if nxt is None or not nxt.get("href"):
                    break
                url = urljoin(url, nxt["href"])
        raise IngestionError(
            f"journal PMU'B du {wanted.isoformat()} introuvable sur la LONAB "
            "(pas encore publié, ou structure du site modifiée)"
        )

    def fetch(self, date: dt.date | None = None) -> Race:
        target = date or dt.date.today()
        link = self.locate(target)
        pdf = self.session.get(link.pdf_url, timeout=self.settings.lonab_timeout_s)
        pdf.raise_for_status()
        text = _pdf_text(pdf.content)
        horses, unparsed = parse_real_horses(text)
        if not horses:
            raise IngestionError(
                f"PDF téléchargé ({link.pdf_url}) mais aucun partant reconnu — "
                "le gabarit LONAB a peut-être changé"
            )
        # Métadonnées (hippodrome, discipline, heures) : meilleur effort ; les
        # champs illisibles restent None, jamais devinés.
        meta = RaceMeta(operator="LONAB", country="Burkina Faso", date=target,
                        source_url=link.pdf_url)
        try:
            parsed, _ = parse_journal_text(text)
            meta = parsed.meta
            meta.source_url = link.pdf_url
            meta.date = meta.date or target
        except IngestionError:
            pass
        return Race(meta=meta, horses=horses, race_id=_race_id(meta, target))
