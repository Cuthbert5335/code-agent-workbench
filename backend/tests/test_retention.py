from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import settings
from app.services.retention import retention_service
from app.services.security import security_service
from app.storage import database


@pytest.fixture(autouse=True)
def isolated_retention_database(tmp_path: Path) -> Iterator[None]:
    security_service.use_database_for_test(str(tmp_path / "retention.db"))
    yield


def test_cleanup_removes_only_expired_and_terminal_data() -> None:
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    old = (now - timedelta(days=3)).isoformat()
    recent = (now - timedelta(hours=12)).isoformat()
    app_settings = settings.model_copy(
        update={
            "expired_session_retention_days": 1,
            "login_attempt_retention_days": 1,
            "terminal_task_retention_days": 1,
            "audit_log_retention_days": 1,
            "usage_record_retention_days": 1,
            "usage_window_seconds": 60,
        },
    )

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO users (
                user_id, email, display_name, password_hash, created_at, updated_at
            ) VALUES ('user', 'retention@example.com', 'Retention', 'hash', ?, ?)
            """,
            (recent, recent),
        )
        connection.execute(
            """
            INSERT INTO projects (
                project_id, owner_user_id, name, run_mode, created_at, updated_at
            ) VALUES ('project', 'user', 'Retention', 'local', ?, ?)
            """,
            (recent, recent),
        )
        connection.executemany(
            """
            INSERT INTO sessions (
                session_id, user_id, token_hash, created_at, expires_at, last_used_at
            ) VALUES (?, 'user', ?, ?, ?, ?)
            """,
            [
                ("old-session", "old-token", old, old, old),
                ("recent-session", "recent-token", recent, recent, recent),
            ],
        )
        connection.executemany(
            """
            INSERT INTO login_attempts (
                email, client_address, succeeded, attempted_at
            ) VALUES ('retention@example.com', '127.0.0.1', 0, ?)
            """,
            [(old,), (recent,)],
        )
        connection.executemany(
            """
            INSERT INTO audit_logs (
                audit_id, actor_user_id, action, resource_type, detail_json, created_at
            ) VALUES (?, 'user', 'test', 'test', '{}', ?)
            """,
            [("old-audit", old), ("recent-audit", recent)],
        )
        connection.executemany(
            """
            INSERT INTO usage_records (
                usage_id, user_id, project_id, resource, quantity, occurred_at
            ) VALUES (?, 'user', 'project', 'files', 1, ?)
            """,
            [("old-usage", old), ("recent-usage", recent)],
        )
        connection.executemany(
            """
            INSERT INTO agent_tasks (
                task_id, project_id, owner_user_id, goal, mode, status,
                created_at, updated_at
            ) VALUES (?, 'project', 'user', 'test', 'plan', ?, ?, ?)
            """,
            [
                ("old-terminal", "completed", old, old),
                ("old-active", "waiting_for_confirmation", old, old),
                ("recent-terminal", "completed", recent, recent),
            ],
        )
        connection.execute(
            """
            INSERT INTO task_files (task_id, path, language, content)
            VALUES ('old-terminal', 'src/a.py', 'Python', 'x = 1')
            """,
        )
        connection.execute(
            """
            INSERT INTO patches (
                patch_id, task_id, status, summary, risk, files_json,
                base_contents_json, proposed_contents_json, created_at, updated_at
            ) VALUES (
                'old-patch', 'old-terminal', 'draft', 'test', 'test', '[]',
                '{}', '{}', ?, ?
            )
            """,
            (old, old),
        )
        connection.execute(
            """
            INSERT INTO validation_runs (
                validation_id, patch_id, status, created_at, run_json
            ) VALUES ('old-validation', 'old-patch', 'passed', ?, '{}')
            """,
            (old,),
        )

    result = retention_service.cleanup(app_settings, now=now)

    assert result.expired_sessions == 1
    assert result.login_attempts == 1
    assert result.terminal_tasks == 1
    assert result.audit_logs == 1
    assert result.usage_records == 1
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0] == 1
        remaining_tasks = {
            row[0] for row in connection.execute("SELECT task_id FROM agent_tasks")
        }
        assert remaining_tasks == {"old-active", "recent-terminal"}
        assert connection.execute("SELECT COUNT(*) FROM patches").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM validation_runs").fetchone()[0] == 0


def test_usage_cleanup_never_shortens_the_active_quota_window() -> None:
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    within_window = (now - timedelta(days=2)).isoformat()
    app_settings = settings.model_copy(
        update={
            "usage_record_retention_days": 1,
            "usage_window_seconds": 3 * 24 * 60 * 60,
        },
    )
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO usage_records (
                usage_id, resource, quantity, occurred_at
            ) VALUES ('window-usage', 'files', 1, ?)
            """,
            (within_window,),
        )

    result = retention_service.cleanup(app_settings, now=now)

    assert result.usage_records == 0
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0] == 1
        connection.execute(
            "UPDATE usage_records SET occurred_at = ?",
            ((now - timedelta(days=4)).isoformat(),),
        )
    result = retention_service.cleanup(app_settings, now=now)
    assert result.usage_records == 1
