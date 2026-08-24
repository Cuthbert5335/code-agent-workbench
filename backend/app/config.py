"""Application configuration loaded from environment variables.

The first milestone only needs configuration to exist safely. Model-related
settings are read by the backend and are intentionally not sent to the
browser.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()


class Settings(BaseModel):
    """Runtime settings for the CodeXXX backend."""

    model_config = ConfigDict(protected_namespaces=())

    model_api_key: str = Field(default_factory=lambda: os.getenv("MODEL_API_KEY", ""))
    model_base_url: str = Field(default_factory=lambda: os.getenv("MODEL_BASE_URL", ""))
    model_name: str = Field(default_factory=lambda: os.getenv("MODEL_NAME", ""))
    model_timeout_seconds: int = Field(
        default_factory=lambda: int(os.getenv("MODEL_TIMEOUT_SECONDS", "60")),
        ge=1,
    )
    max_file_size_bytes: int = Field(
        default_factory=lambda: int(os.getenv("MAX_FILE_SIZE_BYTES", "1048576")),
        ge=1,
    )
    max_total_file_size_bytes: int = Field(
        default_factory=lambda: int(os.getenv("MAX_TOTAL_FILE_SIZE_BYTES", "20971520")),
        ge=1,
    )
    max_file_count: int = Field(
        default_factory=lambda: int(os.getenv("MAX_FILE_COUNT", "50")),
        ge=1,
    )
    max_context_chars: int = Field(
        default_factory=lambda: int(os.getenv("MAX_CONTEXT_CHARS", "60000")),
        ge=1,
    )
    database_path: str = Field(
        default_factory=lambda: os.getenv("DATABASE_PATH", "data/codexxx.db"),
        min_length=1,
    )
    session_ttl_seconds: int = Field(
        default_factory=lambda: int(os.getenv("SESSION_TTL_SECONDS", "86400")),
        ge=300,
        le=2_592_000,
    )
    login_max_failures: int = Field(
        default_factory=lambda: int(os.getenv("LOGIN_MAX_FAILURES", "5")),
        ge=1,
        le=100,
    )
    login_failure_window_seconds: int = Field(
        default_factory=lambda: int(os.getenv("LOGIN_FAILURE_WINDOW_SECONDS", "300")),
        ge=10,
        le=86_400,
    )
    allow_legacy_local_workflows: bool = Field(
        default_factory=lambda: os.getenv(
            "ALLOW_LEGACY_LOCAL_WORKFLOWS",
            "true",
        ).strip().casefold()
        in {"1", "true", "yes", "on"},
    )
    usage_window_seconds: int = Field(
        default_factory=lambda: int(os.getenv("USAGE_WINDOW_SECONDS", "86400")),
        ge=60,
        le=2_592_000,
    )
    max_active_tasks_per_user: int = Field(
        default_factory=lambda: int(os.getenv("MAX_ACTIVE_TASKS_PER_USER", "3")),
        ge=1,
        le=1_000,
    )
    max_active_tasks_per_project: int = Field(
        default_factory=lambda: int(os.getenv("MAX_ACTIVE_TASKS_PER_PROJECT", "5")),
        ge=1,
        le=10_000,
    )
    max_model_calls_per_user_window: int = Field(
        default_factory=lambda: int(
            os.getenv("MAX_MODEL_CALLS_PER_USER_WINDOW", "100"),
        ),
        ge=1,
    )
    max_model_calls_per_project_window: int = Field(
        default_factory=lambda: int(
            os.getenv("MAX_MODEL_CALLS_PER_PROJECT_WINDOW", "200"),
        ),
        ge=1,
    )
    max_files_per_user_window: int = Field(
        default_factory=lambda: int(os.getenv("MAX_FILES_PER_USER_WINDOW", "1000")),
        ge=1,
    )
    max_files_per_project_window: int = Field(
        default_factory=lambda: int(os.getenv("MAX_FILES_PER_PROJECT_WINDOW", "2000")),
        ge=1,
    )
    max_patches_per_user_window: int = Field(
        default_factory=lambda: int(os.getenv("MAX_PATCHES_PER_USER_WINDOW", "50")),
        ge=1,
    )
    max_patches_per_project_window: int = Field(
        default_factory=lambda: int(os.getenv("MAX_PATCHES_PER_PROJECT_WINDOW", "100")),
        ge=1,
    )
    max_validations_per_user_window: int = Field(
        default_factory=lambda: int(
            os.getenv("MAX_VALIDATIONS_PER_USER_WINDOW", "100"),
        ),
        ge=1,
    )
    max_validations_per_project_window: int = Field(
        default_factory=lambda: int(
            os.getenv("MAX_VALIDATIONS_PER_PROJECT_WINDOW", "200"),
        ),
        ge=1,
    )
    task_queue_worker_concurrency: int = Field(
        default_factory=lambda: int(os.getenv("TASK_QUEUE_WORKER_CONCURRENCY", "2")),
        ge=1,
        le=32,
    )
    task_queue_lease_seconds: float = Field(
        default_factory=lambda: float(os.getenv("TASK_QUEUE_LEASE_SECONDS", "15")),
        ge=1,
        le=3600,
    )
    task_queue_heartbeat_seconds: float = Field(
        default_factory=lambda: float(os.getenv("TASK_QUEUE_HEARTBEAT_SECONDS", "2")),
        ge=0.1,
        le=300,
    )
    task_queue_poll_seconds: float = Field(
        default_factory=lambda: float(os.getenv("TASK_QUEUE_POLL_SECONDS", "0.1")),
        ge=0.01,
        le=10,
    )
    task_queue_max_attempts: int = Field(
        default_factory=lambda: int(os.getenv("TASK_QUEUE_MAX_ATTEMPTS", "3")),
        ge=1,
        le=20,
    )
    task_queue_retry_base_seconds: float = Field(
        default_factory=lambda: float(os.getenv("TASK_QUEUE_RETRY_BASE_SECONDS", "0.25")),
        ge=0,
        le=300,
    )
    sandbox_runtime: str = Field(
        default_factory=lambda: os.getenv("SANDBOX_RUNTIME", "docker"),
        pattern=r"^[a-zA-Z0-9_.-]+$",
    )
    sandbox_python_image: str = Field(
        default_factory=lambda: os.getenv(
            "SANDBOX_PYTHON_IMAGE",
            "codexxx-sandbox-python:3.13",
        ),
        min_length=1,
    )
    sandbox_node_image: str = Field(
        default_factory=lambda: os.getenv(
            "SANDBOX_NODE_IMAGE",
            "codexxx-sandbox-node:22",
        ),
        min_length=1,
    )
    sandbox_cpu_limit: float = Field(
        default_factory=lambda: float(os.getenv("SANDBOX_CPU_LIMIT", "1")),
        gt=0,
        le=8,
    )
    sandbox_memory_mb: int = Field(
        default_factory=lambda: int(os.getenv("SANDBOX_MEMORY_MB", "512")),
        ge=64,
        le=8192,
    )
    sandbox_disk_mb: int = Field(
        default_factory=lambda: int(os.getenv("SANDBOX_DISK_MB", "128")),
        ge=16,
        le=4096,
    )
    sandbox_pids_limit: int = Field(
        default_factory=lambda: int(os.getenv("SANDBOX_PIDS_LIMIT", "128")),
        ge=16,
        le=4096,
    )
    sandbox_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("SANDBOX_TIMEOUT_SECONDS", "60")),
        ge=1,
        le=600,
    )
    sandbox_max_output_chars: int = Field(
        default_factory=lambda: int(os.getenv("SANDBOX_MAX_OUTPUT_CHARS", "12000")),
        ge=1000,
        le=200_000,
    )
    retention_cleanup_interval_seconds: int = Field(
        default_factory=lambda: int(
            os.getenv("RETENTION_CLEANUP_INTERVAL_SECONDS", "3600"),
        ),
        ge=60,
        le=86_400,
    )
    expired_session_retention_days: int = Field(
        default_factory=lambda: int(
            os.getenv("EXPIRED_SESSION_RETENTION_DAYS", "7"),
        ),
        ge=1,
        le=365,
    )
    login_attempt_retention_days: int = Field(
        default_factory=lambda: int(
            os.getenv("LOGIN_ATTEMPT_RETENTION_DAYS", "7"),
        ),
        ge=1,
        le=365,
    )
    terminal_task_retention_days: int = Field(
        default_factory=lambda: int(
            os.getenv("TERMINAL_TASK_RETENTION_DAYS", "90"),
        ),
        ge=1,
        le=3650,
    )
    audit_log_retention_days: int = Field(
        default_factory=lambda: int(
            os.getenv("AUDIT_LOG_RETENTION_DAYS", "365"),
        ),
        ge=1,
        le=3650,
    )
    usage_record_retention_days: int = Field(
        default_factory=lambda: int(
            os.getenv("USAGE_RECORD_RETENTION_DAYS", "30"),
        ),
        ge=1,
        le=3650,
    )


settings = Settings()
