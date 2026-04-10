"""APScheduler -> Hatchet migration helpers for mionyee-style tasks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


Complexity = Literal["simple_cron", "stateful_cron", "chained"]


@dataclass(frozen=True)
class APSchedulerJob:
    """Normalized APScheduler-like job definition."""

    name: str
    cron: str
    func_ref: str
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    stateful: bool = False


def classify_job_complexity(job: APSchedulerJob) -> Complexity:
    """Classify migration complexity by state and dependency shape."""
    if job.depends_on:
        return "chained"
    if job.stateful:
        return "stateful_cron"
    return "simple_cron"


def select_canary_batch(jobs: list[APSchedulerJob], *, max_jobs: int = 5) -> list[APSchedulerJob]:
    """Pick the first low-risk simple cron jobs for gray migration."""
    simple_jobs = [job for job in jobs if classify_job_complexity(job) == "simple_cron"]
    return sorted(simple_jobs, key=lambda item: item.name)[:max(0, max_jobs)]


def split_jobs_by_complexity(jobs: list[APSchedulerJob]) -> dict[str, list[APSchedulerJob]]:
    """Split jobs by migration complexity buckets."""
    buckets: dict[str, list[APSchedulerJob]] = {
        "simple_cron": [],
        "stateful_cron": [],
        "chained": [],
    }
    for job in jobs:
        buckets[classify_job_complexity(job)].append(job)
    return buckets


def _to_pascal_case(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", value).strip()
    parts = [part for part in normalized.split(" ") if part]
    if not parts:
        base = "Generated"
    else:
        base = "".join(part.capitalize() for part in parts)
    if base[0].isdigit():
        base = f"Workflow{base}"
    return f"{base}Workflow"


def _to_kebab_case(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return cleaned or "generated-job"


def render_hatchet_workflow(job: APSchedulerJob) -> str:
    """Render one standalone Hatchet workflow skeleton from one APScheduler job."""
    class_name = _to_pascal_case(job.name)
    task_name = _to_kebab_case(job.name)
    return (
        "from __future__ import annotations\n\n"
        "from hatchet_sdk import Hatchet\n\n"
        "hatchet = Hatchet()\n\n"
        f"@hatchet.task(name=\"{task_name}\", on_crons=[\"{job.cron}\"])\n"
        f"async def {class_name}_run(input_data, ctx):\n"
        f"    \"\"\"Generated from APScheduler job: {job.func_ref}\"\"\"\n"
        "    return {\n"
        "        \"status\": \"ok\",\n"
        f"        \"source\": \"{job.func_ref}\",\n"
        "        \"input\": input_data,\n"
        "    }\n"
    )


def render_hatchet_module(jobs: list[APSchedulerJob]) -> str:
    """Render a Python module containing multiple generated Hatchet tasks."""
    if not jobs:
        return (
            "from __future__ import annotations\n\n"
            "from hatchet_sdk import Hatchet\n\n"
            "hatchet = Hatchet()\n"
        )
    blocks: list[str] = [
        "from __future__ import annotations",
        "",
        "from hatchet_sdk import Hatchet",
        "",
        "hatchet = Hatchet()",
        "",
    ]
    used_function_names: dict[str, int] = {}
    used_task_names: dict[str, int] = {}

    def _unique_function_name(base_name: str) -> str:
        counter = used_function_names.get(base_name, 0)
        used_function_names[base_name] = counter + 1
        if counter == 0:
            return f"{base_name}_run"
        return f"{base_name}_run_{counter + 1}"

    def _unique_task_name(base_name: str) -> str:
        counter = used_task_names.get(base_name, 0)
        used_task_names[base_name] = counter + 1
        if counter == 0:
            return base_name
        return f"{base_name}-{counter + 1}"

    for job in jobs:
        class_name = _to_pascal_case(job.name)
        function_name = _unique_function_name(class_name)
        task_name = _unique_task_name(_to_kebab_case(job.name))
        blocks.extend(
            [
                f"@hatchet.task(name=\"{task_name}\", on_crons=[\"{job.cron}\"])",
                f"async def {function_name}(input_data, ctx):",
                f"    \"\"\"Generated from APScheduler job: {job.func_ref}\"\"\"",
                "    return {",
                "        \"status\": \"ok\",",
                f"        \"source\": \"{job.func_ref}\",",
                "        \"input\": input_data,",
                "    }",
                "",
            ]
        )
    return "\n".join(blocks).rstrip() + "\n"


def load_jobs_from_mionyee_scenarios(path: str | Path) -> list[APSchedulerJob]:
    """Load equivalent APScheduler jobs from mionyee e2e scenario source."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    jobs: list[APSchedulerJob] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        scenario_id = str(item.get("scenario_id", "")).strip()
        input_data = item.get("input_data", {})
        if not name or not scenario_id or not isinstance(input_data, dict):
            continue
        action = str(input_data.get("action", "entry_check")).strip().lower()
        cron_map = {
            "entry_check": "30 9 * * 1-5",
            "risk_review": "0 12 * * 1-5",
            "position_adjust": "30 14 * * 1-5",
        }
        func_map = {
            "entry_check": "mionyee.scheduler.entry_monitor",
            "risk_review": "mionyee.scheduler.risk_review",
            "position_adjust": "mionyee.scheduler.position_adjust",
        }
        jobs.append(
            APSchedulerJob(
                name=name,
                cron=cron_map.get(action, "0 9 * * 1-5"),
                func_ref=func_map.get(action, "mionyee.scheduler.unknown"),
                kwargs={"scenario_id": scenario_id, **input_data},
                depends_on=["mionyee task 1"] if action == "position_adjust" else [],
                stateful=action == "risk_review",
            )
        )
    return jobs


