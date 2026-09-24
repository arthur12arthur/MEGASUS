"""Module 1.1 — DataIngestion (+ module 1.2 — extraction).

Récupère le journal hippique officiel du jour et identifie sans ambiguïté la
course principale, avec vérification croisée opérateur/pays pour éviter de
récupérer la course d'un autre marché de la zone (Côte d'Ivoire, Mali, Togo)
ou d'un autre jour.

Trois providers, du plus fiable au plus automatisé :

  1. ``JsonFileProvider`` — lit un journal déjà structuré. Chemin reproductible
     et testable, utilisé par les tests et par l'exploitation manuelle.
  2. ``LonabProvider`` — scrape le journal officiel, télécharge le PDF et
     l'analyse. URL et expressions rationnelles configurables : le rendu
     exact d'un PDF LONAB doit être validé sur un vrai document.
  3. ``SyntheticProvider`` — génère des courses pour les backtests.

Règle absolue : le parseur n'invente jamais une valeur manquante. Toute ligne
non reconnue est rapportée dans ``ParseReport.unparsed`` plutôt que devinée.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hyperion.analysis.discipline import detect_discipline
from hyperion.config import Settings
from hyperion.models import Discipline, Horse, Race, RaceMeta
from hyperion import relay

#: Motifs d'identification d'un en-tête de course dans le texte du journal.
_MEETING_RE = re.compile(
    r"(?:R(?:ÉUNION|EUNION)?\s*)?(?P<meeting>R\d+)(?:\s*C\d{1,2})?\s*[-–—]\s*(?P<place>[^\n]{2,60})",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})[/\-.]\s*(?P<month>\d{1,2})[/\-.]\s*(?P<year>\d{2,4})"
)
_TIME_RE = re.compile(r"(?P<hour>\d{1,2})\s*[h:]\s*(?P<minute>\d{2})")
#: Heure de départ explicite, ex. « Départ 14h15 » (heure de Ouagadougou dans le programme LONAB).
_START_RE = re.compile(
    r"d[ée]part[^\d\n]{0,20}(?P<hour>\d{1,2})\s*[h:]\s*(?P<minute>\d{2})", re.IGNORECASE
)
#: Clôture des enjeux LONAB, ex. « Clôture des paris 14h05 ».
_CLOSE_RE = re.compile(
    r"cl[ôo]ture[^\d\n]{0,30}(?P<hour>\d{1,2})\s*[h:]\s*(?P<minute>\d{2})", re.IGNORECASE
)
#: Nom de l'épreuve, ex. « Prix de Bretagne » ou « Grand Prix d'Amérique ».
_NAME_RE = re.compile(
    r"\b((?:Grand\s+)?Prix\s[^\n—–]{2,60}?)(?=\s*(?:[—–]|$))", re.IGNORECASE | re.MULTILINE
)
#: Numéro de course, ex. « R1C4 » ou « Course n° 4 ».
_RACE_NUMBER_RE = re.compile(
    r"\bR\d+\s*C(?P<n1>\d{1,2})\b|\bcourse\s*n[°o]?\s*(?P<n2>\d{1,2})\b", re.IGNORECASE
)

#: Motif par défaut d'une ligne de partant : « 3  NOM DU CHEVAL  ...  (H/DRIVER)  cote »
DEFAULT_PARTANT_RE = re.compile(
    r"^\s*(?P<num>\d{1,2})\s+"
    r"(?P<name>[A-ZÀ-Ý][A-ZÀ-Ý' \-\.]{2,})\s+"
    r"(?P<rest>.*?)\s*$"
)
#: Cote : nombre décimal isolé, ex. « 12.4 » ou « 7/2 ».
_ODDS_RE = re.compile(r"(?<![\d/])(?P<odds>\d{1,3}[.,]\d)(?![\d/])")
_FRACTION_ODDS_RE = re.compile(r"(?P<num>\d{1,2})\s*/\s*(?P<den>\d{1,2})(?!\d)")
#: Driver entre parenthèses, ex. « (H/KEITA M.) ».
_DRIVER_RE = re.compile(r"\(\s*(?P<sex>[HMF])\s*/\s*(?P<driver>[^)]{2,40})\s*\)")
#: Musique : suite de chiffres séparés de lettres, ex. « 1a2a3a ».
_MUSIC_RE = re.compile(r"\b(?P<music>(?:\d[a-zA-Z]){2,}\d?)\b")
#: Gains en francs CFA, ex. « 1 250 000 FCFA ».
_GAINS_RE = re.compile(r"(?P<gains>\d[\d\s.,]{3,})\s*(?:F\s?CFA|FCFA|XOF|€|EUR\b|euros?\b)")


@dataclass
class ParseReport:
    """Diagnostic du parsing — traçabilité de ce qui n'a pas été compris."""

    lines_total: int = 0
    lines_parsed: int = 0
    blank_lines: int = 0
    unparsed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def candidate_lines(self) -> int:
        """Lignes non vides : les en-tetes du journal ne sont pas des partants."""
        return max(0, self.lines_total - self.blank_lines)

    @property
    def parse_rate(self) -> float:
        if not self.candidate_lines:
            return 0.0
        return self.lines_parsed / self.candidate_lines

    def as_dict(self) -> dict[str, Any]:
        return {
            "lines_total": self.lines_total,
            "lines_parsed": self.lines_parsed,
            "candidate_lines": self.candidate_lines,
            "parse_rate": round(self.parse_rate, 4),
            "unparsed": list(self.unparsed[:20]),
            "warnings": list(self.warnings),
        }


