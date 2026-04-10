"""Example 3: render a SKILL.md by Template API."""

from pathlib import Path

from owlclaw.templates.skills import TemplateRegistry, TemplateRenderer, get_default_templates_dir


def main() -> None:
    registry = TemplateRegistry(get_default_templates_dir())
    renderer = TemplateRenderer(registry)
    content = renderer.render(
        "workflow/task-scheduler",
        {
            "skill_name": "daily-task-scheduler",
            "skill_description": "Schedule recurring operations",
        },
    )
    out = Path("capabilities/daily-task-scheduler/SKILL.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    print(f"Generated {out}")


if __name__ == "__main__":
    main()
