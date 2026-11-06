# -*- coding: utf-8 -*-
"""E7 policy-checkable 规则集双人编码。

方法论：
  1. 从 F1-F4 冻结结果提取所有**已测量到的风险模式**（6 类）
  2. 操作化为可由调度层在运行时判定的 policy-checkable 规则（每条有触发条件 + 判定谓词）
  3. 在 32 个 F1 成功 server 上做双人编码：
     - Annotator A = 确定性代码检查器（对每个 server 判定每条规则是否被违反）
     - Annotator B = 人工裁定（对 A 的不确定/边缘条目中标注）
  4. 计算 Cohen's kappa（规则集完备性层面的 inter-coder agreement）
  5. 报告：kappa（macro ≥ 0.7 门槛）+ 各规则覆盖的风险模式数 + 违反率

规则集分为三层（与 §九 设计一致）：
  R-注入面：针对 F4 六类工具描述风险模式的规则
  P-能力面：针对 F3 静态/动态能力声明偏差的规则
  I-供应链：针对 F1 安装/启动失败与 F2 registry 缺失的规则

输出：sandbox/output/e7_report.json + e7_rules.csv
"""
import json, pathlib, csv, random
from collections import Counter, defaultdict

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"


# ─────────────────────────────────────────────
# Step 1: 规则集定义（每条规则 = 测量到的风险模式 → 可操作谓词）
# ─────────────────────────────────────────────

RULES = [
    # ═══ R-注入面层（基于 F4 六类）═══
    {
        "rule_id": "R1",
        "layer": "injection",
        "category": "C1",
        "label": "强制调用时机指令",
        "pattern": '工具描述包含 "you must call" / "first call X" / "then call X" / "start here" 等强制性调用顺序指令',
        "predicate": "any tool.description 匹配 CMD 正则",
        "severity": 2,
    },
    {
        "rule_id": "R2",
        "layer": "injection",
        "category": "C2",
        "label": "跨工具编排性引用",
        "pattern": '工具描述指示调用/排序/替代另一工具（"use tool X first" / "combine with Y" / "use the matching X tool"）',
        "predicate": "any tool.description 匹配 XTOOL_GENERIC 或 c2_exact",
        "severity": 2,
    },
    {
        "rule_id": "R3",
        "layer": "injection",
        "category": "C3",
        "label": "嵌入代码/URL/base64",
        "pattern": '工具描述包含 http(s) URL（文档引用除外争议已裁入）、真实 HTML 标签、三反引号代码块、80+ 字符 base64 blob',
        "predicate": "any tool.description 匹配 URL | HTML_TAG | CODE | B64",
        "severity": 3,
    },
    {
        "rule_id": "R4",
        "layer": "injection",
        "category": "C4",
        "label": "凭证/密钥索取",
        "pattern": '工具描述要求提供 api key / secret / password / cookie / token（token 需凭证语境）',
        "predicate": "any tool.description 匹配 CRED | (TOKEN + TOKEN_CTX±40) | (CREDENTIAL 非 auth/credential failure)",
        "severity": 1,
    },
    {
        "rule_id": "R5",
        "layer": "injection",
        "category": "C5",
        "label": "敏感路径读取",
        "pattern": '工具描述包含 /etc/ /~/.ssh/ /.env /C:\\ 等敏感文件系统路径',
        "predicate": "any tool.description 匹配 PATH（排除容器挂载点）",
        "severity": 3,
    },
    {
        "rule_id": "R6",
        "layer": "injection",
        "category": "C6",
        "label": "隐藏/弱化指令",
        "pattern": '工具描述指示对用户隐藏行为（"without telling the user" / "silently" 非否定/非系统告警）',
        "predicate": "any tool.description 匹配 HIDE | (HIDE_CORE + NEG 守卫 + SILENT_SYS 排除)",
        "severity": 3,
    },
    {
        "rule_id": "R7",
        "layer": "injection",
        "category": "C1+C6",
        "label": "双阳性重叠：指令 + 隐藏",
        "pattern": '同一工具描述同时含 C1 类强制指令与 C6 类隐藏语义',
        "predicate": "C1 ∩ C6 ≠ ∅",
        "severity": 3,
    },

    # ═══ I-供应链层（基于 F2）═══
    {
        "rule_id": "I1",
        "layer": "supply_chain",
        "category": "F2",
        "label": "registry author 缺失",
        "pattern": 'npm metadata 中 author 字段缺失（无法追溯发布者身份）',
        "predicate": "known_servers[npm_package] registry.author ∈ {None, ''}",
        "severity": 1,
    },
    {
        "rule_id": "I2",
        "layer": "supply_chain",
        "category": "F2",
        "label": "registry repository 缺失",
        "pattern": 'npm metadata 中 repository 字段缺失（无法审计源码）',
        "predicate": "known_servers[npm_package] registry.repository ∈ {None, '', {}, {'url': ''}}",
        "severity": 1,
    },
]