@dataclass
class IdentityCheck:
    """Résultat de la vérification croisée opérateur / pays / date."""

    ok: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": list(self.reasons)}


class IngestionError(RuntimeError):
    """Échec d'ingestion — message explicite, jamais d'exception silencieuse."""


# --------------------------------------------------------------------------
# Vérification d'identité
# --------------------------------------------------------------------------


def verify_identity(
    meta: RaceMeta,
    expected_date: dt.date | None = None,
    settings: Settings | None = None,
) -> IdentityCheck:
    """Vérifie qu'on analyse bien la bonne course.

    Opérateur et pays = ceux du MARCHÉ (LONAB, Burkina Faso) ; pays de la
    course = France (la LONAB est un relais) ; date demandée.
    """
    settings = settings or Settings()
    reasons: list[str] = []

    operator = (meta.operator or "").upper()
    if operator:
        allowed = [o.upper() for o in settings.allowed_operators]
        if not any(token in operator for token in allowed):
            reasons.append(
                f"opérateur '{meta.operator}' hors marché autorisé "
                f"({', '.join(settings.allowed_operators)})"
            )
    else:
        reasons.append("opérateur non identifié")

    country = (meta.country or "").lower()
    if country:
        allowed_countries = [c.lower() for c in settings.allowed_countries]
        if not any(token in country for token in allowed_countries):
            reasons.append(
                f"pays '{meta.country}' hors marché autorisé "
                f"({', '.join(settings.allowed_countries)})"
            )
    else:
        reasons.append("pays non identifié")

    race_country = (meta.race_country or "").lower()
    if race_country:
        allowed_race = [c.lower() for c in settings.allowed_race_countries]
        if not any(token in race_country for token in allowed_race):
            reasons.append(
                f"course courue en '{meta.race_country}' : la LONAB relaie les "
                f"courses de {', '.join(settings.allowed_race_countries)}"
            )

    if expected_date and meta.date and meta.date != expected_date:
        reasons.append(
            f"date du journal ({meta.date.isoformat()}) différente de la date "
            f"demandée ({expected_date.isoformat()})"
        )

    return IdentityCheck(ok=not reasons, reasons=reasons)


# --------------------------------------------------------------------------
# Parsing du texte du journal
# --------------------------------------------------------------------------


