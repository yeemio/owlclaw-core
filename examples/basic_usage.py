"""
Basic usage of OwlClaw: mount Skills and register handlers/state.

Run from repo root:
  poetry run python examples/basic_usage.py

Or with PYTHONPATH:
  cd examples && python basic_usage.py  # ensure capabilities path is correct
"""

from pathlib import Path

from owlclaw import OwlClaw


def main() -> None:
    # 1. Create app and mount Skills from the examples/capabilities directory.
    #    OwlClaw will scan for SKILL.md files (Agent Skills spec) and load their metadata.
    app = OwlClaw("basic-usage")
    capabilities_path = Path(__file__).parent / "capabilities"
    app.mount_skills(str(capabilities_path))

    # 2. Register a handler for the "entry-monitor" Skill.
    #    The Skill name must match the "name" field in SKILL.md frontmatter.
    #    When the Agent decides to call this capability (e.g. via function calling),
    #    this async function will be invoked with the given session context.
    @app.handler("entry-monitor")
    async def check_entry(session: dict) -> dict:
        return {
            "checked": True,
            "signals": [],
            "message": "Entry check completed (demo).",
        }

    # 3. Register another handler for "morning-decision".
    @app.handler("morning-decision")
    async def morning_plan(session: dict) -> dict:
        return {
            "plan": "Review overnight data, then run entry-monitor if trading hours.",
            "priority": 1,
        }

    # 4. Register a state provider. The Agent can query this via the built-in
    #    query_state tool to get current business state (e.g. market snapshot).
    @app.state("market_state")
    def get_market_state() -> dict:
        return {
            "market_open": True,
            "positions_count": 0,
            "demo": True,
        }

    # 5. Inspect what was loaded (optional — for demo output).
    skills = app.skills_loader.list_skills()
    print(f"Loaded {len(skills)} Skills: {[s.name for s in skills]}")
    caps = app.registry.list_capabilities()
    for c in caps:
        print(f"  - {c['name']}: {c['description']}")

    # 6. In a real app you would call app.run() to start the Agent runtime
    #    (Hatchet worker, heartbeat, etc.). Here we only demonstrate registration.
    # app.configure(soul="docs/SOUL.md", heartbeat_interval_minutes=30)
    # app.run()


if __name__ == "__main__":
    main()
