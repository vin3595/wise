from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WISE_", extra="ignore")

    # App database user. Must NOT be a superuser or table owner, otherwise
    # Postgres row-level security is bypassed.
    database_url: str = "postgresql+psycopg://wise_app:wise_app@localhost:5432/wise"
    # Migration user (table owner). Used only by Alembic.
    migrate_database_url: str = "postgresql+psycopg://wise_owner:wise_owner@localhost:5432/wise"

    jwt_secret: str = "change-me-in-production"
    owner_token_hours: int = 24 * 30
    staff_token_hours: int = 16

    otp_provider: str = "console"  # console (dev: code shown in the browser) | box (code in the log) | msg91 | fast2sms
    signup_mode: str = "open"      # open | first (Wise Box) | closed
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    msg91_auth_key: str = ""
    msg91_template_id: str = ""
    fast2sms_api_key: str = ""

    pin_max_failures: int = 5
    pin_lock_minutes: int = 10

    # Restaurants get <login_id>.<base_domain>, e.g. shakewallah.wise.in
    base_domain: str = "localhost"
    upload_dir: str = "uploads"
    public_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:5173"
    trial_days: int = 14
    # Address customers' phones use for QR menus and the order-ready screen (the Wise Box's public
    # Cloudflare address). Empty = the address the staff opened Wise from.
    customer_url: str = ""
    sms_worker: bool = True        # send the SMS outbox from this process
    live_updates: bool = True      # push changes to screens (Postgres LISTEN/NOTIFY + Server-Sent Events)


@lru_cache
def get_settings() -> Settings:
    return Settings()