def parse_journal_text(
    text: str,
    race_type: Any = None,
    free_text: str = "",
    partant_re: re.Pattern[str] | None = None,
    programme_tz: str = relay.RELAY_TZ,
) -> tuple[Race, ParseReport]:
    """Analyse le texte d'un journal hippique en course structurée.

    Les heures du programme LONAB sont lues dans ``programme_tz`` (heure de
    Ouagadougou par défaut) ; la course se déroule en France.

    Le rendu exact d'un PDF LONAB/PMU'B doit être validé sur un document réel :
    les expressions sont surchargeables via ``partant_re``. Toute ligne non
    reconnue est rapportée, jamais devinée.
    """
    report = ParseReport()
    if not text or not text.strip():
        raise IngestionError("journal vide : rien à analyser")

    lines = [line.rstrip() for line in text.splitlines()]
    report.lines_total = len(lines)
    report.blank_lines = sum(1 for line in lines if not line.strip())

    # -- métadonnées -------------------------------------------------------
    meeting = None
    hippodrome = None
    race_date: dt.date | None = None
    start_time: dt.datetime | None = None
    distance_m: int | None = None

    match = _MEETING_RE.search(text)
    if match:
        meeting = match.group("meeting").upper()
        # « PARIS-VINCENNES — Départ 14h15 » -> « PARIS-VINCENNES »
        hippodrome = re.split(r"\s[-–—]\s", match.group("place"))[0].strip(" -–—")

    date_match = _DATE_RE.search(text)
    if date_match:
        day = int(date_match.group("day"))
        month = int(date_match.group("month"))
        year = int(date_match.group("year"))
        if year < 100:
            year += 2000
        try:
            race_date = dt.date(year, month, day)
        except ValueError:
            report.warnings.append(f"date illisible dans le journal : {date_match.group(0)}")

    distance_match = re.search(r"(?P<m>\d{3,4})\s*m\b", text)
    if distance_match:
        distance_m = int(distance_match.group("m"))

    def _at(found: re.Match[str] | None) -> dt.datetime | None:
        if not found or not race_date:
            return None
        try:
            return dt.datetime(
                race_date.year, race_date.month, race_date.day,
                int(found.group("hour")), int(found.group("minute")),
                tzinfo=ZoneInfo(programme_tz),
            )
        except ValueError:
            return None

    # « Départ 14h15 » est prioritaire ; à défaut, première heure du texte
    # qui n'est pas celle de la clôture.
    close_match = _CLOSE_RE.search(text)
    betting_close = _at(close_match)
    start_match = _START_RE.search(text)
    if start_match is None:
        for candidate in _TIME_RE.finditer(text):
            if close_match and close_match.start() <= candidate.start() < close_match.end():
                continue
            start_match = candidate
            break
    start_time = _at(start_match)
    if start_match and start_time is None:
        report.warnings.append("heure de départ illisible")

    race_number = None
    number_match = _RACE_NUMBER_RE.search(text)
    if number_match:
        race_number = int(number_match.group("n1") or number_match.group("n2"))

    name_match = _NAME_RE.search(text)
    race_name = name_match.group(1).strip() if name_match else None
    bet_type = relay.normalise_game(text)
    race_country, country_why = relay.race_country_for(hippodrome)
    report.warnings.append(f"pays de la course : {country_why}")

    discipline, why = detect_discipline(race_type, free_text or text)
    if discipline is Discipline.UNKNOWN:
        hinted, hint_why = relay.discipline_hint(hippodrome)
        if hinted is not None:
            discipline, why = hinted, f"{why} ; repli hippodrome ({hint_why})"
    report.warnings.append(f"discipline : {why}")
    report.warnings.extend(relay.consistency_warnings(hippodrome, discipline))

    # -- partants ----------------------------------------------------------
    pattern = partant_re or DEFAULT_PARTANT_RE
    horses: list[Horse] = []
    for line in lines:
        if not line.strip():
            continue
        found = pattern.match(line)
        if not found:
            report.unparsed.append(line[:120])
            continue
        rest = found.groupdict().get("rest") or ""
        odds = _extract_odds(rest)
        driver_match = _DRIVER_RE.search(rest)
        music_match = _MUSIC_RE.search(rest)
        gains_match = _GAINS_RE.search(rest)
        horses.append(
            Horse(
                horse_id=_slug(found.group("name")),
                name=found.group("name").strip(),
                number=int(found.group("num")),
                driver=driver_match.group("driver").strip() if driver_match else None,
                odds_pdf=odds,
                music=music_match.group("music") if music_match else None,
                gains=_parse_gains(gains_match.group("gains")) if gains_match else None,
                raw={"line": line},
            )
        )
        report.lines_parsed += 1

    if not horses:
        raise IngestionError(
            "aucun partant reconnu dans le journal — le rendu du PDF ne "
            "correspond pas au motif attendu. Fournissez un journal structuré "
            "en JSON (JsonFileProvider) ou renseignez un motif personnalisé."
        )

    meta = RaceMeta(
        operator=settings_allowed_operator(text),
        country=_guess_country(text),
        race_country=race_country,
        meeting=meeting,
        hippodrome=hippodrome,
        date=race_date,
        start_time=start_time,
        betting_close=betting_close,
        bet_type=bet_type,
        race_number=race_number,
        name=race_name,
        distance_m=distance_m,
        discipline=discipline,
        race_type=str(race_type) if race_type is not None else None,
    )
    race_id = _race_id(meta, race_date)
    return Race(meta=meta, horses=horses, race_id=race_id), report


def settings_allowed_operator(text: str) -> str | None:
    upper = text.upper()
    for token in ("LONAB", "PMU'B", "PMUB", "PMU"):
        if token in upper:
            return token
    return None


def _guess_country(text: str) -> str | None:
    """Pays du MARCHÉ de paris (l'opérateur relais), pas celui de la course."""
    upper = text.upper()
    if "LONAB" in upper or "PMU'B" in upper or "PMUB" in upper:
        return "Burkina Faso"
    for token, country in (
        ("BURKINA", "Burkina Faso"),
        ("OUAGADOUGOU", "Burkina Faso"),
        ("BOBO-DIOULASSO", "Burkina Faso"),
        ("ABIDJAN", "Côte d'Ivoire"),
        ("BAMAKO", "Mali"),
        ("LOME", "Togo"),
        ("LOMÉ", "Togo"),
    ):
        if token in upper:
            return country
    return None


