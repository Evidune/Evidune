"""Runtime environments and observability for harness-managed tasks."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import yaml


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _utc_ts() -> float:
    return time.time()


class ObservabilityStore:
    """Lightweight structured event store rooted inside one runtime environment."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._paths = {
            "log": self.root / "logs.jsonl",
            "metric": self.root / "metrics.jsonl",
            "trace": self.root / "traces.jsonl",
        }

    def record(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind not in self._paths:
            raise ValueError(f"Unknown observability kind: {kind}")
        record = {"timestamp": _utc_ts(), **payload}
        with self._paths[kind].open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def query(
        self, kind: str, *, filters: dict[str, Any] | None = None, limit: int = 20
    ) -> list[dict]:
        if kind not in self._paths:
            raise ValueError(f"Unknown observability kind: {kind}")
        filters = {key: value for key, value in (filters or {}).items() if value not in ("", None)}
        path = self._paths[kind]
        if not path.is_file():
            return []
        matches: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if all(
                    str(row.get(key, "")).find(str(value)) >= 0 for key, value in filters.items()
                ):
                    matches.append(row)
        return matches[-limit:]


@dataclass
class RuntimeEnvironment:
    """One isolated local runtime environment associated with a harness task."""

    environment_id: str
    task_id: str
    root: Path
    base_dir: Path
    source_config_path: Path | None = None
    service_host: str = "127.0.0.1"
    startup_timeout_s: int = 30
    healthcheck_path: str = "/api/skills"
    service_port: int = 0
    observability: ObservabilityStore = field(init=False)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.services_dir.mkdir(parents=True, exist_ok=True)
        self.observability = ObservabilityStore(self.root / "observability")
        self._write_manifest()

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def services_dir(self) -> Path:
        return self.root / "services"

    @property
    def memory_path(self) -> Path:
        return self.root / "memory.db"

    @property
    def service_log_path(self) -> Path:
        return self.services_dir / "serve.log"

    @property
    def service_state_path(self) -> Path:
        return self.services_dir / "serve.state.json"

    @property
    def runtime_config_path(self) -> Path:
        return self.root / "serve.runtime.yaml"

    @property
    def base_url(self) -> str:
        if self.service_port <= 0:
            return ""
        return f"http://{self.service_host}:{self.service_port}"

    def _write_manifest(self) -> None:
        payload = {
            "environment_id": self.environment_id,
            "task_id": self.task_id,
            "root": str(self.root),
            "base_dir": str(self.base_dir),
            "source_config_path": str(self.source_config_path) if self.source_config_path else "",
            "service_host": self.service_host,
            "service_port": self.service_port,
            "memory_path": str(self.memory_path),
            "artifacts_dir": str(self.artifacts_dir),
        }
        (self.root / "environment.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_service_state(self) -> dict[str, Any]:
        if not self.service_state_path.is_file():
            return {}
        try:
            return json.loads(self.service_state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _write_service_state(self, payload: dict[str, Any]) -> None:
        self.service_state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_manifest()

    @staticmethod
    def _pid_running(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _clone_runtime_config(self, port: int) -> Path:
        if self.source_config_path is None or not self.source_config_path.is_file():
            raise FileNotFoundError("Runtime environment requires a valid source config path")
        raw = yaml.safe_load(self.source_config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("Runtime config root must be a YAML mapping")
        raw.setdefault("memory", {})
        raw["memory"]["path"] = str(self.memory_path)
        raw.setdefault("agent", {})
        raw["agent"].setdefault("emergence", {})
        raw["agent"]["emergence"]["output_dir"] = str(self.root / "emerged_skills")
        raw["gateways"] = [{"type": "web", "host": self.service_host, "port": port}]
        self.runtime_config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        return self.runtime_config_path

    def health(self) -> dict[str, Any]:
        state = self._read_service_state()
        running = self._pid_running(int(state.get("pid", 0)))
        url = state.get("base_url") or self.base_url
        healthy = False
        error = ""
        if running and url:
            try:
                with urlopen(url + self.healthcheck_path, timeout=5) as response:
                    healthy = 200 <= getattr(response, "status", 200) < 400
            except URLError as exc:
                error = str(exc)
        return {
            "environment_id": self.environment_id,
            "running": running,
            "healthy": healthy,
            "base_url": url,
            "pid": int(state.get("pid", 0) or 0),
            "port": int(state.get("port", 0) or 0),
            "status": "healthy" if healthy else ("running" if running else "stopped"),
            "error": error,
        }

    def status(self) -> dict[str, Any]:
        state = self._read_service_state()
        health = self.health()
        return {
            "environment_id": self.environment_id,
            "task_id": self.task_id,
            "root": str(self.root),
            "memory_path": str(self.memory_path),
            "artifacts_dir": str(self.artifacts_dir),
            "service": {
                "pid": int(state.get("pid", 0) or 0),
                "port": int(state.get("port", 0) or 0),
                "base_url": state.get("base_url", ""),
                "status": health["status"],
                "healthy": health["healthy"],
            },
        }

    def up(self) -> dict[str, Any]:
        current = self.health()
        if current["running"]:
            return current
        self.service_port = self.service_port or _free_port(self.service_host)
        runtime_config = self._clone_runtime_config(self.service_port)
        log_handle = self.service_log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "core.loop",
                "serve",
                "--config",
                str(runtime_config),
                "--base-dir",
                str(self.base_dir),
            ],
            cwd=str(self.base_dir),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env={**os.environ, "AIFLAY_ENVIRONMENT_ID": self.environment_id},
        )
        state = {
            "pid": process.pid,
            "port": self.service_port,
            "base_url": self.base_url,
            "started_at": _utc_ts(),
        }
        self._write_service_state(state)
        self.observability.record(
            "log",
            {"level": "info", "event": "environment.up", "message": "Started web runtime", **state},
        )
        deadline = time.time() + self.startup_timeout_s
        while time.time() < deadline:
            health = self.health()
            if health["healthy"]:
                return health
            time.sleep(0.25)
        return self.health()

    def down(self) -> dict[str, Any]:
        state = self._read_service_state()
        pid = int(state.get("pid", 0) or 0)
        if pid and self._pid_running(pid):
            os.kill(pid, signal.SIGTERM)
            deadline = time.time() + 5
            while time.time() < deadline and self._pid_running(pid):
                time.sleep(0.1)
            if self._pid_running(pid):
                os.kill(pid, signal.SIGKILL)
        self.observability.record(
            "log",
            {
                "level": "info",
                "event": "environment.down",
                "message": "Stopped web runtime",
                "pid": pid,
            },
        )
        self._write_service_state(
            {
                "pid": 0,
                "port": int(state.get("port", 0) or 0),
                "base_url": state.get("base_url", ""),
            }
        )
        return self.health()

    def restart(self) -> dict[str, Any]:
        self.down()
        return self.up()


class HarnessRuntimeManager:
    """Factory and registry for task-scoped runtime environments."""

    def __init__(
        self,
        *,
        runtime_dir: Path,
        base_dir: Path,
        source_config_path: Path | None = None,
        service_host: str = "127.0.0.1",
        startup_timeout_s: int = 30,
        healthcheck_path: str = "/api/skills",
    ) -> None:
        self.runtime_dir = runtime_dir
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.base_dir = base_dir
        self.source_config_path = source_config_path
        self.service_host = service_host
        self.startup_timeout_s = startup_timeout_s
        self.healthcheck_path = healthcheck_path
        self._environments: dict[str, RuntimeEnvironment] = {}

    def create_environment(self, task_id: str) -> RuntimeEnvironment:
        env_id = f"env-{task_id.split('-')[-1]}-{uuid.uuid4().hex[:6]}"
        environment = RuntimeEnvironment(
            environment_id=env_id,
            task_id=task_id,
            root=self.runtime_dir / env_id,
            base_dir=self.base_dir,
            source_config_path=self.source_config_path,
            service_host=self.service_host,
            startup_timeout_s=self.startup_timeout_s,
            healthcheck_path=self.healthcheck_path,
        )
        self._environments[task_id] = environment
        return environment

    def get_environment(self, task_id: str) -> RuntimeEnvironment | None:
        return self._environments.get(task_id)

    def load_environment(self, environment_id: str) -> RuntimeEnvironment:
        root = self.runtime_dir / environment_id
        manifest_path = root / "environment.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Unknown environment: {environment_id}")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        environment = RuntimeEnvironment(
            environment_id=environment_id,
            task_id=payload.get("task_id", ""),
            root=root,
            base_dir=Path(payload.get("base_dir") or self.base_dir),
            source_config_path=(
                Path(payload["source_config_path"])
                if payload.get("source_config_path")
                else self.source_config_path
            ),
            service_host=payload.get("service_host", self.service_host),
            startup_timeout_s=self.startup_timeout_s,
            healthcheck_path=self.healthcheck_path,
            service_port=int(payload.get("service_port", 0) or 0),
        )
        self._environments[environment.task_id] = environment
        return environment
