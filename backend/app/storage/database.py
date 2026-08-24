"""Small versioned SQLite boundary for durable application state."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock

SCHEMA_VERSION = 5

MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_system_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_system_admin IN (0, 1)),
    disabled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS login_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    client_address TEXT NOT NULL,
    succeeded INTEGER NOT NULL CHECK (succeeded IN (0, 1)),
    attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempt_window
ON login_attempts(email, client_address, attempted_at);

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    run_mode TEXT NOT NULL CHECK (run_mode IN ('local', 'service')),
    default_model TEXT,
    file_count INTEGER NOT NULL DEFAULT 0 CHECK (file_count >= 0),
    index_status TEXT NOT NULL DEFAULT 'not_started',
    archived_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_user_id);

CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(user_id);

CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id TEXT PRIMARY KEY,
    actor_user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    project_id TEXT REFERENCES projects(project_id) ON DELETE SET NULL,
    request_id TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_project ON audit_logs(project_id, created_at);
"""

MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
    owner_user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,
    goal TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('plan', 'execute')),
    status TEXT NOT NULL,
    plan_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    transitions_json TEXT NOT NULL DEFAULT '[]',
    final_answer TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0
        CHECK (cancel_requested IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (project_id IS NULL AND owner_user_id IS NULL)
        OR (project_id IS NOT NULL AND owner_user_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_project
ON agent_tasks(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_owner
ON agent_tasks(owner_user_id, updated_at);

CREATE TABLE IF NOT EXISTS task_files (
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (task_id, path)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_task
ON tool_calls(task_id, sequence);

CREATE TABLE IF NOT EXISTS patches (
    patch_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    risk TEXT NOT NULL,
    files_json TEXT NOT NULL,
    base_contents_json TEXT NOT NULL,
    proposed_contents_json TEXT NOT NULL,
    suggested_validators_json TEXT NOT NULL DEFAULT '[]',
    events_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_patches_task
ON patches(task_id, updated_at);

CREATE TABLE IF NOT EXISTS validation_runs (
    validation_id TEXT PRIMARY KEY,
    patch_id TEXT NOT NULL REFERENCES patches(patch_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    run_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_validation_runs_patch
ON validation_runs(patch_id, created_at);
"""

MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS usage_records (
    usage_id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,
    project_id TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
    resource TEXT NOT NULL CHECK (
        resource IN ('model_calls', 'files', 'patches', 'validations')
    ),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    resource_id TEXT,
    request_id TEXT,
    occurred_at TEXT NOT NULL,
    CHECK (
        (project_id IS NULL)
        OR (project_id IS NOT NULL AND user_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_usage_user_window
ON usage_records(user_id, resource, occurred_at);
CREATE INDEX IF NOT EXISTS idx_usage_project_window
ON usage_records(project_id, resource, occurred_at);
CREATE INDEX IF NOT EXISTS idx_usage_legacy_window
ON usage_records(resource, occurred_at)
WHERE user_id IS NULL AND project_id IS NULL;
"""

MIGRATION_4 = """
CREATE TABLE IF NOT EXISTS task_queue (
    job_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE
        REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'cancelled', 'failed')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    idempotency_key TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0
        CHECK (cancel_requested IN (0, 1)),
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK (
        (status = 'running' AND lease_owner IS NOT NULL
            AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR status <> 'running'
    )
);
CREATE INDEX IF NOT EXISTS idx_task_queue_available
ON task_queue(status, available_at, created_at);
CREATE INDEX IF NOT EXISTS idx_task_queue_lease
ON task_queue(status, lease_expires_at);

CREATE TABLE IF NOT EXISTS task_idempotency (
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scope, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_task_idempotency_task
ON task_idempotency(task_id);
"""

MIGRATION_5 = """
CREATE TABLE IF NOT EXISTS organizations (
    organization_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_organizations_owner
ON organizations(owner_user_id);

CREATE TABLE IF NOT EXISTS organization_members (
    organization_id TEXT NOT NULL
        REFERENCES organizations(organization_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_organization_members_user
ON organization_members(user_id);

"""


class Database:
    """Open short-lived SQLite connections and apply idempotent migrations."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._initialized = False
        self._initialize_lock = Lock()
        self._generation = 0

    @property
    def generation(self) -> int:
        """Change whenever services should discard their process-local caches."""

        return self._generation

    def reconfigure(self, path: str) -> None:
        """Point all shared services at another database, primarily for tests."""

        with self._initialize_lock:
            self.path = path
            self._initialized = False
            self._generation += 1

    def _ensure_parent(self) -> None:
        if self.path == ":memory:" or self.path.startswith("file:"):
            return
        Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        self.initialize()
        connection = sqlite3.connect(
            self.path,
            timeout=5,
            uri=self.path.startswith("file:"),
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            self._ensure_parent()
            connection = sqlite3.connect(
                self.path,
                timeout=5,
                uri=self.path.startswith("file:"),
            )
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 5000")
                if self.path != ":memory:":
                    connection.execute("PRAGMA journal_mode = WAL")
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"数据库版本 {version} 高于应用支持版本 {SCHEMA_VERSION}。",
                    )
                if version < 1:
                    connection.executescript(MIGRATION_1)
                    version = 1
                    connection.execute("PRAGMA user_version = 1")
                if version < 2:
                    connection.executescript(MIGRATION_2)
                    version = 2
                    connection.execute("PRAGMA user_version = 2")
                if version < 3:
                    connection.executescript(MIGRATION_3)
                    version = 3
                    connection.execute("PRAGMA user_version = 3")
                if version < 4:
                    connection.executescript(MIGRATION_4)
                    connection.execute("PRAGMA user_version = 4")
                    version = 4
                if version < 5:
                    connection.executescript(MIGRATION_5)
                    self._add_column_if_missing(
                        connection,
                        "projects",
                        "organization_id",
                        "TEXT REFERENCES organizations(organization_id) ON DELETE CASCADE",
                    )
                    self._add_column_if_missing(
                        connection,
                        "audit_logs",
                        "organization_id",
                        "TEXT REFERENCES organizations(organization_id) ON DELETE SET NULL",
                    )
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS idx_projects_organization "
                        "ON projects(organization_id, updated_at)",
                    )
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS idx_audit_organization "
                        "ON audit_logs(organization_id, created_at)",
                    )
                    self._backfill_organizations(connection)
                    connection.execute(
                        """
                        UPDATE audit_logs
                        SET organization_id = (
                            SELECT organization_id FROM projects
                            WHERE projects.project_id = audit_logs.project_id
                        )
                        WHERE organization_id IS NULL AND project_id IS NOT NULL
                        """,
                    )
                    connection.execute("PRAGMA user_version = 5")
                connection.commit()
            finally:
                connection.close()
            self._initialized = True

    @staticmethod
    def _add_column_if_missing(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _backfill_organizations(connection: sqlite3.Connection) -> None:
        """Give pre-organization users and projects a private default organization."""

        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        users = connection.execute(
            "SELECT user_id, display_name FROM users ORDER BY user_id",
        ).fetchall()
        for user in users:
            user_id = str(user[0])
            display_name = str(user[1])
            organization_id = f"personal-{user_id}"
            connection.execute(
                """
                INSERT OR IGNORE INTO organizations (
                    organization_id, owner_user_id, name, description,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    user_id,
                    f"{display_name} 的个人组织",
                    "阶段 7 迁移创建的默认组织",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO organization_members (
                    organization_id, user_id, role, created_at, updated_at
                ) VALUES (?, ?, 'owner', ?, ?)
                """,
                (organization_id, user_id, now, now),
            )

        connection.execute(
            """
            UPDATE projects
            SET organization_id = 'personal-' || owner_user_id
            WHERE organization_id IS NULL
            """,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO organization_members (
                organization_id, user_id, role, created_at, updated_at
            )
            SELECT p.organization_id, pm.user_id, 'member', pm.created_at, pm.updated_at
            FROM project_members AS pm
            JOIN projects AS p ON p.project_id = pm.project_id
            WHERE p.organization_id IS NOT NULL
            """,
        )
