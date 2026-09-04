"""Konfiguration aus Umgebungsvariablen; .env wird bei Vorhandensein gelesen."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Modelle. Die Spec legt Haiku fuer den Prefilter und Sonnet fuer Digest und
# Monatsverdichtung fest; `mi ask` laeuft selten und bekommt das staerkste Modell.
DEFAULT_MODEL_PREFILTER = "claude-haiku-4-5"
DEFAULT_MODEL_DIGEST = "claude-sonnet-5"
DEFAULT_MODEL_MONTHLY = "claude-sonnet-5"
DEFAULT_MODEL_ASK = "claude-opus-5"


def _load_dotenv(path: Path) -> None:
    """Minimaler .env-Leser. Setzt nur, was noch nicht in der Umgebung steht."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(REPO_ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "ja"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class MailConfig:
    backend: str = "console"          # resend | smtp | console
    sender: str = "marktbrief@example.com"
    recipients: tuple[str, ...] = ()
    resend_api_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True


@dataclass(frozen=True)
class Config:
    db_path: Path = REPO_ROOT / "data" / "mi.db"
    sources_path: Path = REPO_ROOT / "sources.yaml"
    snapshot_dir: Path = REPO_ROOT / "data" / "snapshots"

    anthropic_api_key: str | None = None
    model_prefilter: str = DEFAULT_MODEL_PREFILTER
    model_digest: str = DEFAULT_MODEL_DIGEST
    model_monthly: str = DEFAULT_MODEL_MONTHLY
    model_ask: str = DEFAULT_MODEL_ASK

    digest_min_score: int = 4
    digest_max_items: int = 8
    digest_min_items: int = 5
    alert_min_score: int = 8
    max_alerts_per_day: int = 3

    prefilter_batch_size: int = 10
    raw_text_limit: int = 20_000     # Zeichen; haelt die Archiv-DB handhabbar
    http_timeout: int = 20
    user_agent: str = (
        "MarketIntelligenceBot/0.1 (+Marktbeobachtung Gesundheitswesen)"
    )

    mail: MailConfig = field(default_factory=MailConfig)

    @classmethod
    def from_env(cls) -> "Config":
        recipients = tuple(
            addr.strip()
            for addr in os.environ.get("MI_MAIL_TO", "").split(",")
            if addr.strip()
        )
        mail = MailConfig(
            backend=os.environ.get("MI_MAIL_BACKEND", "console").strip().lower(),
            sender=os.environ.get("MI_MAIL_FROM", "marktbrief@example.com"),
            recipients=recipients,
            resend_api_key=os.environ.get("RESEND_API_KEY") or None,
            smtp_host=os.environ.get("SMTP_HOST") or None,
            smtp_port=_env_int("SMTP_PORT", 587),
            smtp_user=os.environ.get("SMTP_USER") or None,
            smtp_password=os.environ.get("SMTP_PASSWORD") or None,
            smtp_starttls=_env_bool("SMTP_STARTTLS", True),
        )
        db_path = Path(os.environ.get("MI_DB_PATH") or (REPO_ROOT / "data" / "mi.db"))
        if not db_path.is_absolute():
            db_path = REPO_ROOT / db_path
        return cls(
            db_path=db_path,
            sources_path=Path(
                os.environ.get("MI_SOURCES_PATH") or (REPO_ROOT / "sources.yaml")
            ),
            snapshot_dir=Path(
                os.environ.get("MI_SNAPSHOT_DIR") or (REPO_ROOT / "data" / "snapshots")
            ),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            model_prefilter=os.environ.get("MI_MODEL_PREFILTER", DEFAULT_MODEL_PREFILTER),
            model_digest=os.environ.get("MI_MODEL_DIGEST", DEFAULT_MODEL_DIGEST),
            model_monthly=os.environ.get("MI_MODEL_MONTHLY", DEFAULT_MODEL_MONTHLY),
            model_ask=os.environ.get("MI_MODEL_ASK", DEFAULT_MODEL_ASK),
            digest_min_score=_env_int("MI_DIGEST_MIN_SCORE", 4),
            digest_max_items=_env_int("MI_DIGEST_MAX_ITEMS", 8),
            digest_min_items=_env_int("MI_DIGEST_MIN_ITEMS", 5),
            alert_min_score=_env_int("MI_ALERT_MIN_SCORE", 8),
            max_alerts_per_day=_env_int("MI_MAX_ALERTS_PER_DAY", 3),
            prefilter_batch_size=_env_int("MI_PREFILTER_BATCH_SIZE", 10),
            raw_text_limit=_env_int("MI_RAW_TEXT_LIMIT", 20_000),
            http_timeout=_env_int("MI_HTTP_TIMEOUT", 20),
            mail=mail,
        )
