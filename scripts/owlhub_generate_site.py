"""Generate OwlHub static site from index.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from owlclaw.owlhub.site import SiteGenerator


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate OwlHub static site")
    parser.add_argument("--index", required=True, help="Input index.json path")
    parser.add_argument("--output", required=True, help="Output site directory")
    parser.add_argument("--base-url", default="https://owlhub.local", help="Site base URL")
    parser.add_argument("--page-size", type=int, default=20, help="Skills per index page")
    args = parser.parse_args()

    index_path = Path(args.index).resolve()
    output_dir = Path(args.output).resolve()

    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.json").write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")

    generator = SiteGenerator()
    generator.generate(index_data=index_data, output_dir=output_dir, base_url=args.base_url, page_size=args.page_size)
    print(f"Generated OwlHub static site at {output_dir}")


if __name__ == "__main__":
    main()
