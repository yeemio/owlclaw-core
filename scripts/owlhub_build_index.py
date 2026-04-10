"""Build OwlHub `index.json` from local repository paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from owlclaw.owlhub.indexer import IndexBuilder
from owlclaw.owlhub.statistics import StatisticsTracker


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OwlHub index.json")
    parser.add_argument("--output", required=True, help="Output index.json path")
    parser.add_argument(
        "--repos",
        nargs="+",
        required=True,
        help="Repository paths to crawl (space separated)",
    )
    parser.add_argument("--github-token", default="", help="GitHub token for release statistics API")
    args = parser.parse_args()

    stats = StatisticsTracker(github_token=args.github_token or None)
    builder = IndexBuilder(statistics_tracker=stats)
    index = builder.build_index(args.repos)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {output} with {index['total_skills']} skills")


if __name__ == "__main__":
    main()
