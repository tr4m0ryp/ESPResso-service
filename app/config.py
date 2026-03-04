"""Application configuration via environment variables."""

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """ESPResso service settings loaded from environment variables."""

    # Authentication
    API_KEY: str

    # Security
    ENVIRONMENT: str = "development"
    HMAC_SECRET: str = ""
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    ALLOWED_ORIGINS: str = ""
    HEALTH_REQUIRE_AUTH: bool = False

    # NVIDIA NIM
    NIM_API_KEY: str = ""
    NIM_API_KEYS: str = ""
    NIM_MODEL_ID: str
    NIM_BASE_URL: str

    # Model artifact paths
    MODEL_A_PATH: str
    MODEL_B_PATH: str
    MODEL_C_PATH: str

    # Pipeline
    RARITY_CONFIDENCE_THRESHOLD: float
    MAX_BATCH_SIZE: int
    NIM_CONCURRENCY_LIMIT: int

    # Cache
    CACHE_MAX_SIZE: int
    CACHE_TTL_SECONDS: int

    # Supabase (PostgREST) -- optional; validated at runtime by the predict endpoint
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""

    # Server
    HOST: str
    PORT: int
    LOG_LEVEL: str

    @model_validator(mode="after")
    def _require_at_least_one_nim_key(self) -> "Settings":
        if not self.NIM_API_KEY and not self.NIM_API_KEYS:
            raise ValueError(
                "At least one NIM key is required: set NIM_API_KEY "
                "or NIM_API_KEYS (comma-separated)"
            )
        return self

    @model_validator(mode="after")
    def _require_hmac_in_production(self) -> "Settings":
        if self.ENVIRONMENT == "production" and not self.HMAC_SECRET:
            raise ValueError(
                "HMAC_SECRET is required when ENVIRONMENT=production"
            )
        return self

    @property
    def is_production(self) -> bool:
        """True when running in production environment."""
        return self.ENVIRONMENT == "production"

    @property
    def nim_api_key_list(self) -> list[str]:
        """Parse all configured NIM API keys into a list.

        NIM_API_KEYS takes priority. Falls back to NIM_API_KEY.
        """
        if self.NIM_API_KEYS:
            return [k.strip() for k in self.NIM_API_KEYS.split(",") if k.strip()]
        return [self.NIM_API_KEY]

    @property
    def allowed_origin_list(self) -> list[str]:
        """Parse ALLOWED_ORIGINS into a list of origin strings."""
        if not self.ALLOWED_ORIGINS:
            return []
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
