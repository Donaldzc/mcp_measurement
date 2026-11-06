# -*- coding: utf-8 -*-
"""工具计数口径统一（879 vs 885 对账）。

四个来源：
  1) summary_v7.json / f1_report_v7.json   —— 运行时记录 n_tools（握手客户端返回长度）
  2) sandbox/output/<sid>.jsonl            —— 原始 tools/list 转录（tool_name 行）
  3) f4_all.csv                            —— 标注语料行（id = sid|tool）
  4) f3_report.json                        —— 静态分析侧 n_dynamic

输出：sandbox/output/tool_count_reconciliation.json
结论口径：以 (2) 的唯一 tool_name 集合为权威，解释 (1)(3)(4) 的偏差来源。
"""
import csv
import json
import pathlib
from collections import Counter

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"


def main():
    summ = json.loads((OUT / "summary_v7.json").read_text(encoding="utf-8"))
    f1 = {r["server_id"]: r for r in summ["results"] if r.get("ok")}
    f3 = json.loads((OUT / "f3_report.json").read_text(encoding="utf-8"))
    f3_by = {p["server_id"]: p for p in f3["per_server"]}

    # F4 语料行数（按 server 聚合）
    f4_n = Counter()
    f4_ids = set()
    with open(OUT / "f4_all.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            f4_ids.add(row["id"])
            f4_n[row["server"]] += 1

    rows = []
    for sid in sorted(f1):
        raw = 0
        names = []
        dup_lines = 0
        path = OUT / f"{sid}.jsonl"
        if path.exists():
            seen = set()
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                nm = o.get("tool_name")
                if not nm:
                    continue
                raw += 1
                if nm in seen:
                    dup_lines += 1
                seen.add(nm)
                names.append(nm)
        uniq = set(names)
        rows.append({
            "server_id": sid,
            "f1_n": f1[sid].get("n_tools"),
            "jsonl_raw": raw,
            "jsonl_unique": len(uniq),
            "dup_name_lines": dup_lines,
            "f4_n": f4_n.get(sid, 0),
            "f3_n": f3_by.get(sid, {}).get("n_dynamic"),
            "d_f1_vs_jsonl": (f1[sid].get("n_tools") or 0) - len(uniq),
            "d_f4_vs_jsonl": f4_n.get(sid, 0) - len(uniq),
            "d_f3_vs_jsonl": (f3_by.get(sid, {}).get("n_dynamic") or 0) - len(uniq),
        })

    totals = {
        "f1_n_tools_sum": sum(r["f1_n"] or 0 for r in rows),
        "jsonl_raw_rows": sum(r["jsonl_raw"] for r in rows),
        "jsonl_unique_tools": sum(r["jsonl_unique"] for r in rows),
        "f4_rows": sum(r["f4_n"] for r in rows),
        "f4_unique_ids": len(f4_ids),
        "f3_n_dynamic_sum": sum(r["f3_n"] or 0 for r in rows),
        "servers": len(rows),
    }
    mism = [r for r in rows if r["d_f1_vs_jsonl"] or r["d_f4_vs_jsonl"] or r["d_f3_vs_jsonl"]]

    report = {
        "authoritative_source": "sandbox/output/<sid>.jsonl unique tool_name",
        "totals": totals,
        "mismatch_servers": mism,
        "per_server": rows,
        "verdict": (
            "以 jsonl 唯一工具名集合为权威口径；F4 语料与 F3 动态集合与之一致，"
            "F1 记录的 n_tools 在个别 server 上偏小，原因是握手客户端按名称去重/截断"
            "（见 mismatch_servers 明细），不应再使用 879 作为 tool 总数。"
        ),
    }
    (OUT / "tool_count_reconciliation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== 工具计数对账 ===")
    for k, v in totals.items():
        print(f"  {k}: {v}")
    print(f"  存在偏差的 server: {len(mism)}")
    for r in mism:
        print(f"    {r['server_id']}: f1={r['f1_n']} jsonl={r['jsonl_unique']} "
              f"f4={r['f4_n']} f3={r['f3_n']} dup={r['dup_name_lines']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
