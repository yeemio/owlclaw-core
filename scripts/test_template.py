#!/usr/bin/env python3
"""Test SKILL.md templates: render with default params and validate output."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from owlclaw.templates.skills import (  # noqa: E402
    TemplateRegistry,
    TemplateRenderer,
    TemplateValidator,
    get_default_templates_dir,
)


def _make_default_params(meta: object) -> dict:
    """Build default params for a template."""
    from owlclaw.templates.skills.models import TemplateMetadata

    m = meta
    if not isinstance(m, TemplateMetadata):
        return {}
    params: dict = {}
    for p in m.parameters:
        if p.name in ("skill_name", "skill_description"):
            params[p.name] = f"test-{m.id.replace('/', '-')}"
        elif p.default is not None:
            params[p.name] = p.default
        elif p.type == "list":
            params[p.name] = []
        elif p.type == "bool":
            params[p.name] = False
        elif p.type == "int":
            params[p.name] = 0
        else:
            params[p.name] = "test-value"
    return params


def test_template(template_id: str) -> bool:
    """Test a single template: render and validate output. Returns True if pass."""
    registry = TemplateRegistry(get_default_templates_dir())
    meta = registry.get_template(template_id)
    if not meta:
        print(f"Template not found: {template_id}")
        return False

    renderer = TemplateRenderer(registry)
    validator = TemplateValidator()

    params = _make_default_params(meta)
    try:
        content = renderer.render(template_id, params)
    except Exception as e:
        print(f"Render failed: {e}")
        return False

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(content)
        tmp_path = Path(f.name)
    try:
        errors = validator.validate_skill_file(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    if errors:
        print("Validation failed:")
        for e in errors:
            print(f"  - [{e.severity}] {e.field}: {e.message}")
        return False
    print(f"OK: {template_id}")
    return True


def test_all_templates() -> tuple[int, int]:
    """Test all templates. Returns (passed, total)."""
    registry = TemplateRegistry(get_default_templates_dir())
    templates = registry.list_templates()
    passed = 0
    for meta in templates:
        if test_template(meta.id):
            passed += 1
    return passed, len(templates)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Test SKILL.md templates")
    parser.add_argument(
        "template",
        nargs="?",
        help="Template ID (e.g. monitoring/health-check). Omit to test all.",
    )
    args = parser.parse_args()

    if args.template:
        ok = test_template(args.template)
        sys.exit(0 if ok else 1)
    else:
        passed, total = test_all_templates()
        print(f"\n{passed}/{total} templates passed")
        sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
