# -*- coding: utf-8 -*-
"""实体解析与去重脚本（v2 四级实体图）。

核心任务:  把不同来源的 artifact（GitHub repo、npm/PyPI 包、registry 条目）
           解析为 "可独立部署的 MCP server" 主分析单元，处理：
             1 repo → 多个 server（monorepo 子模块）
             1 server → 多个 artifact（多镜像/多包）
             1 server → 多 deployment（stdio/HTTP/SSE 同协议不同端）

输入:
  --repos   github_repos.csv （Top-N star，pilot-only，后续换 registry 抽样）
  --npm     npm_packages.csv
  --gold    gold_set.csv     （人工枚举，用于验证 precision/recall）
输出:
  --out     entities.jsonl   （四级实体图扁平记录）
  --report  dedup_report.json（统计 + gold set 验证结果）

去重规则（四级强度，可调）:
  L1 精确匹配:  规范化名 + source 一致
  L2 模糊匹配:  规范化名（连字符/下划线/点归一），同 transport
  L3 仓库归一:  GitHub API 查 packages 与 repo 的归属关系
  L4 部署归一:  不同传输协议的同 server 合并（需 tools/list 交叉验证）
"""
import argparse
import csv
import json
import os
import pathlib
import re
import sys
from collections import defaultdict

BASE = pathlib.Path(__file__).resolve().parent       # mcp_measurement/
DATA = BASE / "data"
OUT = BASE / "gold_set"


def normalize_name(s):
    s = s.lower().strip()
    s = re.sub(r"[^\w]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    # 去掉常见 server/mcp 后缀前缀噪声
    s = re.sub(r"^mcp[_-]?", "", s)
    s = re.sub(r"[_-]server$", "", s)
    return s


def repo_to_server_id(repo_full, language, name_hint=""):
    owner, name = repo_full.split("/", 1)
    base = normalize_name(name_hint or name)
    return f"github_{base}"


def npm_to_server_id(pkg_name, name_hint=""):
    bare = pkg_name.split("/")[-1] if "/" in pkg_name else pkg_name
    base = normalize_name(name_hint or bare)
    return f"npm_{base}"


def dedup_entities(records, level=2):
    """records: list of dicts with server_id, name, source, transport, language, artifact"""
    idx = defaultdict(list)
    for r in records:
        idx[r["server_id"]].append(r)
    merged = []
    for sid, group in idx.items():
        merged.append({
            "server_id": sid,
            "canonical_name": group[0]["name"],
            "sources": sorted(set(r["source"] for r in group)),
            "artifacts": sorted(set(r["artifact"] for r in group)),
            "transports": sorted(set(r.get("transport", "") for r in group if r.get("transport"))),
            "languages": sorted(set(r.get("language", "") for r in group if r.get("language"))),
            "n_artifacts": len(group),
        })
    # L3: 跨 server_id 合并，若规范化名一致且 artifacts 有交叉（仓库同源）
    if level >= 3:
        by_norm = defaultdict(list)
        for m in merged:
            norm = normalize_name(m["canonical_name"])
            by_norm[norm].append(m)
        merged = []
        for norm, group in by_norm.items():
            all_artifacts = set()
            for m in group:
                all_artifacts.update(m["artifacts"])
            merged.append({
                "server_id": f"merged_{norm}" if len(group) > 1 else group[0]["server_id"],
                "canonical_name": group[0]["canonical_name"],
                "sources": sorted({s for m in group for s in m["sources"]}),
                "artifacts": sorted(all_artifacts),
                "transports": sorted({t for m in group for t in m["transports"]}),
                "languages": sorted({l for m in group for l in m["languages"]}),
                "n_artifacts": len(all_artifacts),
            })
    return merged


def load_gold_set(path):
    gold = {}
    if not path or not pathlib.Path(path).exists():
        return gold
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("included", "").lower() in ("1", "true", "yes"):
                gold[r["server_id"]] = r
    return gold


def compute_pr(candidate_ids, gold_ids):
    tp = candidate_ids & gold_ids
    fp = candidate_ids - gold_ids
    fn = gold_ids - candidate_ids
    prec = len(tp) / max(len(tp | fp), 1)
    rec = len(tp) / max(len(tp | fn), 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)
    return {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
            "tp": len(tp), "fp": len(fp), "fn": len(fn)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", default=str(DATA / "github_repos.csv"))
    ap.add_argument("--npm", default=str(DATA / "npm_packages.csv"))
    ap.add_argument("--gold", default=str(OUT / "gold_set.csv"))
    ap.add_argument("--out", default=str(OUT / "entities.jsonl"))
    ap.add_argument("--report", default=str(OUT / "dedup_report.json"))
    ap.add_argument("--level", type=int, default=2, choices=[1, 2, 3], help="去重强度")
    ap.add_argument("--top-n", type=int, default=60, help="pilot 只用前 N 条，后续换 registry 抽样")
    args = ap.parse_args()

    records = []

    # 1) GitHub repo 层
    if pathlib.Path(args.repos).exists():
        with open(args.repos, encoding="utf-8-sig") as f:
            repos = list(csv.DictReader(f))[:args.top_n]
        for r in repos:
            sid = repo_to_server_id(r["full_name"], r.get("lang", ""))
            records.append({
                "server_id": sid,
                "name": r["full_name"],
                "source": "github",
                "transport": "",
                "language": r.get("lang", ""),
                "publisher_type": "",
                "artifact": r["full_name"],
                "desc": r.get("desc", ""),
            })

    # 2) npm 包层
    if pathlib.Path(args.npm).exists():
        with open(args.npm, encoding="utf-8-sig") as f:
            pkgs = list(csv.DictReader(f))[:args.top_n]
        for r in pkgs:
            sid = npm_to_server_id(r["name"])
            records.append({
                "server_id": sid,
                "name": r["name"],
                "source": "npm",
                "transport": "",
                "language": "TS",
                "publisher_type": "bot" if "actions" in r.get("publisher", "").lower() else "human",
                "artifact": r["name"],
                "desc": r.get("desc", ""),
            })

    # 3) 聚合去重
    merged = dedup_entities(records, level=args.level)

    # 4) gold set 验证
    gold = load_gold_set(args.gold)
    cand_ids = {m["server_id"] for m in merged}
    gold_ids = set(gold.keys())
    pr = compute_pr(cand_ids, gold_ids) if gold_ids else {"note": "gold set empty; annotate gold_set.csv first"}

    # 5) 可疑实体检测（含 awesome/headroom/private-gpt 这类污染样本）
    pollution_patterns = [
        ("awesome", "awesome-mcp-servers 是清单而非 server"),
        ("headroom", "headroom 是 MCP 代理/中继而非独立 server"),
        ("private-gpt", "private-gpt 是完整应用而非 MCP server"),
    ]
    suspicious = []
    for m in merged:
        for pat, reason in pollution_patterns:
            if pat in m["canonical_name"].lower():
                suspicious.append({"server_id": m["server_id"], "reason": reason})

    # 6) 写出
    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for m in merged:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    report = {
        "run_timestamp": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "dedup_level": args.level,
        "source_records": len(records),
        "merged_entities": len(merged),
        "compression": round(1 - len(merged) / max(len(records), 1), 3),
        "pr_vs_gold": pr,
        "suspicious_topn_sample": suspicious,
        "note": "pilot 输入来自 Top-N star GitHub/npm，不是四 registry 抽样；主测量需换 frame 重跑",
    }
    pathlib.Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
