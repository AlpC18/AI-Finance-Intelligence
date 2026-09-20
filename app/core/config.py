"""Application configuration + strict production preflight validation."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised to refuse boot when production configuration is unsafe."""


_PLACEHOLDER_SECRET_MARKERS = ("dev-insecure", "change-me", "changeme")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "development"  # development | production

    # AI
    anthropic_api_key: str = ""
    ai_model: str = "claude-sonnet-4-6"
    ai_max_tokens: int = 1024

    # Cache
    cache_ttl_seconds: int = 120
    cache_backend: str = "memory"  # memory | redis
    redis_url: str = "redis://localhost:6379/0"

    # Rate limiting (per authenticated user, else per IP)
    rate_limit_ai: str = "5/minute"
    # Credential endpoints are unauthenticated, so these always key by IP.
    # They exist to make credential stuffing expensive, not to inconvenience a
    # user who mistypes a password: the caps are well above human retry rates
    # and far below what an automated sprayer needs.
    rate_limit_login: str = "10/minute"
    rate_limit_register: str = "5/minute"

    # Alerts / background scheduler
    alerts_enabled: bool = True
    alert_interval_minutes: int = 5

    # Auth (JWT)
    jwt_secret: str = "dev-insecure-change-me-please-set-a-strong-32byte-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Trade execution (paper-first broker integration)
    trade_enabled: bool = True
    # Safe by default across every broker.  Live routing requires BOTH this
    # explicit value and non-paper credentials; no provider infers it from URL.
    trading_mode: str = "paper"  # paper | live
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    max_order_notional: float = 10_000.0
    min_trade_confidence: float = 0.0  # 0 -> no gate; set >0 to require signal confidence
    rate_limit_trade: str = "10/minute"
    order_reconciliation_enabled: bool = True
    order_poll_interval_seconds: int = 30
    position_sync_enabled: bool = True
    position_sync_interval_minutes: int = 15
    event_scan_enabled: bool = True
    event_scan_interval_minutes: int = 10
    # Evaluates the daily-loss limit on a timer instead of only when someone
    # happens to make a request. Without it the automatic halt is not a
    # protection, it is a report you get next time you look.
    risk_sweep_enabled: bool = True
    risk_sweep_interval_minutes: int = 5
    paper_automation_enabled: bool = False  # explicit opt-in; paper only
    paper_automation_interval_minutes: int = 15
    worker_queue_enabled: bool = False  # enable only with a separate Redis worker
    admin_emails: str = ""  # comma-separated allowlist; empty denies everyone

    # Inbound broker webhook (Alpaca trade updates). Shared-secret authenticated;
    # empty -> the endpoint is disabled (503) and only the poll backstop runs.
    trade_webhook_secret: str = ""

    # Multi-channel alert fallback: when a critical alert can't reach a live
    # WebSocket, deliver it out-of-band. Both sinks are optional and best-effort.
    notify_webhook_url: str = ""          # generic POST sink (Slack/ops/etc.)
    notify_email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "alerts@ai-finance.local"
    smtp_use_tls: bool = True

    # Distributed tracing (OpenTelemetry). Optional: with no OTLP endpoint the
    # SDK still propagates context but drops spans, so enabling it is cheap.
    otel_enabled: bool = False
    otel_service_name: str = "ai-finance-intelligence"
    otel_exporter_otlp_endpoint: str = ""  # e.g. http://localhost:4318/v1/traces

    # Encryption at rest (Fernet). Required & validated in production.
    encryption_key: str = ""

    # Persistence / server
    database_url: str = "sqlite:///./finance.db"
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key.strip())

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    def _secret_is_placeholder(self) -> bool:
        low = self.jwt_secret.lower()
        return any(marker in low for marker in _PLACEHOLDER_SECRET_MARKERS)

    def production_config_errors(self) -> list[str]:
        """Fatal misconfigurations that must block boot in production."""
        errors: list[str] = []
        if not self.is_production:
            return errors
        if not self.jwt_secret or self._secret_is_placeholder():
            errors.append("JWT_SECRET is missing or a development placeholder.")
        elif len(self.jwt_secret.encode("utf-8")) < 32:
            errors.append("JWT_SECRET must be at least 32 bytes in production.")
        if not self.anthropic_api_key.strip():
            errors.append("ANTHROPIC_API_KEY is required in production.")
        if self.cache_backend == "memory":
            errors.append(
                "CACHE_BACKEND must be 'redis' in production (multi-worker safety)."
            )
        errors.extend(self._encryption_key_errors())
        if self.trading_mode not in {"paper", "live"}:
            errors.append("TRADING_MODE must be 'paper' or 'live'.")
        return errors

    @property
    def live_trading_enabled(self) -> bool:
        return self.trading_mode.strip().lower() == "live"

    def _encryption_key_errors(self) -> list[str]:
        raw = self.encryption_key.strip()
        if not raw:
            return ["ENCRYPTION_KEY is required in production (encrypts broker keys at rest)."]
        from cryptography.fernet import Fernet

        keys = [k.strip() for k in raw.split(",") if k.strip()]
        for key in keys:
            try:
                Fernet(key.encode("utf-8"))
            except Exception:  # noqa: BLE001 - invalid key material
                return [
                    "ENCRYPTION_KEY must be valid urlsafe-base64 Fernet key(s) "
                    "(comma-separated, primary first, for rotation)."
                ]
        return []


@lru_cache
def get_settings() -> Settings:
    return Settings()
