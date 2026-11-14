"""Pronostics externes réels : extraction par étiquettes, puis secours Gemini + recherche web.

Pourquoi ce module : lire les noms de chevaux dans l'ordre où ils apparaissent sur une
page donne l'ordre du programme, pas un pronostic. Deux méthodes honnêtes à la place :

1. ``extract_labeled_picks`` : cherche des blocs que les sites publient explicitement
   (« Bases : 3 - 13 - 14 », « Belles chances : 4 - 9 - 7 - 10 - 8 », « Délaissés : ... »)
   et ne retient que les numéros situés derrière ces étiquettes.
2. ``gemini_web_picks`` (secours) : demande à Gemini, avec la recherche Google activée,
   les pronostics publiés pour la course, puis REJETTE toute source dont le domaine
   n'apparaît pas dans les pages réellement consultées par la recherche (anti-invention).

Dans les deux cas, une liste qui sort en ordre croissant (probable liste de partants), qui
compte moins de 5 numéros distincts ou qui cite des numéros absents de la course est écartée.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from typing import Any, Mapping, Sequence

from hyperion.models import Race

MIN_PICKS = 5
_LABEL_RE = re.compile(
    r"(bases?|belles?\s+chances?|outsiders?|d[ée]laiss[ée]s?)\s*:?\s*"
    r"((?:\d{1,2}\s*[-–—,/·]\s*)*\d{1,2})(?!\d)",
    re.IGNORECASE,
)


def strip_html(text: str) -> str:
    """Texte brut : retire scripts, styles et balises, décode les entités."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _numbers(raw: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d{1,2}", raw)]


def _dedup(seq: Sequence[int]) -> list[int]:
    seen: set[int] = set()
    return [n for n in seq if not (n in seen or seen.add(n))]


def _to_ids(numbers: Sequence[int], race: Race) -> list[str]:
    by_number = {h.number: h.horse_id for h in race.runners if h.number is not None}
    return [by_number[n] for n in numbers if n in by_number]


def _plausible(core: Sequence[int]) -> bool:
    """Au moins 5 numéros distincts, et pas simplement l'ordre croissant des partants."""
    return len(core) >= MIN_PICKS and list(core) != sorted(core)


def extract_labeled_picks(text: str, race: Race) -> tuple[list[str], str] | None:
    """Premier bloc « Bases / Belles chances » exploitable, ou ``None``.

    Retourne ``(classement d'identifiants, note)``. Les « Délaissés » sont exclus ; les
    « Outsiders » sont ajoutés après les chances principales.
    """
    plain = strip_html(text)
    blocks: list[dict[str, list[int]]] = []
    current: dict[str, list[int]] = {}
    seen: set[str] = set()
    for match in _LABEL_RE.finditer(plain):
        label = re.sub(r"[^a-z]", "", match.group(1).lower().replace("é", "e"))[:4]
        if label not in {"base", "bell", "outs", "dela"}:
            continue
        kind = {"base": "core", "bell": "core", "outs": "out", "dela": "drop"}[label]
        if label in seen:  # même étiquette qu'avant : nouveau bloc (autre expert ou autre course)
            blocks.append(current)
            current, seen = {}, set()
        seen.add(label)
        current[kind] = current.get(kind, []) + _numbers(match.group(2))
        if kind == "drop":  # « Délaissés » termine un bloc
            blocks.append(current)
            current, seen = {}, set()
    if current:
        blocks.append(current)
    valid = {h.number for h in race.runners if h.number is not None}
    for block in blocks:
        core = [n for n in _dedup(block.get("core", [])) if n in valid]
        if not _plausible(core):
            continue
        extra = [n for n in _dedup(block.get("out", [])) if n in valid and n not in core]
        ranking = _to_ids([*core, *extra], race)
        return ranking, f"pronostic étiqueté (« bases / belles chances ») : {len(core)} chances retenues"
    return None


# --------------------------------------------------------------------------
# Secours : Gemini + recherche web
# --------------------------------------------------------------------------

_PROMPT = """Tu cherches sur le web les pronostics PUBLIÉS pour une course hippique française.
Course : {date}, {hippodrome}, réunion {reunion}, course {course} ({nom}).
Partants (numéro - nom) : {partants}.
Sources voulues : {sources}.
Règles strictes :
- Ne retiens qu'un pronostic réellement publié pour CETTE course et CE jour, sous forme d'une
  liste de numéros classés du plus au moins favori. N'invente rien ; si rien n'est trouvé pour
  une source, omets-la.
- Retourne UNIQUEMENT un objet JSON, sans texte autour, de la forme
  {{"sources": {{"Equidia": [4, 9, 7, 10, 8]}}}} (5 à 8 numéros par source).
"""


def _grounding_titles(body: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for cand in body.get("candidates") or []:
        meta = cand.get("groundingMetadata") or {}
        for chunk in meta.get("groundingChunks") or []:
            web = chunk.get("web") or {}
            out.append(f"{web.get('title', '')} {web.get('uri', '')}".lower())
    return out


def gemini_web_picks(
    race: Race,
    sources: Sequence[str],
    settings: Any,
    post: Any = None,
    when: dt.datetime | None = None,
) -> dict[str, tuple[list[str], str]]:
    """Pronostics trouvés par Gemini (recherche Google), validés. ``{source: (ids, note)}``."""
    from hyperion.external_registry import registry_by_name
    from hyperion.gemini import API_ROOT, _first_text, _parse_json

    if not getattr(settings, "gemini_keys", ()) or not sources:
        return {}
    if post is None:
        import requests

        post = requests.post
    registry = registry_by_name()
    wanted = [s for s in sources if s in registry]
    if not wanted:
        return {}
    meta = race.meta
    prompt = _PROMPT.format(
        date=meta.date.isoformat() if meta.date else "",
        hippodrome=meta.hippodrome or "",
        reunion=meta.meeting or "",
        course=meta.race_number or "",
        nom=meta.name or "",
        partants="; ".join(f"{h.number} - {h.name}" for h in race.runners if h.number is not None),
        sources=", ".join(wanted),
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.0},
    }
    valid = {h.number for h in race.runners if h.number is not None}
    for model in settings.gemini_models:
        for key in settings.gemini_keys:
            try:
                response = post(f"{API_ROOT}/{model}:generateContent", params={"key": key}, json=payload, timeout=90)
                if response.status_code != 200:
                    continue
                body = response.json()
            except Exception:
                continue
            text = _first_text(body)
            data = _parse_json(text) if text else None
            claimed = (data or {}).get("sources") if isinstance(data, dict) else None
            if not isinstance(claimed, Mapping):
                continue
            titles = _grounding_titles(body)
            found: dict[str, tuple[list[str], str]] = {}
            for name, raw in claimed.items():
                if name not in registry or not isinstance(raw, Sequence) or isinstance(raw, str):
                    continue
                numbers = _dedup([int(n) for n in raw if str(n).strip().isdigit()])
                numbers = [n for n in numbers if n in valid]
                domain = registry[name].domain.lower()
                if not _plausible(numbers):
                    continue
                if not any(domain in t for t in titles):
                    continue  # affirmation non étayée par une page réellement consultée
                found[name] = (
                    _to_ids(numbers, race),
                    f"Gemini + recherche web, étayé par {domain} ({len(numbers)} numéros)",
                )
            return found
    return {}
