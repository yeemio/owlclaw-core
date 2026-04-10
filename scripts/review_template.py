#!/usr/bin/env python3
"""Review SKILL.md templates: metadata completeness, params, docs quality."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from owlclaw.templates.skills import (  # noqa: E402
    TemplateRegistry,
    TemplateValidator,
    get_default_templates_dir,
)
from owlclaw.templates.skills.models import TemplateMetadata  # noqa: E402


def _review_template(meta: TemplateMetadata, validator: TemplateValidator) -> list[str]:
    """Review a single template. Returns list of findings (empty = OK)."""
    findings: list[str] = []

    # Metadata completeness
    if not meta.parameters:
        findings.append("[warning] No parameters defined")
    for p in meta.parameters:
        if not p.description:
            findings.append(f"[warning] Parameter {p.name!r} has no description")
        if p.type not in ("str", "int", "bool", "list"):
            findings.append(f"[info] Parameter {p.name!r} uses non-standard type: {p.type!r}")

    # Template file validation
    errors = validator.validate_template(meta.file_path)
    for e in errors:
        findings.append(f"[{e.severity}] {e.field}: {e.message}")

    return findings


def review_template(template_id: str) -> list[str]:
    """Review a single template by ID."""
    registry = TemplateRegistry(get_default_templates_dir())
    meta = registry.get_template(template_id)
    if not meta:
        return [f"Template not found: {template_id}"]
    validator = TemplateValidator()
    return _review_template(meta, validator)


def review_all_templates() -> dict[str, list[str]]:
    """Review all templates. Returns {template_id: [findings]}."""
    registry = TemplateRegistry(get_default_templates_dir())
    validator = TemplateValidator()
    results: dict[str, list[str]] = {}
    for meta in registry.list_templates():
        results[meta.id] = _review_template(meta, validator)
    return results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Review SKILL.md templates")
    parser.add_argument(
        "template",
        nargs="?",
        help="Template ID (e.g. monitoring/health-check). Omit to review all.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON report",
    )
    args = parser.parse_args()

    if args.template:
        findings = review_template(args.template)
        if args.json:
            import json
            print(json.dumps({args.template: findings}, indent=2))
        else:
            for f in findings:
                print(f)
            sys.exit(0 if not findings or all("[info]" in x for x in findings) else 1)
    else:
        results = review_all_templates()
        if args.json:
            import json
            print(json.dumps(results, indent=2))
        else:
            has_errors = False
            for tid, findings in sorted(results.items()):
                status = "OK" if not findings else "REVIEW"
                if any("[error]" in f for f in findings):
                    has_errors = True
                print(f"\n{tid}: {status}")
                for f in findings:
                    print(f"  {f}")
            print(f"\nReviewed {len(results)} templates")
            sys.exit(0 if not has_errors else 1)


if __name__ == "__main__":
    main()
