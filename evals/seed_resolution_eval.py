"""Small regression harness for seed resolution.

This is intentionally lightweight: it verifies routing behavior and evidence,
not perfect ranking. Run via scripts/test_seed_resolver.sh.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agent_server.seed_resolver import resolve_seed


def _contains_any(resolution: dict, needles: list[str]) -> bool:
    text = json.dumps(resolution, ensure_ascii=False).lower()
    return any(n.lower() in text for n in needles)


async def _run(cases_path: Path) -> int:
    cases = json.loads(cases_path.read_text())
    failures = 0
    for case in cases:
        seed = case["seed"]
        print(f"\n=== {seed} ===")
        resolution = await resolve_seed(seed)
        print(json.dumps({
            "resolved_type": resolution["resolved_type"],
            "parts": [
                {
                    "seed": p["seed"],
                    "resolved_type": p["resolved_type"],
                    "top": [
                        c.get("title")
                        for c in (p.get("evidence") or {}).get("top_candidates", [])[:3]
                    ],
                }
                for p in resolution["parts"]
            ],
        }, indent=2))

        expected_type = case.get("expected_type")
        if expected_type and resolution["resolved_type"] != expected_type:
            print(f"FAIL: expected type {expected_type}, got {resolution['resolved_type']}")
            failures += 1

        expected_parts = case.get("expected_parts") or []
        actual_parts = [p["seed"] for p in resolution["parts"]]
        for p in expected_parts:
            if p not in actual_parts:
                print(f"FAIL: expected part {p!r}, got {actual_parts}")
                failures += 1

        must_match = case.get("must_match") or []
        if must_match and not _contains_any(resolution, must_match):
            print(f"FAIL: none of {must_match} found in evidence")
            failures += 1

    print(f"\nSeed-resolution eval: {len(cases) - failures}/{len(cases)} checks passed" if failures == 0 else f"\nSeed-resolution eval failures: {failures}")
    return 1 if failures else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="evals/seed_resolution_cases.json")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_run(Path(args.cases))))


if __name__ == "__main__":
    main()

