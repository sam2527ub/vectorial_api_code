"""
Central runtime configuration loaded from ``config/runtime.yaml``.

Secrets (API keys, database URLs, bucket credentials) remain in environment / ``.env``.
This module supplies defaults for hosts, ports, reload, pool sizes, retry policy, scraper IDs,
default model names, and repo-relative pipeline YAML paths.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# Used when ``pipeline_config_files`` is absent or partial in YAML.
DEFAULT_PIPELINE_FILES: Dict[str, str] = {
    "linkedin_sgo_async": (
        "app/services/linkedin_sgo_pipeline_service_async/"
        "linkedin_sgo_pipeline_async_config.yaml"
    ),
    "linkedin_initial_prediction": (
        "app/services/linkedin_initial_prediction_service/initial_prediction_config.yaml"
    ),
    "linkedin_ground_truth_extraction": (
        "app/services/linkedin_ground_truth_extraction_service/"
        "ground_truth_extraction_config.yaml"
    ),
    "linkedin_stimulus_extraction": (
        "app/services/linkedin_stimulus_extraction_service/stimulus_extraction_config.yaml"
    ),
    "dynamic_context_window": (
        "app/services/dynamic_context_window_management_service/config.yaml"
    ),
}


def _default_runtime_yaml_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "runtime.yaml"


class ServerSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False


class LoggingSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    level: str = "INFO"


class DatabasePoolSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min_connections: int = 1
    max_connections: int = 10


class DatabaseRetrySettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_attempts: int = 4
    base_delay_s: float = 0.12


class LinkedinSgoAsyncSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    process_trigger_timeout_s: float = 120.0


class PathsSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo_root: str = ""


class AwsSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    region_default: str = "us-west-2"


class ApiClientsSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    groq_default_model: str = "llama-3.3-70b-versatile"


class ScrapersSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    linkedin_post_actor_id: str = "curious_coder/linkedin-post-search-scraper"
    linkedin_profile_actor_id: str = "2SyF0bVxmgGr8IVCZ"
    linkedin_comments_actor_id: str = "harvestapi/linkedin-profile-comments"


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    server: ServerSettings = Field(default_factory=ServerSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    database_pool: DatabasePoolSettings = Field(default_factory=DatabasePoolSettings)
    database_retry: DatabaseRetrySettings = Field(default_factory=DatabaseRetrySettings)
    linkedin_sgo_async: LinkedinSgoAsyncSettings = Field(default_factory=LinkedinSgoAsyncSettings)
    pipeline_config_files: Dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_PIPELINE_FILES)
    )
    paths: PathsSettings = Field(default_factory=PathsSettings)
    aws: AwsSettings = Field(default_factory=AwsSettings)
    api_clients: ApiClientsSettings = Field(default_factory=ApiClientsSettings)
    scrapers: ScrapersSettings = Field(default_factory=ScrapersSettings)


def _deep_merge_env_into_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow env overrides for common operational knobs."""
    out = dict(raw) if isinstance(raw, dict) else {}
    server = dict(out.get("server") or {})
    if os.getenv("SERVER_HOST"):
        server["host"] = os.environ["SERVER_HOST"].strip()
    if os.getenv("SERVER_PORT"):
        server["port"] = int(os.environ["SERVER_PORT"].strip())
    _r = os.getenv("UVICORN_RELOAD", "").strip().lower()
    if _r:
        server["reload"] = _r in ("1", "true", "yes", "on")
    if server:
        out["server"] = server

    logging_cfg = dict(out.get("logging") or {})
    if os.getenv("LOG_LEVEL"):
        logging_cfg["level"] = os.environ["LOG_LEVEL"].strip()
    if logging_cfg:
        out["logging"] = logging_cfg

    sgo = dict(out.get("linkedin_sgo_async") or {})
    if os.getenv("LINKEDIN_SGO_PROCESS_TRIGGER_TIMEOUT_S"):
        sgo["process_trigger_timeout_s"] = float(
            os.environ["LINKEDIN_SGO_PROCESS_TRIGGER_TIMEOUT_S"].strip()
        )
    if sgo:
        out["linkedin_sgo_async"] = sgo

    db_retry = dict(out.get("database_retry") or {})
    if os.getenv("DATABASE_RETRY_MAX_ATTEMPTS"):
        db_retry["max_attempts"] = int(os.environ["DATABASE_RETRY_MAX_ATTEMPTS"].strip())
    if os.getenv("DATABASE_RETRY_BASE_DELAY_S"):
        db_retry["base_delay_s"] = float(os.environ["DATABASE_RETRY_BASE_DELAY_S"].strip())
    if db_retry:
        out["database_retry"] = db_retry

    return out


def _load_yaml_file(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        logger.warning("Runtime config file missing at %s — using built-in defaults only", path)
        return {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception as e:
        logger.error("Failed to load runtime config %s: %s", path, e)
        return {}


@lru_cache(maxsize=1)
def get_runtime_settings() -> RuntimeSettings:
    cfg_path = Path(
        os.getenv("RUNTIME_CONFIG_PATH", str(_default_runtime_yaml_path()))
    ).expanduser()
    raw = _deep_merge_env_into_raw(_load_yaml_file(cfg_path.resolve()))
    pcf = raw.get("pipeline_config_files")
    raw["pipeline_config_files"] = {
        **DEFAULT_PIPELINE_FILES,
        **(pcf if isinstance(pcf, dict) else {}),
    }
    return RuntimeSettings.model_validate(raw)


def get_repo_root() -> Path:
    explicit = get_runtime_settings().paths.repo_root.strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    # app/runtime_settings.py -> parent = app/, parent.parent = repo root
    return Path(__file__).resolve().parent.parent


def pipeline_config_path(key: str) -> Optional[Path]:
    """
    Resolve a configured pipeline YAML path by key (see ``pipeline_config_files`` in runtime.yaml).
    Returns None if the key is missing or path is empty.
    """
    rel = get_runtime_settings().pipeline_config_files.get(key, "").strip()
    if not rel:
        return None
    p = (get_repo_root() / rel).resolve()
    return p


def reset_runtime_settings_cache() -> None:
    """Tests only — clear memoized settings."""
    get_runtime_settings.cache_clear()