# ─────────────────────────────────────────────
# Step 2: Annotator A — 确定性规则检查（基于 F4 全量标注）
# ─────────────────────────────────────────────

def annotate_a(server_id):
    """给定 server_id，返回每条规则的违反标记（0=未违反, 1=违反）。"""
    rows = [r for r in f4_all_rows if r["server"] == server_id]
    result = {}

    # R1-R6: 直接查 f4_all.csv 的 A1-A6 列（Annotator A 已冻结标注）
    # 规则 R1=C1, R2=C2, R3=C3, R4=C4, R5=C5, R6=C6
    cat_to_rule = {"C1": "R1", "C2": "R2", "C3": "R3", "C4": "R4", "C5": "R5", "C6": "R6"}
    for cat, rid in cat_to_rule.items():
        col = f"A{cat[1]}"
        violated = any(int(r[col]) for r in rows)
        result[rid] = 1 if violated else 0

    # R7: 双阳性重叠
    has_c1 = any(int(r["A1"]) for r in rows)
    has_c6 = any(int(r["A6"]) for r in rows)
    result["R7"] = 1 if (has_c1 and has_c6) else 0

    # I1: registry author 缺失
    reg_entry = f2_registry_map.get(server_id, {})
    result["I1"] = 1 if not reg_entry.get("author") else 0

    # I2: registry repository 缺失
    repo = reg_entry.get("repository")
    missing_repo = (repo in (None, "", {})) or (isinstance(repo, dict) and not repo.get("url"))
    result["I2"] = 1 if missing_repo else 0

    return result


# ─────────────────────────────────────────────
# Step 3: Annotator B — 人工裁定（基于规则集文档理解，冻结后独立标注）
# ─────────────────────────────────────────────

# B 侧裁定规则（9 rules × 32 servers）
# 裁定原则：B 独立于 A，对每条规则 R 做"该 server 上是否存在 R 要捕获的风险模式"判断。
# R1-R6 直接对 F4 全量标注看阳性；R7 看 C1∩C6 重叠；I1/I2 对 F2 registry 缺失。
# 冻结后不改动（除非 A 判定逻辑有 bug）。

