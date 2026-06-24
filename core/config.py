"""Central settings, loaded from environment / .env (pydantic-settings).

Every field has a default so the package imports cleanly even before .env is
filled (Phase 1 is scaffolded offline). Missing real credentials surface as
clear errors at the point of use (see core/clients.py), not at import time.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "development"
    base_url: str = "https://api.procureos.in/v1"
    frontend_url: str = "https://app.procureos.in"
    secret_key: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_postgres_url: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # LLM providers
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    groq_api_key: str = ""

    # Optional per-task model overrides (override the routing config when set).
    # Format: "provider/model", e.g. "groq/llama-3.1-8b-instant".
    llm_intent_parser_model: str = ""
    llm_quote_parser_model: str = ""
    llm_orchestrator_model: str = ""

    # Vendor discovery
    google_places_api_key: str = ""

    # WhatsApp (Chat Mitra WABA)
    chat_mitra_api_key: str = ""
    chat_mitra_waba_number: str = ""
    chat_mitra_base_url: str = "https://api.chatmitra.io/v1"  # confirm against Chat Mitra docs
    meta_webhook_secret: str = ""          # HMAC verify of inbound WhatsApp webhooks
    meta_webhook_verify_token: str = ""    # WhatsApp webhook URL verification (GET challenge)

    # Payment (Volopay)
    volopay_api_key: str = ""
    volopay_webhook_secret: str = ""
    volopay_team_id: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_signing_secret: str = ""   # Fix 11 — HMAC verification of Slack webhooks

    # Monitoring
    sentry_dsn: str = ""


settings = Settings()
