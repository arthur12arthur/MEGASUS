"""Module 1.12 — Delivery.

Transmet le rapport final à l'utilisateur, de façon identifiable et dans les
délais.

Règles appliquées (correctifs du test comparatif du 20/09/2026) :

  * chaque envoi porte la SIGNATURE de la plateforme d'origine +
    l'HORODATAGE, pour rester identifiable dans une boîte Gmail qui reçoit
    plusieurs systèmes Hyperion ;
  * plusieurs messages courts thématiques plutôt qu'un message unique ;
  * si la course est déjà partie ou terminée au moment de l'exécution, le
    rapport s'ouvre sur « ANALYSE HORS DÉLAI » au lieu de se présenter comme
    un rapport pré-course normal ;
  * vérification AVANT envoi : confirmation humaine si un humain est présent,
    sinon checklist d'auto-validation. Jamais un envoi sans aucune vérification.

Canaux : Telegram (Bot API, gratuit) et email (SMTP Gmail + mot de passe
d'application, gratuit). Les deux sont optionnels : sans configuration, le
rapport est simplement retourné.
"""

from __future__ import annotations

import datetime as dt
import smtplib
import ssl
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from hyperion.config import Settings
from hyperion import relay
from hyperion.models import Race

#: Les 9 étapes du pipeline, vérifiées par la checklist d'auto-validation.
PIPELINE_STEPS: tuple[str, ...] = (
    "1.1 DataIngestion",
    "1.2 Extraction (GeminiManager)",
    "1.3 DisciplineDetector",
    "1.4 MarketWatch",
    "1.5 DataFilter",
    "1.6 BaseScorer",
    "1.7 ConsensusInterne",
    "1.8 HADES",
    "1.9 ExternalConsensus",
)


@dataclass
class OutOfDeadline:
    """Position de l'exécution par rapport à la clôture des enjeux LONAB.

    L'heure limite n'est pas le départ en France mais la clôture des jeux
    au Burkina Faso (environ 10 min avant). ``is_late`` est vrai dès que
    cette clôture est passée : un rapport produit après elle n'est plus jouable.
    """

    is_late: bool
    race_start: dt.datetime | None
    now: dt.datetime
    minutes_late: float = 0.0
    betting_close: dt.datetime | None = None
    race_started: bool = False
    minutes_to_close: float | None = None
    schedule: relay.RaceSchedule | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_late": self.is_late,
            "race_start": self.race_start.isoformat() if self.race_start else None,
            "betting_close": self.betting_close.isoformat() if self.betting_close else None,
            "race_started": self.race_started,
            "now": self.now.isoformat(),
            "minutes_late": round(self.minutes_late, 2),
            "minutes_to_close": None if self.minutes_to_close is None else round(self.minutes_to_close, 2),
            "schedule": self.schedule.as_dict() if self.schedule else None,
        }


@dataclass
class DeliveryReport:
    """Ce qui a réellement été envoyé."""

    channel: str
    ok: bool
    detail: str
    parts: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "ok": self.ok,
            "detail": self.detail,
            "parts": self.parts,
        }


@dataclass
class ChecklistResult:
    """Résultat de l'auto-validation (utilisée en automatisation)."""

    passed: bool
    items: list[tuple[str, bool]] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        return [name for name, ok in self.items if not ok]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "items": [{"step": n, "ok": ok} for n, ok in self.items],
            "failures": self.failures,
        }


def check_deadline(
    race: Race,
    now: dt.datetime | None = None,
    tz_name: str = relay.RELAY_TZ,
    closing_minutes: int = relay.DEFAULT_CLOSING_MINUTES,
) -> OutOfDeadline:
    """Situe l'exécution par rapport à la clôture LONAB et au départ en France.

    Une heure naïve est lue dans ``tz_name`` (heure du programme LONAB).
    """
    tz = ZoneInfo(tz_name)
    now = now or dt.datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    schedule = relay.schedule_for(
        race.meta.start_time, race.meta.betting_close, closing_minutes, assumed_tz=tz_name
    )
    if schedule is None:
        return OutOfDeadline(is_late=False, race_start=None, now=now)
    to_close = (schedule.close - now).total_seconds() / 60.0
    return OutOfDeadline(
        is_late=to_close <= 0,
        race_start=schedule.start,
        now=now,
        minutes_late=max(0.0, -to_close),
        betting_close=schedule.close,
        race_started=now >= schedule.start,
        minutes_to_close=to_close,
        schedule=schedule,
    )


def format_duration(minutes: float) -> str:
    """« 5 minutes », « 3 h 10 », « 4 jours » — lisible dans un message."""
    minutes = max(0.0, minutes)
    if minutes < 120:
        return f"{minutes:.0f} minutes"
    if minutes < 48 * 60:
        hours, rest = divmod(int(round(minutes)), 60)
        return f"{hours} h {rest:02d}"
    return f"{minutes / 1440:.0f} jours"


