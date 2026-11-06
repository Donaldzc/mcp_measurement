# -*- coding: utf-8 -*-
"""F1 动态抓取成功率报告生成器。

读入:
  sandbox/output/summary.json   sandbox_handshake_runner.py 的输出
  registry/npm_frame.jsonl      抽样框（downloads/published 元数据）
输出:
  sandbox/output/f1_report.json   结构化指标
  stdout                          人读摘要

归因分类（互斥，按优先级）:
  install_failed    主机 npm install 失败（原生编译/平台/包损坏）
  entry_unknown     装上了但找不到 bin/main 入口
  entry_unknown     装上了但找不到 bin/main 入口
  container_timeout 容器 120s 未完成握手（可能是等待输入/死循环）
  container_rc_*    容器非零退出（缺运行时依赖/原生模块平台不符）
  handshake_error   握手器捕获 JSON-RPC error（server 启动即崩/需凭证）
  no_output_file    容器正常退出但没写结果文件

F1 定义（v2 设计 §4）: F1 = 动态 tools/list 成功的 server 数 / 尝试抓取的 server 数
分层报告: 全量 / 按下载量分位（top-20 vs 其余），为分布层抽样提供依据
"""
import json
import pathlib
import sys
from collections import Counter

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"


def classify(reason):
    if not reason:
        return "ok"
    if reason.startswith("install_failed"):
        return "install_failed"
    if "entry unknown" in reason:
        return "entry_unknown"
    if reason.startswith("container_timeout"):
        return "container_timeout"
    if reason.startswith("container_rc_"):
        return "container_error"
    if reason.startswith("handshake_error"):
        return "handshake_error"
    if reason.startswith("no_output_file"):
        return "no_output_file"
    return "other"


def main():
    summary_path = OUT / "summary.json"
    if not summary_path.exists():
        print("summary.json 不存在——先跑 sandbox_handshake_runner.py")
        return 1
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    results = s.get("results", [])
    installs = s.get("install_failures", [])
    attempted = s.get("n_attempted", len(results))

    # 合并 install failures（runner 把它们排除在 results 之外时补进来）
    merged = list(results)
    done_ids = {r["server_id"] for r in merged}
    for f in installs:
        if f["server_id"] not in done_ids:
            merged.append({"server_id": f["server_id"], "ok": False,
                           "n_tools": 0,
                           "reason": "install_failed: " + f.get("detail", "")[:200]})

    n_ok = sum(1 for r in merged if r.get("ok"))
    f1 = n_ok / len(merged) if merged else 0.0
    cats = Counter(classify(r.get("reason", "")) for r in merged)
    tools_total = sum(r.get("n_tools", 0) for r in merged if r.get("ok"))

    # 下载量分层（如果有元数据）
    meta = {}
    frame = BASE / "registry" / "npm_frame.jsonl"
    if frame.exists():
        for line in frame.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                meta[e["npm_package"]] = e.get("downloads_monthly", 0)
            except Exception:
                pass
    strat = {}
    if meta:
        top20 = {r["server_id"] for r in merged
                 if meta.get(r.get("pkg_ver", "").rsplit("@", 1)[0], 0) and
                 sorted(meta.values(), reverse=True).index(
                     meta.get(r.get("pkg_ver", "").rsplit("@", 1)[0], 0)) < 20}
        all_ids = {r["server_id"] for r in merged}
        for label, ids in [("top20_downloads", top20),
                           ("rest", all_ids - top20)]:
            sub = [r for r in merged if r["server_id"] in ids]
            if sub:
                strat[label] = {"n": len(sub),
                                "ok": sum(1 for r in sub if r.get("ok")),
                                "f1": round(sum(1 for r in sub if r.get("ok")) / len(sub), 3)}

    report = {
        "run_ts": s.get("run_ts"),
        "F1_dynamic_capture_success_rate": round(f1, 3),
        "n_attempted": len(merged),
        "n_ok": n_ok,
        "tools_total": tools_total,
        "failure_attribution": dict(cats.most_common()),
        "stratified": strat,
        "sandbox_config": s.get("sandbox_config", {}),
        "failures": [{"server_id": r["server_id"], "category": classify(r.get("reason", "")),
                      "reason": (r.get("reason") or "")[:300]}
                     for r in merged if not r.get("ok")],
    }
    (OUT / "f1_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: v for k, v in report.items() if k != "failures"},
                     ensure_ascii=False, indent=2))
    if report["failures"]:
        print("\n失败明细（前 15）:")
        for f in report["failures"][:15]:
            print(f"  [{f['category']}] {f['server_id']}: {f['reason'][:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
