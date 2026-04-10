#!/usr/bin/env python3
"""Compare top-level response fields between Java and curl samples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare response fields")
    parser.add_argument("--java-json", required=True)
    parser.add_argument("--curl-json", required=True)
    return parser.parse_args()


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    args = _parse_args()
    java_data = _load(Path(args.java_json))
    curl_data = _load(Path(args.curl_json))

    java_keys = set(java_data.keys())
    curl_keys = set(curl_data.keys())
    only_java = sorted(java_keys - curl_keys)
    only_curl = sorted(curl_keys - java_keys)

    print("[cross-lang-compare] java_keys:", sorted(java_keys))
    print("[cross-lang-compare] curl_keys:", sorted(curl_keys))
    if not only_java and not only_curl:
        print("[cross-lang-compare] PASS: field sets are aligned.")
        return 0

    print("[cross-lang-compare] FAIL: field set mismatch.")
    if only_java:
        print("  only_java:", only_java)
    if only_curl:
        print("  only_curl:", only_curl)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
