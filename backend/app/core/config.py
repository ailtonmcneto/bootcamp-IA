from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    max_file_size: int = 10 * 1024 * 1024
    max_pages: int = 100
    chunk_size: int = 1000
    chunk_overlap: int = 150
    max_search_results: int = 5
    min_similarity: float = 0.35

    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-3.6-flash"

    storage_backend: Literal["memory", "supabase"] = "memory"
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_service_role_key: str = ""
    supabase_bucket: str = "documents"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="DOCUMIND_")


settings = Settings()