class Delivery:
    """Envoie le rapport par Telegram et/ou email."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    # -- signature ---------------------------------------------------------

    def signature(self, extra: Mapping[str, Any] | None = None) -> str:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        text = f"— envoyé par {self.settings.platform_name} · {stamp}"
        if extra:
            details = " · ".join(f"{k} {v}" for k, v in extra.items())
            text = f"{text} · {details}"
        return text

    # -- checklist ---------------------------------------------------------

    def auto_validate(self, record: Mapping[str, Any]) -> ChecklistResult:
        """Checklist d'auto-validation quand aucun humain n'est présent."""
        items: list[tuple[str, bool]] = []
        for step in PIPELINE_STEPS:
            key = step.split(" ", 1)[1].lower()
            items.append((step, bool(record.get(key) is not None)))
        # Les deux scores doivent être bien séparés dans la sortie.
        items.append(
            (
                "séparation compétitivité / classement",
                "competitivite" in record and "classement" in record,
            )
        )
        items.append(
            ("indice de confiance justifié", "confiance" in record and "justification" in str(record.get("confiance"))),
        )
        passed = all(ok for _, ok in items)
        return ChecklistResult(passed=passed, items=items)

    # -- messages ----------------------------------------------------------

    def split_messages(self, blocks: Mapping[str, str]) -> list[str]:
        """Découpe le rapport en messages courts thématiques."""
        return [f"*{title}*\n{body}" for title, body in blocks.items()]

    def render_header(self, race: Race, late: OutOfDeadline) -> str:
        meta = race.meta
        where = meta.hippodrome or "hippodrome non précisé"
        code = f"{meta.meeting or ''}C{meta.race_number}" if meta.race_number else (meta.meeting or "")
        country = meta.race_country or relay.RACE_COUNTRY
        if late.schedule is not None:
            when = late.schedule.describe()
        else:
            when = "heure de départ non précisée — clôture LONAB non calculable"
        game = relay.lonab_game_for(meta.date, meta.bet_type)
        lines = [
            f"🏇 MEGASUS — {meta.name or 'Course du jour'}",
            f"{where} ({country}){' · ' + code if code else ''} · {meta.discipline.label_fr}",
            f"Course française relayée par {meta.operator or 'LONAB'} (PMU'B) pour le Burkina Faso",
            when,
        ]
        if game is not None:
            lines.append(f"Pari PMU'B : {game.describe()}")
        if late.minutes_to_close is not None and not late.is_late:
            lines.append(f"⏱ {format_duration(late.minutes_to_close)} avant la clôture LONAB")
        head = "\n".join(lines)
        if late.is_late:
            if late.race_started:
                why = (
                    f"La course a déjà débuté en France "
                    f"(clôture LONAB dépassée de {format_duration(late.minutes_late)})."
                )
            else:
                why = (
                    f"Les enjeux LONAB sont clos depuis {format_duration(late.minutes_late)} "
                    "(la course n'est pas encore partie en France)."
                )
            head = (
                "⛔ ANALYSE HORS DÉLAI\n"
                f"{why}\n"
                "Ce rapport est fourni à titre d'analyse rétrospective, "
                "pas comme pronostic jouable.\n\n" + head
            )
        return head

    # -- canaux ------------------------------------------------------------

    def send_telegram(self, messages: Sequence[str]) -> list[DeliveryReport]:
        token = self.settings.telegram_token
        chat_id = self.settings.telegram_chat_id
        if not token or not chat_id:
            return [
                DeliveryReport("telegram", False, "canal non configuré (secret manquant)")
            ]
        import requests

        reports: list[DeliveryReport] = []
        for index, message in enumerate(messages, start=1):
            try:
                response = requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "disable_web_page_preview": True,
                    },
                    timeout=20,
                )
                ok = response.status_code == 200
                reports.append(
                    DeliveryReport(
                        "telegram",
                        ok,
                        "ok" if ok else f"HTTP {response.status_code}",
                        parts=index,
                    )
                )
            except Exception as exc:  # réseau indisponible : on n'échoue pas
                reports.append(DeliveryReport("telegram", False, f"exception {exc}"))
        return reports

    def send_email(self, subject: str, messages: Sequence[str]) -> list[DeliveryReport]:
        host = self.settings.smtp_host
        user = self.settings.smtp_user
        password = self.settings.smtp_password
        recipients = self.settings.mail_to
        if not host or not user or not password or not recipients:
            return [
                DeliveryReport("email", False, "canal non configuré (secret manquant)")
            ]
        body = "\n\n".join(messages)
        reports: list[DeliveryReport] = []
        try:
            message = MIMEMultipart()
            message["Subject"] = subject
            message["From"] = self.settings.mail_from or user
            message["To"] = ", ".join(recipients)
            message.attach(MIMEText(body, "plain", "utf-8"))
            context = ssl.create_default_context()
            with smtplib.SMTP(host, self.settings.smtp_port, timeout=30) as server:
                server.starttls(context=context)
                server.login(user, password)
                server.sendmail(
                    self.settings.mail_from or user, list(recipients), message.as_string()
                )
            reports.append(DeliveryReport("email", True, "ok", parts=len(messages)))
        except Exception as exc:
            reports.append(DeliveryReport("email", False, f"exception {exc}"))
        return reports

    def deliver(self, subject: str, messages: Sequence[str]) -> list[DeliveryReport]:
        """Envoie sur tous les canaux configurés."""
        if self.settings.dry_run:
            return [
                DeliveryReport("dry-run", True, f"{len(messages)} message(s) non envoyés")
            ]
        reports: list[DeliveryReport] = []
        reports.extend(self.send_telegram(messages))
        reports.extend(self.send_email(subject, messages))
        return reports


__all__ = [
    "Delivery",
    "DeliveryReport",
    "ChecklistResult",
    "OutOfDeadline",
    "check_deadline",
    "PIPELINE_STEPS",
]
