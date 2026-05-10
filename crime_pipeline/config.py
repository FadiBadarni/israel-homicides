from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = Field(..., description="Google Gemini API key for LLM extraction")
    db_path: Path = Field(default=Path("data/pipeline.db"), description="SQLite database file path")
    log_level: str = Field(default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR")
    llm_provider: str = Field(default="gemini", description="LLM provider: gemini")
    llm_model: str = Field(default="gemini-2.5-flash", description="Gemini model identifier")
    llm_max_tokens: int = Field(default=1024, gt=0, description="Maximum tokens for LLM response")
    llm_concurrency: int = Field(default=8, gt=0, description="Max concurrent LLM requests")
    jaro_threshold: float = Field(
        default=0.88, ge=0.0, le=1.0, description="Jaro-Winkler similarity threshold for name dedup"
    )
    cosine_threshold: float = Field(
        default=0.82, ge=0.0, le=1.0, description="Cosine similarity threshold for embedding dedup"
    )
    robots_txt_respect: bool = Field(default=True, description="Whether to respect robots.txt rules")
    request_delay_seconds: float = Field(
        default=3.0, ge=0.0, description="Delay between HTTP requests to avoid rate limiting"
    )
    max_article_tokens: int = Field(
        default=8000, gt=0, description="Max tokens of article text sent to LLM"
    )
    output_dir: Path = Field(default=Path("output"), description="Directory for pipeline output files")


def get_settings() -> Settings:
    """Return a cached Settings instance loaded from environment / .env file."""
    return Settings()  # type: ignore[call-arg]
