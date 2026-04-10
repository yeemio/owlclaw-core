"""Launch and monitor the OwlClaw workflow automation processes."""

from __future__ import annotations

import argparse
import json
import locale
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import workflow_mailbox  # noqa: E402
import workflow_objects  # noqa: E402


DETACHED_FLAGS = 0
if os.name == "nt":
    DETACHED_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    role: str
    workdir: str
    command: list[str]
    log_path: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _windows_text_encoding() -> str:
    return locale.getpreferredencoding(False) or "utf-8"


def _runtime_dir(repo_root: Path) -> Path:
    return repo_root / ".kiro" / "runtime"


def _supervisor_dir(repo_root: Path) -> Path:
    return _runtime_dir(repo_root) / "supervisor"


def _supervisor_pids_dir(repo_root: Path) -> Path:
    return _supervisor_dir(repo_root) / "pids"


def _supervisor_logs_dir(repo_root: Path) -> Path:
    return _supervisor_dir(repo_root) / "logs"


def ensure_supervisor_dirs(repo_root: Path) -> None:
    workflow_mailbox.ensure_runtime_dirs(repo_root)
    workflow_objects.ensure_object_dirs(repo_root)
    _supervisor_pids_dir(repo_root).mkdir(parents=True, exist_ok=True)
    _supervisor_logs_dir(repo_root).mkdir(parents=True, exist_ok=True)


def _pid_path(repo_root: Path, name: str) -> Path:
    return _supervisor_pids_dir(repo_root) / f"{name}.json"


def _default_worktree_paths(repo_root: Path) -> dict[str, Path]:
    parent = repo_root.parent
    return {
        "main": repo_root,
        "review": parent / "owlclaw-review",
        "codex": parent / "owlclaw-codex",
        "codex-gpt": parent / "owlclaw-codex-gpt",
    }


def _log_path(repo_root: Path, name: str) -> Path:
    return _supervisor_logs_dir(repo_root) / f"{name}.log"


def default_worker_specs(repo_root: Path, interval: int) -> list[WorkerSpec]:
    worktrees = _default_worktree_paths(repo_root)
    script_root = repo_root / "scripts"
    return [
        WorkerSpec(
            name="orchestrator",
            role="orchestrator",
            workdir=str(repo_root),
            command=[
                "poetry",
                "run",
                "python",
                str(script_root / "workflow_orchestrator.py"),
                "--repo-root",
                str(repo_root),
                "--interval",
                str(interval),
            ],
            log_path=str(_log_path(repo_root, "orchestrator")),
        ),
        WorkerSpec(
            name="main-agent",
            role="agent",
            workdir=str(worktrees["main"]),
            command=[
                "poetry",
                "run",
                "python",
                str(script_root / "workflow_executor.py"),
                "--repo-root",
                str(repo_root),
                "--agent",
                "main",
                "--interval",
                str(interval),
            ],
            log_path=str(_log_path(repo_root, "main-agent")),
        ),
        WorkerSpec(
            name="main-mailbox-agent",
            role="mailbox-agent",
            workdir=str(worktrees["main"]),
            command=[
                "poetry",
                "run",
                "python",
                str(script_root / "workflow_agent.py"),
                "--repo-root",
                str(repo_root),
                "--agent",
                "main",
                "--interval",
                str(interval),
            ],
            log_path=str(_log_path(repo_root, "main-mailbox-agent")),
        ),
        WorkerSpec(
            name="review-agent",
            role="agent",
            workdir=str(worktrees["review"]),
            command=[
                "poetry",
                "run",
                "python",
                str(script_root / "workflow_executor.py"),
                "--repo-root",
                str(repo_root),
                "--agent",
                "review",
                "--interval",
                str(interval),
            ],
            log_path=str(_log_path(repo_root, "review-agent")),
        ),
        WorkerSpec(
            name="review-mailbox-agent",
            role="mailbox-agent",
            workdir=str(worktrees["review"]),
            command=[
                "poetry",
                "run",
                "python",
                str(script_root / "workflow_agent.py"),
                "--repo-root",
                str(repo_root),
                "--agent",
                "review",
                "--interval",
                str(interval),
            ],
            log_path=str(_log_path(repo_root, "review-mailbox-agent")),
        ),
        WorkerSpec(
            name="codex-agent",
            role="agent",
            workdir=str(worktrees["codex"]),
            command=[
                "poetry",
                "run",
                "python",
                str(script_root / "workflow_executor.py"),
                "--repo-root",
                str(repo_root),
                "--agent",
                "codex",
                "--interval",
                str(interval),
            ],
            log_path=str(_log_path(repo_root, "codex-agent")),
        ),
        WorkerSpec(
            name="codex-mailbox-agent",
            role="mailbox-agent",
            workdir=str(worktrees["codex"]),
            command=[
                "poetry",
                "run",
                "python",
                str(script_root / "workflow_agent.py"),
                "--repo-root",
                str(repo_root),
                "--agent",
                "codex",
                "--interval",
                str(interval),
            ],
            log_path=str(_log_path(repo_root, "codex-mailbox-agent")),
        ),
        WorkerSpec(
            name="codex-gpt-agent",
            role="agent",
            workdir=str(worktrees["codex-gpt"]),
            command=[
                "poetry",
                "run",
                "python",
                str(script_root / "workflow_executor.py"),
                "--repo-root",
                str(repo_root),
                "--agent",
                "codex-gpt",
                "--interval",
                str(interval),
            ],
            log_path=str(_log_path(repo_root, "codex-gpt-agent")),
        ),
        WorkerSpec(
            name="codex-gpt-mailbox-agent",
            role="mailbox-agent",
            workdir=str(worktrees["codex-gpt"]),
            command=[
                "poetry",
                "run",
                "python",
                str(script_root / "workflow_agent.py"),
                "--repo-root",
                str(repo_root),
                "--agent",
                "codex-gpt",
                "--interval",
                str(interval),
            ],
            log_path=str(_log_path(repo_root, "codex-gpt-mailbox-agent")),
        ),
    ]


