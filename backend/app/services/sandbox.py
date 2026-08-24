"""Fail-closed Docker sandbox for a fixed validation-command allowlist."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from threading import Thread
from typing import BinaryIO, Literal
from uuid import uuid4

from app.config import Settings
from app.schemas.patches import ValidationStatus
from app.schemas.sandbox import SandboxStatusResponse

logger = logging.getLogger("uvicorn.error")

SandboxLanguage = Literal["python", "node"]


@dataclass(frozen=True)
class SandboxCommand:
    name: str
    title: str
    description: str
    language: SandboxLanguage
    argv: tuple[str, ...]


@dataclass(frozen=True)
class SandboxResult:
    status: ValidationStatus
    exit_code: int | None
    output: str
    duration_ms: float


class SandboxError(Exception):
    """A sandbox request was rejected before host execution."""

    status_code = 409

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class SandboxUnavailableError(SandboxError):
    status_code = 503


SANDBOX_COMMANDS: dict[str, SandboxCommand] = {
    "sandbox_pytest": SandboxCommand(
        name="sandbox_pytest",
        title="Pytest 单元测试",
        description="在断网 Python 容器中运行项目测试。",
        language="python",
        argv=("python", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
    ),
    "sandbox_ruff": SandboxCommand(
        name="sandbox_ruff",
        title="Ruff 静态检查",
        description="在断网 Python 容器中运行 Ruff 静态检查。",
        language="python",
        argv=("python", "-m", "ruff", "check", ".", "--no-cache"),
    ),
    "sandbox_mypy": SandboxCommand(
        name="sandbox_mypy",
        title="Mypy 类型检查",
        description="在断网 Python 容器中运行 Mypy 类型检查。",
        language="python",
        argv=("python", "-m", "mypy", ".", "--cache-dir=/tmp/mypy"),
    ),
    "sandbox_npm_test": SandboxCommand(
        name="sandbox_npm_test",
        title="前端单元测试",
        description="在断网 Node.js 容器中运行项目声明的测试脚本。",
        language="node",
        argv=("npm", "test", "--", "--runInBand"),
    ),
    "sandbox_npm_build": SandboxCommand(
        name="sandbox_npm_build",
        title="前端生产构建",
        description="在断网 Node.js 容器中运行项目声明的构建脚本。",
        language="node",
        argv=("npm", "run", "build"),
    ),
}


class SandboxService:
    """Stream a trusted file snapshot into an ephemeral, resource-bounded container."""

    def __init__(self) -> None:
        self._status_cache: tuple[float, str, bool, str | None] | None = None

    def _runtime_status(self, settings: Settings) -> tuple[bool, str | None]:
        cached = self._status_cache
        now = time.monotonic()
        cache_key = (
            f"{settings.sandbox_runtime}|{settings.sandbox_python_image}|"
            f"{settings.sandbox_node_image}"
        )
        if cached is not None and cached[1] == cache_key and now - cached[0] < 5:
            return cached[2], cached[3]
        runtime_path = shutil.which(settings.sandbox_runtime)
        if runtime_path is None:
            result = (False, "未安装容器运行时，沙箱命令保持禁用。")
        else:
            try:
                process = subprocess.run(
                    [runtime_path, "info", "--format", "{{.ServerVersion}}"],
                    capture_output=True,
                    check=False,
                    timeout=3,
                )
            except (OSError, subprocess.TimeoutExpired):
                result = (False, "容器运行时不可连接，沙箱命令保持禁用。")
            else:
                if process.returncode != 0:
                    result = (False, "容器运行时未就绪，沙箱命令保持禁用。")
                else:
                    missing_images = []
                    for image in {
                        settings.sandbox_python_image,
                        settings.sandbox_node_image,
                    }:
                        try:
                            inspected = subprocess.run(
                                [runtime_path, "image", "inspect", image],
                                capture_output=True,
                                check=False,
                                timeout=3,
                            )
                        except (OSError, subprocess.TimeoutExpired):
                            missing_images.append(image)
                        else:
                            if inspected.returncode != 0:
                                missing_images.append(image)
                    result = (
                        (True, None)
                        if not missing_images
                        else (
                            False,
                            "沙箱基础镜像尚未构建，命令保持禁用："
                            + "、".join(sorted(missing_images)),
                        )
                    )
        self._status_cache = (now, cache_key, result[0], result[1])
        return result

    def status(self, settings: Settings) -> SandboxStatusResponse:
        available, reason = self._runtime_status(settings)
        return SandboxStatusResponse(
            available=available,
            runtime=settings.sandbox_runtime,
            reason=reason,
            allowed_commands=list(SANDBOX_COMMANDS),
        )

    def _archive(self, files: dict[str, str], settings: Settings) -> bytes:
        maximum = settings.sandbox_disk_mb * 1024 * 1024
        encoded_files: list[tuple[PurePosixPath, bytes]] = []
        total = 0
        for raw_path, content in files.items():
            path = PurePosixPath(raw_path.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise SandboxError(f"沙箱文件路径不安全：{raw_path}。")
            encoded = content.encode("utf-8")
            total += len(encoded)
            if total > maximum:
                raise SandboxError("任务文件超过沙箱临时磁盘上限。")
            encoded_files.append((path, encoded))

        archive = io.BytesIO()
        directories = {
            parent
            for path, _content in encoded_files
            for parent in path.parents
            if str(parent) != "."
        }
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for directory in sorted(directories, key=lambda item: (len(item.parts), str(item))):
                info = tarfile.TarInfo(str(directory))
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.uid = 65534
                info.gid = 65534
                tar.addfile(info)
            for path, content in encoded_files:
                info = tarfile.TarInfo(str(path))
                info.size = len(content)
                info.mode = 0o644
                info.uid = 65534
                info.gid = 65534
                tar.addfile(info, io.BytesIO(content))
        return archive.getvalue()

    def _docker_command(
        self,
        command: SandboxCommand,
        settings: Settings,
        container_name: str,
    ) -> list[str]:
        image = (
            settings.sandbox_python_image
            if command.language == "python"
            else settings.sandbox_node_image
        )
        memory = f"{settings.sandbox_memory_mb}m"
        disk = f"{settings.sandbox_disk_mb}m"
        tmp = f"{max(16, min(64, settings.sandbox_disk_mb // 2))}m"
        trusted_script = "tar -xf - -C /workspace && cd /workspace && exec \"$@\""
        return [
            settings.sandbox_runtime,
            "run",
            "--name",
            container_name,
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cpus",
            str(settings.sandbox_cpu_limit),
            "--memory",
            memory,
            "--memory-swap",
            memory,
            "--pids-limit",
            str(settings.sandbox_pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--ulimit",
            "nofile=256:256",
            "--user",
            "65534:65534",
            "--tmpfs",
            f"/workspace:rw,nosuid,nodev,size={disk}",
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,noexec,size={tmp}",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--workdir",
            "/workspace",
            "--interactive",
            "--entrypoint",
            "/bin/sh",
            image,
            "-c",
            trusted_script,
            "sandbox-entrypoint",
            *command.argv,
        ]

    def _drain(
        self,
        stream: BinaryIO,
        target: bytearray,
        maximum: int,
    ) -> None:
        while chunk := stream.read(8192):
            if len(target) < maximum:
                target.extend(chunk[: maximum - len(target)])

    def _force_remove(self, container_name: str, settings: Settings) -> None:
        try:
            subprocess.run(
                [settings.sandbox_runtime, "rm", "--force", container_name],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.exception("sandbox_container_cleanup_failed name=%s", container_name)

    def run(
        self,
        name: str,
        *,
        files: dict[str, str],
        settings: Settings,
    ) -> SandboxResult:
        command = SANDBOX_COMMANDS.get(name)
        if command is None:
            raise SandboxError("请求的命令不在沙箱允许列表中。")
        available, reason = self._runtime_status(settings)
        if not available:
            raise SandboxUnavailableError(reason or "容器沙箱不可用。")
        archive = self._archive(files, settings)
        container_name = f"codexxx-sandbox-{uuid4().hex}"
        argv = self._docker_command(command, settings, container_name)
        maximum_bytes = settings.sandbox_max_output_chars * 4
        stdout = bytearray()
        stderr = bytearray()
        started = time.perf_counter()
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None
            readers = [
                Thread(target=self._drain, args=(process.stdout, stdout, maximum_bytes)),
                Thread(target=self._drain, args=(process.stderr, stderr, maximum_bytes)),
            ]
            for reader in readers:
                reader.start()
            process.stdin.write(archive)
            process.stdin.close()
            try:
                exit_code = process.wait(timeout=settings.sandbox_timeout_seconds)
            except subprocess.TimeoutExpired:
                self._force_remove(container_name, settings)
                process.kill()
                exit_code = None
            for reader in readers:
                reader.join(timeout=5)
        except OSError as error:
            self._force_remove(container_name, settings)
            raise SandboxUnavailableError("无法启动容器沙箱。") from error
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        combined = bytes(stdout)
        if stderr:
            combined += b"\n[stderr]\n" + bytes(stderr)
        output = combined.decode("utf-8", errors="replace")
        if len(output) > settings.sandbox_max_output_chars:
            output = output[: settings.sandbox_max_output_chars] + "\n[输出已截断]"
        if exit_code is None:
            return SandboxResult(
                status="timed_out",
                exit_code=None,
                output=output or "沙箱命令执行超时。",
                duration_ms=duration_ms,
            )
        return SandboxResult(
            status="passed" if exit_code == 0 else "failed",
            exit_code=exit_code,
            output=output or "命令未产生输出。",
            duration_ms=duration_ms,
        )


sandbox_service = SandboxService()
