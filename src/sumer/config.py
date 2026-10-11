"""Settings, read once from the environment (and .env if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # LLM: any OpenAI-compatible endpoint. Bayleaf (UCSC) is the default.
    llm_mode: str = os.getenv("LLM_MODE", "bayleaf")  # live LLM decisions only
    llm_base_url: str = os.getenv("BAYLEAF_BASE_URL", "")
    llm_api_key: str = os.getenv("BAYLEAF_API_KEY", "")
    llm_model: str = os.getenv("BAYLEAF_MODEL", "")
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "25"))
    llm_reasoning: bool = _bool("LLM_REASONING", False)
    llm_reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "")
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "700"))
    llm_cache: bool = _bool("LLM_CACHE", True)
    # Hard cap on real API spend, tracked from the cost Bayleaf reports per call.
    llm_budget_usd: float = float(os.getenv("LLM_BUDGET_USD", "3.00"))

    # Transport: "slim" (AGNTCY, the demo path) or "inproc" (tests, fast benches)
    transport: str = os.getenv("TRANSPORT", "slim")
    slim_host: str = os.getenv("SLIM_HOST", "127.0.0.1")
    slim_port: int = int(os.getenv("SLIM_PORT", "46357"))
    slim_secret: str = os.getenv(
        "SLIM_SHARED_SECRET", "council-of-sumer-dev-secret-change-me-0123456789"
    )

    # Deliberation
    max_rounds: int = int(os.getenv("MAX_ROUNDS", "5"))
    reign_years: int = int(os.getenv("REIGN_YEARS", "5"))
    # 0 = the live game waits for a human as long as it takes
    escalation_timeout_s: float = float(os.getenv("ESCALATION_TIMEOUT_S", "0"))

    # Voice (ElevenLabs, optional)
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "")
    # Seconds between council messages in the live chamber (POST /api/pace changes it)
    pace_s: float = float(os.getenv("PACE_S", "0.8"))

    web_port: int = int(os.getenv("WEB_PORT", "8000"))

    @property
    def slim_endpoint(self) -> str:
        return f"http://{self.slim_host}:{self.slim_port}"


settings = Settings()
CACHE_DIR = ROOT / ".cache"
RESULTS_DIR = ROOT / "results"
