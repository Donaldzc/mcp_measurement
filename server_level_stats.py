# -*- coding: utf-8 -*-
"""F4 server-level 统计 + F3 static-only over-approximation。

输出 sandbox/output/server_level_stats.json：
  - 每个注入类别的 server 级计数/比率（分母 = 32 个可部署 server）
  - 每 server 的 category 覆盖与 tool 数
  - 静态侧 over-approximation：目标包注册调用集合中未出现在动态工具列表的比例
"""
import csv
import json
import pathlib
from collections import Counter, defaultdict

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
CATS = ["C1", "C2", "C3", "C4", "C5", "C6"]
# f4_all.csv 的列名是 A1..A6（人工/规则标注），对应 codebook 的 C1..C6
COL = {c: "A" + c[1:] for c in CATS}
CAT_NAMES = {
    "C1": "direct command",
    "C2": "cross-tool reference",
    "C3": "embedded URL/code",
    "C4": "credential request",
    "C5": "sensitive path",
    "C6": "hidden instruction",
}


def main():
    f3 = json.loads((OUT / "f3_report.json").read_text(encoding="utf-8"))
    per = f3["per_server"]
    ok_servers = [p["server_id"] for p in per]

    # ---- 1) server-level 类别覆盖 ----
    tools_by_server = Counter()
    cat_tools = defaultdict(Counter)      # cat -> server -> count
    cat_servers = Counter()               # cat -> number of servers
    with open(OUT / "f4_all.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sid = row["server"]
            tools_by_server[sid] += 1
            for c in CATS:
                if row.get(COL[c]) not in ("", "0", None):
                    cat_tools[c][sid] += 1

    n_servers = len(ok_servers)
    cat_server_rows = []
    for c in CATS:
        hit = len(cat_tools[c])
        cat_server_rows.append({
            "cat": c, "name": CAT_NAMES[c],
            "servers": hit,
            "server_rate": round(hit / n_servers, 4),
            "tools": sum(cat_tools[c].values()),
        })

    server_rows = []
    for sid in sorted(ok_servers):
        pos_cats = [c for c in CATS if cat_tools[c].get(sid)]
        server_rows.append({
            "server_id": sid,
            "tools": tools_by_server.get(sid, 0),
            "cats": {c: cat_tools[c].get(sid, 0) for c in CATS},
            "n_pos_cats": len(pos_cats),
            "any_positive": bool(pos_cats),
        })

    # ---- 2) static-only over-approximation ----
    static_rows = []
    for p in per:
        n_tc = p.get("n_target_calls", 0)
        n_so = p.get("n_static_only", 0)
        static_rows.append({
            "server_id": p["server_id"],
            "style": p["registration_style"],
            "n_target_calls": n_tc,
            "n_matched_target": p.get("target_matched", 0),
            "n_static_only": n_so,
            "static_only_rate": round(n_so / n_tc, 4) if n_tc else None,
            "static_only_examples": p.get("static_only", [])[:6],
        })
    with_calls = [r for r in static_rows if r["n_target_calls"]]
    sum_calls = sum(r["n_target_calls"] for r in with_calls)
    sum_only = sum(r["n_static_only"] for r in with_calls)
    sum_hit = sum(r["n_matched_target"] for r in with_calls)
    over = {
        "servers_with_registration_calls": len(with_calls),
        "servers_with_static_only": sum(1 for r in with_calls if r["n_static_only"]),
        "target_calls_total": sum_calls,
        "target_matched_total": sum_hit,
        "static_only_total": sum_only,
        "micro_precision_target": round(sum_hit / sum_calls, 4) if sum_calls else None,
        "micro_over_approximation_rate": round(sum_only / sum_calls, 4) if sum_calls else None,
        "note": ("static_only 来自目标包注册调用的字符串字面量，未出现在动态 tools/list；"
                 "其中一部分可能是命名变换（前缀/后缀），也可能是条件注册或权限裁剪。"
                 "无注册调用的 server（含 runtime_generated）为 None，不参与聚合。"),
    }
    by_style = {}
    for r in with_calls:
        e = by_style.setdefault(r["style"], {"servers": 0, "calls": 0, "static_only": 0})
        e["servers"] += 1
        e["calls"] += r["n_target_calls"]
        e["static_only"] += r["n_static_only"]
    for e in by_style.values():
        e["rate"] = round(e["static_only"] / e["calls"], 4) if e["calls"] else None

    report = {
        "n_deployable_servers": n_servers,
        "n_tools": sum(tools_by_server.values()),
        "category_server_level": cat_server_rows,
        "any_positive_servers": sum(1 for r in server_rows if r["any_positive"]),
        "any_positive_server_rate": round(sum(1 for r in server_rows if r["any_positive"]) / n_servers, 4),
        "per_server": server_rows,
        "static_over_approximation": over,
        "static_over_by_style": by_style,
        "static_per_server": static_rows,
    }
    (OUT / "server_level_stats.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== server-level 类别 ===")
    for r in cat_server_rows:
        print(f"  {r['cat']} {r['name']:<24} servers={r['servers']:2d} "
              f"({r['server_rate']*100:5.1f}%)  tools={r['tools']}")
    print(f"  any positive: {report['any_positive_servers']}/{n_servers} "
          f"({report['any_positive_server_rate']*100:.1f}%)")
    print("\n=== static-only over-approximation ===")
    print(f"  servers with registration calls: {over['servers_with_registration_calls']}")
    print(f"  target calls={sum_calls} matched={sum_hit} static_only={sum_only}")
    print(f"  micro precision={over['micro_precision_target']}  over-approx={over['micro_over_approximation_rate']}")
    print(f"  servers with >=1 static_only: {over['servers_with_static_only']}")
    for sid, e in sorted(by_style.items(), key=lambda kv: -kv[1]["calls"]):
        print(f"    {sid:<22} servers={e['servers']:2d} calls={e['calls']:5d} "
              f"static_only={e['static_only']:4d} rate={e['rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