def _extract_odds(text: str) -> float | None:
    fraction = _FRACTION_ODDS_RE.search(text)
    if fraction:
        num = float(fraction.group("num"))
        den = float(fraction.group("den"))
        if den:
            return round(1.0 + num / den, 2)
    decimal = _ODDS_RE.search(text)
    if decimal:
        try:
            return float(decimal.group("odds").replace(",", "."))
        except ValueError:
            return None
    return None


def _parse_gains(raw: str) -> float | None:
    cleaned = re.sub(r"[\s.,]", "", raw)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _slug(text: str) -> str:
    table = str.maketrans("àâäéèêëîïôöùûüçñ'", "aaaeeeeiioouuucn-")
    slug = re.sub(r"\s+", "-", text.translate(table).strip().lower())
    return re.sub(r"-{2,}", "-", slug).strip("-")


def _race_id(meta: RaceMeta, date: dt.date | None) -> str:
    stamp = (date or dt.date.today()).strftime("%Y%m%d")
    where = _slug(meta.hippodrome or "inconnu")
    course = meta.race_number or 1
    meeting = (meta.meeting or "").lower()
    return f"{stamp}-{where}-{meeting}c{course}" if meeting else f"{stamp}-{where}-c{course}"


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


class JsonFileProvider:
    """Provider JSON — chemin fiable et reproductible."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def fetch(self, date: dt.date | None = None) -> Race:
        if not self.path.exists():
            raise IngestionError(f"journal introuvable : {self.path}")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return Race.from_dict(payload)


class DirectoryProvider:
    """Provider qui cherche le journal du jour dans un répertoire."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)

    def fetch(self, date: dt.date | None = None) -> Race:
        target = date or dt.date.today()
        candidates = [
            self.directory / f"{target.isoformat()}.json",
            self.directory / f"{target:%Y%m%d}.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return JsonFileProvider(candidate).fetch(target)
        available = sorted(p.name for p in self.directory.glob("*.json")) if self.directory.exists() else []
        raise IngestionError(
            f"aucun journal pour {target.isoformat()} dans {self.directory} "
            f"(disponibles : {', '.join(available) or 'aucun'})"
        )


class LonabProvider:
    """Provider du journal officiel LONAB/PMU'B.

    L'URL et les motifs sont configurables : le rendu exact d'un PDF officiel
    doit être validé sur un document réel. En cas d'échec, le pipeline doit
    basculer sur le provider JSON (source de secours).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        fallback: Any | None = None,
        partant_re: re.Pattern[str] | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.fallback = fallback
        self.partant_re = partant_re

    def fetch(self, date: dt.date | None = None) -> Race:
        target = date or dt.date.today()
        if not self.settings.lonab_url:
            if self.fallback is not None:
                return self.fallback.fetch(target)
            raise IngestionError(
                "HYPERION_LONAB_URL n'est pas configurée — renseignez l'URL du "
                "journal officiel, ou fournissez un provider JSON en secours."
            )
        import requests

        last_error: Exception | None = None
        for attempt in range(self.settings.lonab_max_retries + 1):
            try:
                response = requests.get(
                    self.settings.lonab_url,
                    timeout=self.settings.lonab_timeout_s,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; HyperionBot/1.0)"},
                )
                response.raise_for_status()
                return self._from_response(response, target)
            except Exception as exc:
                last_error = exc
                if attempt < self.settings.lonab_max_retries:
                    import time

                    time.sleep(1.5 * (attempt + 1))
        if self.fallback is not None:
            return self.fallback.fetch(target)
        raise IngestionError(
            f"récupération du journal LONAB impossible : {last_error}"
        )

    def _from_response(self, response: Any, target: dt.date) -> Race:
        content_type = (response.headers.get("content-type") or "").lower()
        if "pdf" in content_type or response.content[:4] == b"%PDF":
            text = extract_pdf_bytes(response.content)
        else:
            text = response.text
        race, report = parse_journal_text(text, partant_re=self.partant_re)
        check = verify_identity(race.meta, target, self.settings)
        if not check.ok:
            raise IngestionError(
                "journal récupéré mais identité non conforme : "
                + " ; ".join(check.reasons)
            )
        return race


def extract_pdf_bytes(payload: bytes) -> str:
    """Extrait le texte d'un PDF (nécessite le paquet optionnel ``pypdf``)."""
    try:
        import io

        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dépendance optionnelle
        raise IngestionError(
            "extraction PDF indisponible : installez le paquet optionnel "
            "« pip install hyperion[pdf] »"
        ) from exc
    reader = PdfReader(io.BytesIO(payload))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(pages)


def extract_pdf_file(path: Path | str) -> str:
    return extract_pdf_bytes(Path(path).read_bytes())


__all__ = [
    "IngestionError",
    "ParseReport",
    "IdentityCheck",
    "verify_identity",
    "parse_journal_text",
    "JsonFileProvider",
    "DirectoryProvider",
    "LonabProvider",
    "extract_pdf_bytes",
    "extract_pdf_file",
]
