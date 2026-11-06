# -*- coding: utf-8 -*-
"""Analyse the `instructions` probe: length, and whether the frozen v2 checker
fires on it, per server; compare with the same server's tool-description flags."""
import collections
import csv
import json
import pathlib
import statistics
import sys

BASE = pathlib.Path(r"d:\code\trae\temp_20261021_02\mcp_measurement")
sys.path.insert(0, str(BASE))
from f4_sample_v2 import annotate_v2  # noqa: E402

OUT = BASE / "sandbox" / "output"
RUNS = OUT / "instr_runs"

rows = list(csv.DictReader((OUT / "f4_all_v2.csv").open(encoding="utf-8", newline="")))
tool_flags = collections.defaultdict(lambda: collections.Counter())
tool_count = collections.Counter()
for r in rows:
    tool_count[r["server"]] += 1
    for i, c in enumerate(["A1", "A2", "A3", "A4", "A5", "A6"]):
        if r[c] == "1":
            tool_flags[r["server"]][c] += 1

res = []
for f in sorted(RUNS.glob("*.instr.json")):
    d = json.loads(f.read_text(encoding="utf-8"))
    sid = f.name.replace(".jsonl.instr.json", "")
    instr = d.get("instructions") or ""
    cats, sev = annotate_v2(instr, []) if instr.strip() else ([0] * 6, 0)
    res.append({
        "server": sid,
        "has_field": bool(d.get("has_instructions")),
        "chars": len(instr),
        "tools": tool_count.get(sid, 0),
        "instr_cats": [f"C{i+1}" for i, v in enumerate(cats) if v],
        "tool_cats": sorted(tool_flags[sid]),
        "tool_flag_count": sum(tool_flags[sid].values()),
    })

with_field = [r for r in res if r["has_field"]]
nonempty = [r for r in res if r["chars"] > 0]
flagged = [r for r in nonempty if r["instr_cats"]]
only_instr = [r for r in flagged if not r["tool_cats"]]

summary = {
    "probed": len(res),
    "with_field": len(with_field),
    "nonempty": len(nonempty),
    "chars": {
        "median": statistics.median([r["chars"] for r in nonempty]) if nonempty else 0,
        "max": max([r["chars"] for r in nonempty], default=0),
        "max_server": max(nonempty, key=lambda r: r["chars"])["server"] if nonempty else None,
    },
    "flagged": len(flagged),
    "flagged_servers": [r["server"] for r in flagged],
    "flagged_categories": collections.Counter(c for r in flagged for c in r["instr_cats"]),
    "flagged_only_in_instructions": [r["server"] for r in only_instr],
    "rows": res,
}
(OUT / "analysis" / "instructions_probe.json").write_text(
    json.dumps(summary, indent=2, default=str), encoding="utf-8")
print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2, default=str))
for r in sorted(res, key=lambda x: -x["chars"])[:8]:
    print(r["server"], r["chars"], r["instr_cats"], "tools", r["tools"])
