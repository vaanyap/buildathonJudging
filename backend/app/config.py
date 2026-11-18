from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://judging:judging@localhost:5432/judging"
    jwt_secret: str = ""
    jwt_exp_minutes: int = 60 * 12
    seed_on_start: bool = True
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
