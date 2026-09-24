"""Recompute tests/fixtures/golden.json from the fixtures.

Run only after an INTENDED change to the parser, then review the diff of
golden.json - every changed line is a plan whose output changed:
    python tests/update_golden.py
"""
import json

from helpers import GOLDEN_PATH, load_plans, summarize

golden = {plan_id: summarize(cfg) for plan_id, cfg in sorted(load_plans().items())}
with open(GOLDEN_PATH, 'w', encoding='utf-8') as f:
    json.dump(golden, f, ensure_ascii=False, indent=1, sort_keys=True)
print(f"{len(golden)} plans written to {GOLDEN_PATH}")
