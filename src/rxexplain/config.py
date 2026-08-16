

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------- paths
PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parent.parent  # repo root (contains .env, data/, results/)

DATA_DIR = ROOT / "data"
GOLD_DIR = DATA_DIR / "gold"
CACHE_DIR = DATA_DIR / "cache"
RESULTS_DIR = ROOT / "results"
KB_PATH = PKG_DIR / "kb" / "drug_kb.json"
GOLD_PATH = GOLD_DIR / "gold_set.jsonl"

for _d in (DATA_DIR, GOLD_DIR, CACHE_DIR, RESULTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# override=True: a stale AZURE_OPENAI_* left in the shell or user environment
# would otherwise shadow .env silently and surface only as a confusing 401.
# .env is documented as the credential source, so it wins.
load_dotenv(ROOT / ".env", override=True)

# Target reading level for generated explanations (Flesch-Kincaid grade).
TARGET_GRADE_LOW = 6.0
TARGET_GRADE_HIGH = 8.0


class ConfigError(RuntimeError):
    """Raised when required Azure credentials are missing or malformed."""


@dataclass(frozen=True)
class AzureConfig:
    """Azure AI Foundry / Azure OpenAI connection settings."""

    endpoint: str
    api_key: str
    deployment: str
    api_version: str
    judge_deployment: str
    #  "azure_openai"  -> *.openai.azure.com          (AzureOpenAI client)
    #  "openai_compat" -> *.services.ai.azure.com/... (OpenAI client + base_url)
    mode: str

    @property
    def compat_base_url(self) -> str:
        """Base URL for the OpenAI-compatible v1 surface of a Foundry resource."""
        base = self.endpoint.rstrip("/")
        if base.endswith("/openai/v1"):
            return base
        if "/openai" in base:
            return base.split("/openai")[0] + "/openai/v1"
        return base + "/openai/v1"


def _infer_mode(endpoint: str) -> str:
    ep = endpoint.lower()
    if "services.ai.azure.com" in ep or "/openai/v1" in ep:
        return "openai_compat"
    return "azure_openai"


def load_azure_config(require: bool = True) -> AzureConfig | None:
    """Read Azure settings from the environment.

    With ``require=False`` returns ``None`` instead of raising, so that the
    rule-based baseline, the parser and the test suite stay runnable with no
    credentials at all.
    """
    endpoint = (os.getenv("AZURE_OPENAI_ENDPOINT") or "").strip()
    api_key = (os.getenv("AZURE_OPENAI_API_KEY") or "").strip()
    deployment = (os.getenv("AZURE_OPENAI_DEPLOYMENT") or "").strip()
    api_version = (os.getenv("AZURE_OPENAI_API_VERSION") or "2024-10-21").strip()
    judge = (os.getenv("AZURE_OPENAI_JUDGE_DEPLOYMENT") or deployment).strip()
    mode = (os.getenv("RX_CLIENT_MODE") or "").strip() or _infer_mode(endpoint)

    missing = [
        name
        for name, val in (
            ("AZURE_OPENAI_ENDPOINT", endpoint),
            ("AZURE_OPENAI_API_KEY", api_key),
            ("AZURE_OPENAI_DEPLOYMENT", deployment),
        )
        if not val
    ]
    if missing:
        if not require:
            return None
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + f"\nCopy .env.example to .env (at {ROOT}) and fill in your "
            "Azure AI Foundry endpoint, key and deployment name."
        )

    return AzureConfig(
        endpoint=endpoint,
        api_key=api_key,
        deployment=deployment,
        api_version=api_version,
        judge_deployment=judge,
        mode=mode,
    )