def _write_manifest(repo_root: Path, spec: WorkerSpec, process: subprocess.Popen[bytes]) -> dict[str, object]:
    payload = {
        "name": spec.name,
        "role": spec.role,
        "pid": process.pid,
        "started_at": _utc_now(),
        "workdir": spec.workdir,
        "command": spec.command,
        "log_path": spec.log_path,
    }
    _pid_path(repo_root, spec.name).write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return payload


def _load_manifest(repo_root: Path, name: str) -> dict[str, object] | None:
    path = _pid_path(repo_root, name)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=True,
                encoding=_windows_text_encoding(),
                errors="replace",
            )
            output = (result.stdout or "").strip()
            return bool(output) and "No tasks are running" not in output
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except subprocess.CalledProcessError:
        return False


def _manifest_status(manifest: dict[str, object]) -> dict[str, object]:
    pid = int(manifest["pid"])
    return {
        **manifest,
        "running": _is_pid_running(pid),
        "checked_at": _utc_now(),
    }


def _open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("ab")


def _prime_runtime(repo_root: Path, interval: int) -> None:
    script_path = repo_root / "scripts" / "workflow_orchestrator.py"
    result = subprocess.run(
        [
            "poetry",
            "run",
            "python",
            str(script_path),
            "--repo-root",
            str(repo_root),
            "--interval",
            str(interval),
            "--once",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        details = stderr or stdout or "workflow_orchestrator.py --once returned non-zero"
        raise RuntimeError(f"failed to prime workflow runtime: {details}")

    missing_mailboxes = [
        agent
        for agent in workflow_mailbox.VALID_AGENT_NAMES
        if not (_runtime_dir(repo_root) / "mailboxes" / f"{agent}.json").exists()
    ]
    if missing_mailboxes:
        raise RuntimeError(
            "failed to prime workflow runtime: missing mailboxes for "
            + ", ".join(sorted(missing_mailboxes))
        )


def start_worker(repo_root: Path, spec: WorkerSpec) -> dict[str, object]:
    existing = _load_manifest(repo_root, spec.name)
    if existing and _is_pid_running(int(existing["pid"])):
        return _manifest_status(existing)

    stdout_handle = _open_log(Path(spec.log_path))
    process = subprocess.Popen(
        spec.command,
        cwd=spec.workdir,
        stdout=stdout_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=DETACHED_FLAGS,
    )
    stdout_handle.close()
    return _write_manifest(repo_root, spec, process)


def stop_worker(repo_root: Path, name: str) -> dict[str, object]:
    manifest = _load_manifest(repo_root, name)
    if not manifest:
        return {"name": name, "stopped": False, "reason": "not_found"}

    pid = int(manifest["pid"])
    running = _is_pid_running(pid)
    if running:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                encoding=_windows_text_encoding(),
                errors="replace",
            )
        else:
            os.kill(pid, signal.SIGTERM)

    _pid_path(repo_root, name).unlink(missing_ok=True)
    return {"name": name, "stopped": running, "pid": pid, "stopped_at": _utc_now()}


def _stalled_objects(repo_root: Path, stale_seconds: int) -> list[dict[str, object]]:
    return workflow_objects.find_stale_objects(repo_root, stale_seconds=stale_seconds)


def start_all(repo_root: Path, interval: int) -> dict[str, object]:
    ensure_supervisor_dirs(repo_root)
    specs = default_worker_specs(repo_root, interval)
    orchestrator_spec = next(spec for spec in specs if spec.name == "orchestrator")
    start_worker(repo_root, orchestrator_spec)
    try:
        _prime_runtime(repo_root, interval)
    except Exception:
        stop_worker(repo_root, orchestrator_spec.name)
        raise
    for spec in specs:
        if spec.name == "orchestrator":
            continue
        start_worker(repo_root, spec)
    return status_all(repo_root, interval)


def stop_all(repo_root: Path) -> dict[str, object]:
    ensure_supervisor_dirs(repo_root)
    for spec in default_worker_specs(repo_root, 15):
        stop_worker(repo_root, spec.name)
    return status_all(repo_root, 15)


def status_all(repo_root: Path, interval: int) -> dict[str, object]:
    ensure_supervisor_dirs(repo_root)
    statuses: list[dict[str, object]] = []
    for spec in default_worker_specs(repo_root, interval):
        manifest = _load_manifest(repo_root, spec.name)
        if manifest:
            statuses.append(_manifest_status(manifest))
        else:
            statuses.append(
                {
                    "name": spec.name,
                    "role": spec.role,
                    "workdir": spec.workdir,
                    "command": spec.command,
                    "log_path": spec.log_path,
                    "running": False,
                    "checked_at": _utc_now(),
                }
            )
    return {
        "checked_at": _utc_now(),
        "workers": statuses,
        "stalled_objects": _stalled_objects(repo_root, stale_seconds=max(interval * 2, 30)),
    }


def reconcile_workers(repo_root: Path, interval: int, ensure_running: bool) -> dict[str, object]:
    statuses = status_all(repo_root, interval)
    if not ensure_running:
        return statuses

    status_by_name = {entry["name"]: entry for entry in statuses["workers"]}
    restarted = False
    for spec in default_worker_specs(repo_root, interval):
        entry = status_by_name[spec.name]
        if not entry["running"]:
            start_worker(repo_root, spec)
            restarted = True

    return status_all(repo_root, interval) if restarted else statuses


def _render_status(payload: dict[str, object]) -> str:
    lines = ["# Workflow Supervisor", ""]
    for entry in payload["workers"]:
        state = "running" if entry["running"] else "stopped"
        lines.append(f"- {entry['name']}: {state}")
        lines.append(f"  role={entry['role']} workdir={entry['workdir']}")
        lines.append(f"  log={entry['log_path']}")
        if entry.get("pid"):
            lines.append(f"  pid={entry['pid']}")
    stalled_objects = payload.get("stalled_objects", [])
    lines.append("")
    lines.append(f"stalled_objects={len(stalled_objects)}")
    for item in stalled_objects[:5]:
        lines.append(f"  {item['object_type']}:{item['id']} {item['reason']}")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start, stop, or inspect workflow automation processes.")
    parser.add_argument("--repo-root", default=".", help="Path to the main repository root.")
    parser.add_argument("--interval", type=int, default=15, help="Polling interval in seconds.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start", help="Launch orchestrator and all agent consumers.")
    start.add_argument("--json", action="store_true", help="Emit JSON output.")
    stop = subparsers.add_parser("stop", help="Stop orchestrator and all agent consumers.")
    stop.add_argument("--json", action="store_true", help="Emit JSON output.")
    status = subparsers.add_parser("status", help="Show supervisor status for all automation processes.")
    status.add_argument("--json", action="store_true", help="Emit JSON output.")
    watch = subparsers.add_parser("watch", help="Continuously monitor workers and optionally restart missing ones.")
    watch.add_argument("--json", action="store_true", help="Emit JSON output.")
    watch.add_argument(
        "--ensure-running",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Restart stopped workers while watching.",
    )
    watch.add_argument("--cycles", type=int, default=0, help="Stop after N watch cycles; 0 means run forever.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root).resolve()

    if args.command == "start":
        payload = start_all(repo_root, args.interval)
    elif args.command == "stop":
        payload = stop_all(repo_root)
    elif args.command == "status":
        payload = status_all(repo_root, args.interval)
    elif args.command == "watch":
        cycle = 0
        while True:
            payload = reconcile_workers(repo_root, args.interval, args.ensure_running)
            if args.json:
                print(json.dumps(payload, ensure_ascii=True, indent=2))
            else:
                print(_render_status(payload))
            cycle += 1
            if args.cycles and cycle >= args.cycles:
                return 0
            time.sleep(max(5, args.interval))
    else:
        raise AssertionError(f"unsupported command: {args.command}")

    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        print(_render_status(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
