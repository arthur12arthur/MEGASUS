"""Module 1.2 — GeminiManager.

Extraction structurée du contenu du journal (partants, conditions, musique,
cotes, commentaires) et gestion des appels IA.

Point de fragilité historique du système : erreurs de parsing d'objet et
modèles obsolètes. Trois garde-fous sont donc en place :

  1. rotation des clés API — une clé en quota dépassé ne fait pas échouer
     l'extraction ;
  2. cascade de modèles — un nom de modèle retiré côté Google est
     automatiquement remplacé par le suivant de la liste ;
  3. extraction multi-passes avec validation du schéma — une réponse
     incomplète est re-demandée, et une réponse invalide est REJETÉE plutôt
     que partiellement utilisée.

Sans clé configurée, le manager est simplement inopérant : le pipeline
bascule sur le parsing direct, il n'échoue jamais à cause de l'IA.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from hyperion.config import Settings

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

#: Schéma attendu de la réponse (contrat entre l'IA et le pipeline).
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "meta": {
            "type": "object",
            "properties": {
                "operator": {"type": "string"},
                "country": {"type": "string"},
                "hippodrome": {"type": "string"},
                "meeting": {"type": "string"},
                "race_type": {"type": "string"},
                "distance_m": {"type": "integer"},
                "start_time": {"type": "string"},
            },
        },
        "horses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "number": {"type": "integer"},
                    "driver": {"type": "string"},
                    "odds": {"type": "number"},
                    "music": {"type": "string"},
                    "gains": {"type": "number"},
                    "comment": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    "required": ["horses"],
}

EXTRACTION_PROMPT = """Tu extrais les données d'un journal hippique officiel.
Retourne UNIQUEMENT un objet JSON valide, sans texte autour, sans balise markdown.

Schéma attendu :
{schema}

Règles strictes :
- N'invente aucune valeur. Si un champ est absent du document, omets-le.
- Les cotes sont des nombres décimaux (12.4), pas des fractions.
- La musique est la suite des places des dernières courses (ex. "1a2a3a").
- Les gains sont des nombres entiers, sans séparateur de milliers.

Document :
{document}
"""


@dataclass
class ExtractionResult:
    """Résultat d'une extraction IA."""

    data: dict[str, Any] = field(default_factory=dict)
    model_used: str | None = None
    passes: int = 0
    ok: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "model_used": self.model_used,
            "passes": self.passes,
            "errors": list(self.errors),
            "data": self.data,
        }


class GeminiManager:
    """Extraction structurée avec rotation de clés et cascade de modèles."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    # -- disponibilité -----------------------------------------------------

    @property
    def available(self) -> bool:
        return bool(self.settings.gemini_keys)

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "keys_configured": len(self.settings.gemini_keys),
            "models": list(self.settings.gemini_models),
        }

    # -- extraction --------------------------------------------------------

    def extract(self, document: str, passes: int | None = None) -> ExtractionResult:
        """Extrait les données structurées d'un document texte."""
        if not self.available:
            return ExtractionResult(
                ok=False,
                errors=[
                    "aucune clé Gemini configurée (HYPERION_GEMINI_KEYS) — "
                    "extraction IA inopérante, bascule sur le parsing direct"
                ],
            )
        import requests

        passes = passes or self.settings.gemini_max_passes
        prompt = EXTRACTION_PROMPT.format(
            schema=json.dumps(RESPONSE_SCHEMA, ensure_ascii=False, indent=2),
            document=document[:200_000],
        )
        errors: list[str] = []
        for attempt in range(max(1, passes)):
            for model in self.settings.gemini_models:
                for key in self.settings.gemini_keys:
                    payload = {
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "temperature": 0.0,
                            "responseMimeType": "application/json",
                            "responseSchema": RESPONSE_SCHEMA,
                        },
                    }
                    url = f"{API_ROOT}/{model}:generateContent"
                    try:
                        response = requests.post(
                            url,
                            params={"key": key},
                            json=payload,
                            timeout=60,
                        )
                    except Exception as exc:
                        errors.append(f"{model} : réseau ({exc})")
                        continue
                    if response.status_code != 200:
                        errors.append(f"{model} : HTTP {response.status_code}")
                        # Quota épuisé ou modèle retiré : on tente le suivant.
                        continue
                    try:
                        body = response.json()
                    except ValueError:
                        errors.append(f"{model} : réponse non JSON")
                        continue
                    text = _first_text(body)
                    if not text:
                        errors.append(f"{model} : réponse vide")
                        continue
                    data = _parse_json(text)
                    if data is None:
                        errors.append(f"{model} : JSON illisible dans la réponse")
                        continue
                    if not _validate(data):
                        errors.append(f"{model} : schéma incomplet")
                        continue
                    return ExtractionResult(
                        data=data,
                        model_used=model,
                        passes=attempt + 1,
                        ok=True,
                        errors=errors,
                    )
            time.sleep(1.0)
        return ExtractionResult(ok=False, errors=errors)

    # -- conversion --------------------------------------------------------

    def to_race(self, result: ExtractionResult) -> tuple[dict[str, Any], list[str]]:
        """Convertit une extraction en champs exploitables par le pipeline."""
        if not result.ok:
            return {}, list(result.errors)
        data = result.data
        notes: list[str] = []
        horses: list[dict[str, Any]] = []
        for raw in data.get("horses", []):
            if not isinstance(raw, Mapping):
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                notes.append("partant ignoré : nom manquant")
                continue
            horses.append(
                {
                    "name": name,
                    "number": raw.get("number"),
                    "driver": raw.get("driver"),
                    "odds_pdf": _to_float(raw.get("odds")),
                    "music": raw.get("music"),
                    "gains": _to_float(raw.get("gains")),
                    "comment": raw.get("comment"),
                }
            )
        meta = data.get("meta") or {}
        if not horses:
            notes.append("extraction IA sans aucun partant exploitable")
        return {"meta": dict(meta), "horses": horses}, notes


def _first_text(body: Mapping[str, Any]) -> str | None:
    try:
        candidates = body["candidates"]
        parts = candidates[0]["content"]["parts"]
        for part in parts:
            if "text" in part:
                return str(part["text"])
    except (KeyError, IndexError, TypeError):
        return None
    return None


def _parse_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned
        cleaned = cleaned.removeprefix("json").strip()
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                return None
        else:
            return None
    return parsed if isinstance(parsed, dict) else None


def _validate(data: Mapping[str, Any]) -> bool:
    horses = data.get("horses")
    if not isinstance(horses, list) or not horses:
        return False
    return any(isinstance(h, Mapping) and h.get("name") for h in horses)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["GeminiManager", "ExtractionResult", "RESPONSE_SCHEMA", "EXTRACTION_PROMPT"]
