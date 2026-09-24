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
    """Informations de dépassement de l'heure d'arrêt des jeux."""

    is_late: bool
    race_start: dt.datetime | None
    now: dt.datetime
    minutes_late: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_late": self.is_late,
            "race_start": self.race_start.isoformat() if self.race_start else None,
            "now": self.now.isoformat(),
            "minutes_late": round(self.minutes_late, 2),
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


def check_deadline(race: Race, now: dt.datetime | None = None, tz_name: str = "Africa/Ouagadougou") -> OutOfDeadline:
    """Détermine si la course est déjà partie au moment de l'exécution."""
    tz = ZoneInfo(tz_name)
    now = now or dt.datetime.now(tz)
    start = race.meta.start_time
    if start is None:
        return OutOfDeadline(is_late=False, race_start=None, now=now)
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    delta = (now - start).total_seconds() / 60.0
    return OutOfDeadline(
        is_late=delta >= 0, race_start=start, now=now, minutes_late=max(0.0, delta)
    )


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
        where = meta.hippodrome or meta.meeting or "hippodrome non précisé"
        when = meta.start_time.isoformat() if meta.start_time else "heure non précisée"
        head = (
            f"🏇 HYPERION — {meta.name or 'Course du jour'}\n"
            f"{where} · {when} · {meta.discipline.label_fr}"
        )
        if late.is_late:
            head = (
                "⛔ ANALYSE HORS DÉLAI\n"
                f"La course a déjà débuté il y a {late.minutes_late:.0f} minutes.\n"
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
        reports: list[DeliveryReport] = []
        reports.extend(self.send_telegram(messages))
        reports.extend(self.send_email(subject, messages))
        if self.settings.dry_run:
            reports.append(
                DeliveryReport("dry-run", True, f"{len(messages)} message(s) non envoyés")
            )
        return reports


__all__ = [
    "Delivery",
    "DeliveryReport",
    "ChecklistResult",
    "OutOfDeadline",
    "check_deadline",
    "PIPELINE_STEPS",
]