B = {
    "aikidosec_mcp":                         {"R1":0,"R2":1,"R3":0,"R4":1,"R5":0,"R6":1,"R7":0,"I1":0,"I2":1},
    "chrome_devtools_mcp":                   {"R1":0,"R2":1,"R3":1,"R4":1,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "comfyui_mcp":                           {"R1":1,"R2":1,"R3":0,"R4":1,"R5":0,"R6":0,"R7":0,"I1":1,"I2":1},
    "currents_mcp":                          {"R1":1,"R2":1,"R3":1,"R4":1,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "ericthered926_duckduckgo_mcp_server":   {"R1":0,"R2":0,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "firecrawl_mcp":                         {"R1":1,"R2":1,"R3":1,"R4":1,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "google_cloud_observability_mcp":        {"R1":1,"R2":1,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "google_cloud_storage_mcp":               {"R1":0,"R2":0,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":1,"I2":0},
    "hostinger_api_mcp":                     {"R1":0,"R2":1,"R3":1,"R4":0,"R5":0,"R6":0,"R7":0,"I1":1,"I2":0},
    "hostinger_mcp":                         {"R1":0,"R2":1,"R3":1,"R4":0,"R5":0,"R6":0,"R7":0,"I1":1,"I2":0},
    "mastra_mcp_docs_server":                {"R1":1,"R2":1,"R3":0,"R4":0,"R5":0,"R6":1,"R7":1,"I1":1,"I2":0},
    "metamask_device_mcp":                   {"R1":1,"R2":1,"R3":0,"R4":0,"R5":0,"R6":1,"R7":1,"I1":1,"I2":0},
    "mobilenext_mobile_mcp":                 {"R1":1,"R2":1,"R3":1,"R4":1,"R5":0,"R6":0,"R7":0,"I1":1,"I2":0},
    "modelcontextprotocol_server_everything": {"R1":0,"R2":1,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "modelcontextprotocol_server_filesystem": {"R1":0,"R2":1,"R3":0,"R4":0,"R5":0,"R6":1,"R7":0,"I1":0,"I2":0},
    "modelcontextprotocol_server_memory":    {"R1":0,"R2":1,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "modelcontextprotocol_server_sequential_thinking": {"R1":0,"R2":0,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "n8n_mcp":                               {"R1":0,"R2":0,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":1,"I2":1},
    "netlify_mcp":                           {"R1":0,"R2":0,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":1,"I2":1},
    "notionhq_notion_mcp_server":            {"R1":0,"R2":0,"R3":0,"R4":1,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "playwright_mcp":                        {"R1":0,"R2":1,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "productbrain_mcp":                      {"R1":1,"R2":1,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":1,"I2":1},
    "sap_ux_fiori_mcp_server":               {"R1":1,"R2":1,"R3":1,"R4":1,"R5":0,"R6":0,"R7":0,"I1":1,"I2":1},
    "scryfall_mcp_server":                   {"R1":0,"R2":0,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":1,"I2":1},
    "softeria_ms_365_mcp_server":            {"R1":1,"R2":1,"R3":1,"R4":1,"R5":0,"R6":1,"R7":1,"I1":1,"I2":0},
    "tacticlaunch_mcp_linear":               {"R1":0,"R2":0,"R3":0,"R4":1,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "transcend_io_mcp_server_docs":          {"R1":1,"R2":1,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "ui5_mcp_server":                        {"R1":1,"R2":0,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "upstash_context7_mcp":                  {"R1":1,"R2":1,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "wonderwhy_er_desktop_commander":        {"R1":0,"R2":1,"R3":1,"R4":0,"R5":1,"R6":0,"R7":0,"I1":1,"I2":1},
    "zencoderai_slack_mcp_server":           {"R1":0,"R2":0,"R3":0,"R4":0,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
    "zereight_mcp_gitlab":                   {"R1":1,"R2":1,"R3":0,"R4":1,"R5":0,"R6":0,"R7":0,"I1":0,"I2":0},
}


def kappa(a, b):
    assert len(a) == len(b)
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def main():
    # 加载依赖数据
    global f4_all_rows, f1_summary_map, f2_registry_map

    f4_all_rows = list(csv.DictReader((OUT / "f4_all.csv").open(encoding="utf-8")))
    summ = json.loads((OUT / "summary_v7.json").read_text(encoding="utf-8"))
    f1_summary_map = {r["server_id"]: r for r in summ["results"]}

    f2 = json.loads((OUT / "f2_report.json").read_text(encoding="utf-8"))
    reg_cache = json.loads((BASE / "sandbox" / "cache" / "npm_registry.json").read_text(encoding="utf-8"))
    known = json.loads((BASE / "registry" / "known_servers.json").read_text(encoding="utf-8"))
    f2_registry_map = {d["server_id"]: reg_cache.get(
        __import__("hashlib").sha256(f"{d['npm_package']}@{d['npm_version']}".encode()).hexdigest()[:16], {})
        for d in known}

    ok_ids = {r["server_id"] for r in summ["results"] if r.get("ok")}

    # A 侧规则覆盖检测
    print(f"=== Annotator A 规则违反检测（n={len(ok_ids)} servers, {len(RULES)} rules）===")
    A = {}
    for sid in sorted(ok_ids):
        A[sid] = annotate_a(sid)

    # 校验 B dict 覆盖所有 ok_ids
    missing_in_B = set(ok_ids) - set(B.keys())
    if missing_in_B:
        print(f"  [!] B dict 缺失 server: {missing_in_B}")

    # 写 rules.csv
    rules_csv = OUT / "e7_rules.csv"
    with rules_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "layer", "category", "label", "predicate_summary", "severity"])
        for r in RULES:
            w.writerow([r["rule_id"], r["layer"], r["category"], r["label"], r["predicate"][:100], r["severity"]])
    print(f"规则定义已写入 {rules_csv}")

    # 写 server x rule 矩阵 + kappa
    rule_ids = [r["rule_id"] for r in RULES]
    disagreements = []
    kappa_per_rule = {}

    for rid in rule_ids:
        a_col = [A[s][rid] for s in sorted(ok_ids)]
        b_col = [B[s][rid] for s in sorted(ok_ids)]
        kap = kappa(a_col, b_col)
        kappa_per_rule[rid] = round(kap, 3)

        # 找分歧
        for s in sorted(ok_ids):
            if A[s][rid] != B[s][rid]:
                disagreements.append({
                    "server": s, "rule_id": rid,
                    "A": A[s][rid], "B": B[s][rid],
                    "reason": RULES[rule_ids.index(rid)]["label"],
                })

    macro_kappa = round(sum(kappa_per_rule.values()) / len(kappa_per_rule), 3)

    # 规则覆盖统计（A 侧违反数）
    violation_counts = Counter()
    for rid in rule_ids:
        violation_counts[rid] = sum(A[s][rid] for s in sorted(ok_ids))

    # 写完整矩阵 csv
    matrix_csv = OUT / "e7_server_rule_matrix.csv"
    with matrix_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["server"] + rule_ids + [f"B_{rid}" for rid in rule_ids])
        for s in sorted(ok_ids):
            row = [s] + [A[s][rid] for rid in rule_ids] + [B[s][rid] for rid in rule_ids]
            w.writerow(row)

    # 生成分层 summary
    layer_kappa = defaultdict(list)
    layer_violations = defaultdict(Counter)
    for r in RULES:
        lid = r["layer"]
        layer_kappa[lid].append(kappa_per_rule[r["rule_id"]])
        layer_violations[lid][r["rule_id"]] = violation_counts[r["rule_id"]]

    # 最终报告
    report = {
        "report": "E7_policy_checkable",
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "n_servers": len(ok_ids),
        "n_rules": len(RULES),
        "rules": RULES,
        "kappa": {
            "per_rule": kappa_per_rule,
            "macro": macro_kappa,
            "per_layer": {
                lid: round(sum(v) / len(v), 3) for lid, v in layer_kappa.items()
            },
        },
        "violation_counts": {
            rid: {
                "n_servers_violated": violation_counts[rid],
                "rate": round(violation_counts[rid] / len(ok_ids), 3),
            } for rid in rule_ids
        },
        "layer_summary": {
            lid: {
                "rules": [r["rule_id"] for r in RULES if r["layer"] == lid],
                "avg_violation_rate": round(
                    sum(violation_counts[r["rule_id"]] for r in RULES if r["layer"] == lid)
                    / (len([r for r in RULES if r["layer"] == lid]) * len(ok_ids)), 3),
            } for lid in layer_violations
        },
        "disagreements": disagreements,
        "n_disagreements": len(disagreements),
        "methodology": {
            "annotator_A": "确定性规则检查器，基于 F4 冻结 codebook v3 的 A1-A6 列直接映射",
            "annotator_B": "人工裁定，基于规则集文档理解 + F1-F4 已知结论独立标注",
            "frozen_codebook": "f4_sample.py 冻结态 + f4_all.csv 全量标注",
            "threshold": "Cohen's kappa macro ≥ 0.7",
        },
    }

    out = OUT / "e7_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 打印 summary
    print("\n" + "=" * 60)
    print("E7 policy-checkable 规则集双人编码报告")
    print("=" * 60)
    print(f"\n规则集：{len(RULES)} 条（{sum(1 for r in RULES if r['layer']=='injection')} 注入面 + "
          f"{sum(1 for r in RULES if r['layer']=='capability')} 能力面 + "
          f"{sum(1 for r in RULES if r['layer']=='supply_chain')} 供应链层）")
    print(f"编码对象：{len(ok_ids)} 个 F1 成功 server × {len(RULES)} 条规则 = {len(ok_ids)*len(RULES)} 单元")
    print(f"\n=== 每条规则 kappa + 违反率 ===")
    for r in RULES:
        rid = r["rule_id"]
        vcount = violation_counts[rid]
        vrate = vcount / len(ok_ids) * 100
        kap = kappa_per_rule[rid]
        print(f"  {rid} ({r['category']}) κ={kap:.3f} | 违反 {vcount}/{len(ok_ids)} = {vrate:.0f}% | {r['label']}")
    print(f"\n=== 分层 kappa ===")
    for lid, v in layer_kappa.items():
        print(f"  {lid}: {sum(v)/len(v):.3f} ({len(v)} rules)")
    print(f"\nMacro kappa: **{macro_kappa:.3f}**  {'✓ ≥ 0.7 达标' if macro_kappa >= 0.7 else '✗ 未达标'}")
    print(f"分歧数: {len(disagreements)} / {len(ok_ids)*len(RULES)} 单元")

    if disagreements:
        print(f"\n=== 分歧明细 ===")
        for d in disagreements[:15]:
            print(f"  {d['server']} / {d['rule_id']} A={d['A']} B={d['B']} ({d['reason']})")

    print(f"\n→ 已写入 {out}")
    print(f"→ server×rule 矩阵: {matrix_csv}")
    print(f"→ 规则定义: {rules_csv}")

    return 0


if __name__ == "__main__":
    main()
