# -*- coding: utf-8 -*-
"""Checker v2 on the frame-extension corpus (ranks 61-120).

Loads sandbox/output/summary_ext61_120.json, parses each ok server's
{server_id}.jsonl transcript (same schema as the frozen v7 corpus), runs
f4_sample_v2.annotate_v2 on every tool description, and writes
sandbox/output/f4_ext_v2.csv with the frozen f4_all_v2.csv column layout
(B1..B6 left empty: that layer is not part of the audit corpus).
"""
import csv, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from f4_sample_v2 import annotate_v2

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"

summaries = ["summary_ext61_120.json", "summary_ext121_180.json"]
ok = []
for s in summaries:
    p = OUT / s
    if p.exists():
        summ = json.loads(p.read_text(encoding="utf-8"))
        ok += [r["server_id"] for r in summ["results"] if r.get("ok")]

items = []
for sid in sorted(ok):
    p = OUT / f"{sid}.jsonl"
    if not p.exists():
        continue
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        if o.get("tool_name"):
            items.append({"server": sid, "tool": o["tool_name"],
                          "description": (o.get("description") or "").replace("\n", " ").strip()})

names = {}
for r in items:
    names.setdefault(r["server"], set()).add(r["tool"])

rows_out = []
for r in items:
    sib = names[r["server"]] - {r["tool"]}
    cats, sev = annotate_v2(r["description"], sib)
    rows_out.append({
        "id": f"{r['server']}|{r['tool']}", "server": r["server"], "tool": r["tool"],
        "description": r["description"],
        **{f"A{i+1}": cats[i] for i in range(6)}, "A_sev": sev,
        **{f"B{i}": "" for i in range(1, 7)}, "B_sev": "",
    })

dest = OUT / "f4_ext_v2.csv"
cols = ["id", "server", "tool", "description",
        "A1", "A2", "A3", "A4", "A5", "A6", "A_sev",
        "B1", "B2", "B3", "B4", "B5", "B6", "B_sev"]
with dest.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    w.writerows(rows_out)

cnt = {f"C{i}": sum(int(r[f"A{i}"]) for r in rows_out) for i in range(1, 7)}
print(json.dumps({"servers_ok": len(ok), "tools": len(rows_out),
                  "per_category": cnt,
                  "union": sum(1 for r in rows_out if any(str(r[f"A{i}"]) == "1" for i in range(1, 7))),
                  "out": str(dest)}, ensure_ascii=False, indent=2))
