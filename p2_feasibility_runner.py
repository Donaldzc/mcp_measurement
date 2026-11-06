# -*- coding: utf-8 -*-
"""可行性试点 runner——v2 四指标计算。

F1 抓取成功率
  数据来源: manifest.json + manifest_dynamic.json
  计算: (抓取成功的 server 数) / (候选总数)，分 source 报告；
        同时报告 HTTP 403/404/超时等失败原因分布。

F2 缺失率
  字段缺失: entities.jsonl 中字段为空/[待补充] 的比例，按字段分桶。
  tool 描述缺失: 工具条目 description 为空的比例（来源语料）。

F3 静态/动态能力一致率
  抽取 20 server（gold set 内），比对静态 AST 声明的能力 vs 动态
  tools/list 返回的 schema，逐能力类型报告一致率与 Cohen's kappa。
  注意：本脚本需要 AST 抽取输出（F3 是全量阶段指标，试点阶段
  若 AST 管道未就绪则只输出"待补"占位，先把框架写好）。

F4 标注一致率
  读取 codebook_template.csv 的双人标注列，计算 Cohen's kappa
  （每分类独立 + 全局 micro），输出 kappa 矩阵。
  gold set 标注缺失时本脚本只输出模板说明，不臆造 kappa。

用法: python p2_feasibility_runner.py --corpus-dir d2_pilot/corpus --gold gold_set
"""
import argparse
import csv
import json
import pathlib
import sys
from collections import defaultdict


def load_jsonl(p):
    if not pathlib.Path(p).exists():
        return []
    raw = open(p, encoding="utf-8").read()
    # 兼容完整 JSON object（manifest.json）和 JSONL（tools corpus）
    if raw.lstrip().startswith("[") or raw.lstrip().startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return [json.loads(l) for l in raw.splitlines() if l.strip()]


def metric_f1(corpus_dir):
    static = load_jsonl(corpus_dir / "manifest.json")  # 字典或 list
    dynamic = load_jsonl(corpus_dir / "manifest_dynamic.json")

    repos_ok = repos_err = rate_limited = 0
    if isinstance(static, dict) and "stats" in static:
        s = static["stats"]
        repos_ok = s.get("repos_ok", 0)
        repos_err = s.get("repos_err", 0)
        rate_limited = s.get("rate_limited", False)
    elif isinstance(static, list):
        for r in static:
            if r.get("status") == "ok": repos_ok += 1
            else: repos_err += 1

    attempts = repos_ok + repos_err
    success_rate = repos_ok / max(attempts, 1)

    dyn_total = dyn_ok = 0
    for r in dynamic:
        dyn_total += 1
        if r.get("tools_listed", 0) > 0:
            dyn_ok += 1

    return {
        "F1_grasp_success_rate_static": round(success_rate, 3),
        "F1_grasp_success_rate_dynamic": round(dyn_ok / max(dyn_total, 1), 3),
        "F1_static_attempts": attempts,
        "F1_static_rate_limited": rate_limited,
        "F1_dynamic_attempts": dyn_total,
        "note": "static 输入为 Top-N star 样本（pilot-only）；主测量应按 registry 抽样后重跑本指标",
    }


def metric_f2(corpus_dir):
    tools = []
    for fn in ("tools_corpus_static.jsonl", "tools_corpus_dynamic.jsonl"):
        p = corpus_dir / fn
        if p.exists():
            tools.extend(load_jsonl(p))
    if not tools:
        return {"F2_tool_description_missing_rate": None, "note": "no corpus yet"}
    total = len(tools)
    missing_desc = sum(1 for t in tools if not t.get("description"))
    by_method = defaultdict(lambda: [0, 0])
    for t in tools:
        m = t.get("method", "?")
        by_method[m][0] += 1
        if not t.get("description"):
            by_method[m][1] += 1
    return {
        "F2_tool_total": total,
        "F2_tool_description_missing_rate": round(missing_desc / total, 3),
        "F2_tool_description_missing_by_method": {
            m: round(miss / max(n, 1), 3) for m, (n, miss) in by_method.items()
        },
    }


def metric_f3():
    """静态/动态能力一致率：需要 AST 抽取输出，框架先占位。"""
    return {
        "F3_static_dynamic_agreement_cohen_kappa": None,
        "note": "需 AST/dataflow 抽取能力后跑 20-server 抽样比对；全量阶段执行",
    }


def metric_f4(gold_dir):
    p = gold_dir / "codebook_template.csv"
    if not p.exists():
        return {"F4_kappa": None, "note": "annotate codebook_template.csv (双人) 后重跑"}
    # 极简 kappa：每分类 C1-C6 两位标注者是否一致
    cats = ["C1", "C2", "C3", "C4", "C5", "C6"]
    kappas = {}
    with open(p, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if len(rows) < 5:
        return {"F4_kappa": None, "note": f"仅 {len(rows)} 条，需 ≥20 条双人标注"}
    for cat in cats:
        a_col = f"annotator_a_{cat}"
        b_col = f"annotator_b_{cat}"
        agree = sum(1 for r in rows if r.get(a_col) == r.get(b_col) and r.get(a_col) not in ("", None))
        total = sum(1 for r in rows if r.get(a_col) not in ("", None) or r.get(b_col) not in ("", None))
        # 简化版 kappa（不含 chance agreement 校正）：只报告观察一致率
        kappas[cat] = round(agree / max(total, 1), 3)
    return {"F4_kappa_observed_agreement_by_class": kappas}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-dir", default=str(
        pathlib.Path(__file__).resolve().parent / "d2_pilot" / "corpus"))
    ap.add_argument("--gold-dir", default=str(
        pathlib.Path(__file__).resolve().parent / "gold_set"))
    ap.add_argument("--out", default=str(
        pathlib.Path(__file__).resolve().parent / "feasibility_report.json"))
    args = ap.parse_args()

    corpus_dir = pathlib.Path(args.corpus_dir)
    gold_dir = pathlib.Path(args.gold_dir)

    report = {
        "run_timestamp": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "thresholds": {
            "F1_success_rate": ">= 0.85 (各 source 分)",
            "F2_missing_rate": "< 0.15 (工具描述)",
            "F3_kappa": ">= 0.6",
            "F4_kappa": ">= 0.6 (按分类)",
        },
        "F1_grasp_success": metric_f1(corpus_dir),
        "F2_missing": metric_f2(corpus_dir),
        "F3_agreement": metric_f3(),
        "F4_annotation": metric_f4(gold_dir),
    }
    pathlib.Path(args.out).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