def write_generated_hatchet_module(jobs: list[APSchedulerJob], output_path: str | Path) -> Path:
    """Write generated Hatchet module to disk."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_hatchet_module(jobs), encoding="utf-8")
    return target


def write_complexity_modules(jobs: list[APSchedulerJob], output_dir: str | Path) -> dict[str, Path]:
    """Write one generated module per complexity bucket."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    buckets = split_jobs_by_complexity(jobs)
    outputs: dict[str, Path] = {}
    for name, bucket_jobs in buckets.items():
        path = out_dir / f"generated_hatchet_tasks_{name}.py"
        path.write_text(render_hatchet_module(bucket_jobs), encoding="utf-8")
        outputs[name] = path
    return outputs


def simulate_apscheduler_execution(job: APSchedulerJob) -> dict[str, Any]:
    """Simulate APScheduler execution output for deterministic migration replay."""
    return {
        "status": "ok",
        "job_name": job.name,
        "source": job.func_ref,
        "scenario_id": job.kwargs.get("scenario_id", ""),
        "symbol": job.kwargs.get("symbol", ""),
        "action": job.kwargs.get("action", ""),
    }


def simulate_hatchet_execution(job: APSchedulerJob) -> dict[str, Any]:
    """Simulate Hatchet execution output for deterministic migration replay."""
    return {
        "status": "ok",
        "job_name": job.name,
        "source": job.func_ref,
        "workflow_name": _to_kebab_case(job.name),
        "scenario_id": job.kwargs.get("scenario_id", ""),
        "symbol": job.kwargs.get("symbol", ""),
        "action": job.kwargs.get("action", ""),
    }


def dual_run_replay_compare(jobs: list[APSchedulerJob]) -> dict[str, Any]:
    """Compare APScheduler and Hatchet simulated outputs for migration replay."""
    compared = 0
    matched = 0
    mismatches: list[dict[str, Any]] = []

    for job in jobs:
        aps = simulate_apscheduler_execution(job)
        hatchet = simulate_hatchet_execution(job)
        compared += 1
        fields = ("status", "job_name", "source", "scenario_id", "symbol", "action")
        if all(aps.get(field) == hatchet.get(field) for field in fields):
            matched += 1
            continue
        mismatches.append({"job_name": job.name, "apscheduler": aps, "hatchet": hatchet})

    return {
        "compared": compared,
        "matched": matched,
        "mismatches": mismatches,
        "match_rate": (matched / compared) if compared else 0.0,
    }
