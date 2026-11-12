#!/usr/bin/env python3
"""Medians over the rounds of a session, per test and variant: req/s, p50 and p99 for capacity, and the container's
memory (peak of the samples over the window, then the median over rounds) for ram.

usage: python3 bench/summary.py results/session3-i7   (writes results/session3-i7/summary.json)
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

FIELDS = {
    "capacity": ("rps", "p50_ms", "p99_ms", "peak_mem_mb"),
    "ram": ("rps", "p50_ms", "p99_ms", "peak_mem_mb"),
}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    session = Path(sys.argv[1])
    grouped = defaultdict(list)
    for path in sorted(session.glob("*.json")):
        if path.name in ("env.json", "summary.json"):
            continue
        cell = json.loads(path.read_text())
        if cell["non_200"]:
            raise SystemExit(f"{path}: non-200 responses {cell['non_200']}")
        grouped[cell["kind"], cell["variant"]].append(cell)
    summary = defaultdict(dict)
    for (kind, variant), cells in sorted(grouped.items()):
        row = {"rounds": len(cells)}
        for field in FIELDS[kind]:
            row[f"{field}_median"] = round(
                statistics.median(c[field] for c in cells), 1
            )
        row["rps_rounds"] = [
            round(c["rps"], 1) for c in sorted(cells, key=lambda c: c["round"])
        ]
        summary[kind][variant] = row
    (session / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
