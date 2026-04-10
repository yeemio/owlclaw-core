"""Example: Render a SKILL.md from template using Python API."""

from pathlib import Path

from owlclaw.templates.skills import (
    TemplateRegistry,
    TemplateRenderer,
    TemplateValidator,
    get_default_templates_dir,
)


def main() -> None:
    registry = TemplateRegistry(get_default_templates_dir())
    renderer = TemplateRenderer(registry)
    validator = TemplateValidator()

    params = {
        "skill_name": "API Health Monitor",
        "skill_description": "Monitor API endpoint health",
        "endpoints": "/health,/ready",
    }
    content = renderer.render("monitoring/health-check", params)

    output_dir = Path(__file__).parent / "output" / "api-health-monitor"
    output_dir.mkdir(parents=True, exist_ok=True)
    skill_file = output_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")

    errs = validator.validate_skill_file(skill_file)
    if errs:
        for e in errs:
            print(f"[{e.severity}] {e.field}: {e.message}")
    else:
        print(f"Created and validated: {skill_file}")


if __name__ == "__main__":
    main()
