"""Configuration centralisée d'Hyperion.

Tout paramètre est surchargeable par variable d'environnement, préfixée
``HYPERION_``. Aucune clé secrète n'est codée en dur : elle est lue dans
l'environnement (GitHub Actions secrets en automatisation).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
SAMPLES_DIR = DATA_DIR / "samples"

#: Fuseau du marché LONAB (Burkina Faso, UTC+0) : heures du programme et
#: heure limite de jeu. Les courses, elles, se déroulent en France
#: (Europe/Paris) — voir hyperion/relay.py.
LOCAL_TZ = "Africa/Ouagadougou"

#: Les 12 sources du panel de référence (ordre = ordre de consultation).
REFERENCE_PANEL: tuple[str, ...] = (
    "Genybet",
    "Equidia",
    "Canal Turf",
    "PMU",
    "France Galop",
    "Paris-Turf",
    "ZEturf",
    "Turf BZH",
    "RueDesJoueurs",
    "Betclic Turf",
    "Turfomania",
    "Quinté du Jour",
)


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(f"HYPERION_{name}")
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = _env_str(name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _env_int_list(name: str, default: tuple[int, ...] = ()) -> tuple[int, ...]:
    raw = _env_str(name)
    if raw is None:
        return default
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return tuple(out) or default


@dataclass
class Settings:
    """Réglages du pipeline. Valeurs par défaut = valeurs de production V10."""

    # -- exécution ---------------------------------------------------------
    data_dir: Path = DATA_DIR
    #: Surchargeable (HYPERION_RUNS_DIR), lu à chaque instanciation : les
    #: tests isolent ainsi leurs écritures hors du dépôt.
    runs_dir: Path = field(default_factory=lambda: Path(_env_str("RUNS_DIR") or RUNS_DIR))
    local_tz: str = _env_str("LOCAL_TZ", LOCAL_TZ) or LOCAL_TZ

    # -- module 1.1 DataIngestion ------------------------------------------
    lonab_url: str | None = _env_str("LONAB_URL")
    lonab_timeout_s: int = _env_int("HTTP_TIMEOUT_S", 20)
    lonab_max_retries: int = _env_int("HTTP_RETRIES", 3)
    #: Opérateurs/pays autorisés — évite de récupérer la course d'un autre marché.
    allowed_operators: tuple[str, ...] = _env_list("ALLOWED_OPERATORS", ("LONAB", "PMU'B", "PMUB"))
    allowed_countries: tuple[str, ...] = _env_list("ALLOWED_COUNTRIES", ("Burkina Faso", "BF"))
    #: Pays où se déroulent les courses relayées (LONAB = relais de courses françaises).
    allowed_race_countries: tuple[str, ...] = _env_list("ALLOWED_RACE_COUNTRIES", ("France",))
    #: Minutes entre la clôture des enjeux LONAB et le départ en France,
    #: utilisées quand le programme n'imprime pas l'heure de clôture.
    lonab_closing_minutes: int = _env_int("LONAB_CLOSING_MINUTES", 10)

    # -- module 1.2 GeminiManager ------------------------------------------
    gemini_keys: tuple[str, ...] = _env_list("GEMINI_KEYS")
    gemini_models: tuple[str, ...] = _env_list(
        "GEMINI_MODELS",
        ("gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"),
    )
    gemini_max_passes: int = _env_int("GEMINI_PASSES", 3)

    # -- module 1.5 DataFilter ---------------------------------------------
    filter_threshold_points: int = _env_int("FILTER_THRESHOLD", 3)
    filter_min_group: int = _env_int("FILTER_MIN_GROUP", 5)
    filter_forced_favourites: int = _env_int("FILTER_FAVOURITES", 5)

    # -- module 1.7 ConsensusInterne ---------------------------------------
    mc_seeds: tuple[int, ...] = _env_int_list("MC_SEEDS", (11, 23, 37, 51, 73))
    mc_simulations: int = _env_int("MC_SIMULATIONS", 10_000)
    #: Concentration du modèle de Plackett-Luce.
    #: Valeur 0.5, issue de la calibration du labo Ouroboros par protocole
    #: glissant : sur 30 courses d'entraînement puis 30 courses de validation
    #: hors échantillon, 0.5 donnait une log-loss de 3.12 contre 3.25 pour
    #: l'ancienne valeur 0.8. Voir docs/ARCHITECTURE.md, section 3.
    mc_concentration: float = _env_float("MC_CONCENTRATION", 0.5)
    mc_top_n_stable: int = _env_int("MC_TOP_N", 3)

    # -- module 1.9 ExternalConsensus --------------------------------------
    reference_panel: tuple[str, ...] = _env_list("REFERENCE_PANEL", REFERENCE_PANEL)
    external_min_sources: int = _env_int("EXTERNAL_MIN_SOURCES", 3)

    # -- module 1.11 Storage ------------------------------------------------
    store_json: bool = _env_bool("STORE_JSON", True)

    # -- module 1.12 Delivery ----------------------------------------------
    telegram_token: str | None = _env_str("TELEGRAM_TOKEN")
    telegram_chat_id: str | None = _env_str("TELEGRAM_CHAT_ID")
    smtp_host: str | None = _env_str("SMTP_HOST")
    smtp_port: int = _env_int("SMTP_PORT", 587)
    smtp_user: str | None = _env_str("SMTP_USER")
    smtp_password: str | None = _env_str("SMTP_PASSWORD")
    mail_from: str | None = _env_str("MAIL_FROM")
    mail_to: tuple[str, ...] = _env_list("MAIL_TO")
    #: Signature d'origine (permet d'identifier l'émetteur dans une boîte Gmail
    #: qui reçoit plusieurs systèmes Hyperion).
    platform_name: str = _env_str("PLATFORM_NAME", "Hyperion V1 (GitHub Actions)") or "Hyperion V1"

    # -- mode ---------------------------------------------------------------
    dry_run: bool = _env_bool("DRY_RUN", False)
    human_present: bool = _env_bool("HUMAN_PRESENT", True)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if any(tag in key.upper() for tag in ("TOKEN", "PASSWORD", "KEY", "SMTP_USER", "CHAT_ID")):
                data[key] = "***" if value else None
            elif isinstance(value, Path):
                data[key] = str(value)
            else:
                data[key] = value
        return data


DEFAULT_SETTINGS = Settings()
