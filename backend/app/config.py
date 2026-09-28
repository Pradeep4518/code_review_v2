"""Runtime configuration. Secrets are read from the environment only and never sent to the frontend."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

SUPPORTED_GROQ_MODELS = ("openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b")
MAX_DIFF_CHARS = 20_000


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    groq_model: str
    hindsight_api_key: str
    hindsight_base_url: str
    hindsight_bank_id: str
    frontend_origin: str
    data_dir: Path

    @property
    def groq_configured(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def hindsight_configured(self) -> bool:
        return bool(self.hindsight_api_key)


def get_settings() -> Settings:
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=os.getenv("GROQ_MODEL", "").strip() or SUPPORTED_GROQ_MODELS[0],
        hindsight_api_key=os.getenv("HINDSIGHT_API_KEY", "").strip(),
        hindsight_base_url=(os.getenv("HINDSIGHT_BASE_URL", "").strip() or "https://api.hindsight.vectorize.io").rstrip("/"),
        hindsight_bank_id=os.getenv("HINDSIGHT_BANK_ID", "").strip() or "repomind",
        frontend_origin=os.getenv("FRONTEND_ORIGIN", "").strip() or "http://localhost:5173",
        data_dir=Path(os.getenv("REPOMIND_DATA_DIR", "") or (BACKEND_DIR / "data")),
    )
