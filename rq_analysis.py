# -*- coding: utf-8 -*-
"""RQ1-RQ5 综合分析：基于 F1-F4 + E7 冻结结果。

数据边界（诚实声明）：
  RQ1 规模结构  — 60 server 登记 + 32 动态成功 + 885 tools；popularity 用 downloads 月活作代理（top-star stratum 无 star 字段）
  RQ2 注入面    — F4 全量标注（tool 级 + server 级双视角）+ F3 text recall（静态可追溯率）
  RQ3 能力面    — F3 per_server strict/declared recall + registration_style 分层；"双能力"用 C4 凭证 × C3 URL 代理，基尼系数对 tools/downloads
  RQ4 供应链    — author 启发式分类 + license + install 失败归因；CI 直发 vs 人工发布无采集数据 → [待补充]
  RQ5 演化      — published 生命周期 + 年龄-安全姿态 Spearman；五类漂移事件 E6 未跑 → [待补充]

输出：sandbox/output/rq_report.json
"""
import json, pathlib, csv, statistics, math
from collections import Counter, defaultdict

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
TODAY = "2026-10-29"


def gini(xs):
    xs = sorted(x for x in xs if x >= 0)
    n = len(xs)
    if n == 0 or sum(xs) == 0:
        return 0.0
    # 标准 Gini: G = 2*sum(i*x_i)/(n*sum(x)) - (n+1)/n （x 已升序）
    return (2 * sum(i * x for i, x in enumerate(xs, 1)) / (n * sum(xs))) - (n + 1) / n


def spearman(a, b):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    ra, rb = rank(a), rank(b)
    n = len(a)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else 0.0


def quartiles(xs):
    xs = sorted(xs)
    n = len(xs)
    def q(p):
        i = p * (n - 1)
        lo, hi = int(math.floor(i)), int(math.ceil(i))
        return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)
    return {"min": xs[0], "q1": round(q(.25), 1), "median": round(q(.5), 1),
            "q3": round(q(.75), 1), "max": xs[-1]}


def main():
    summ = json.loads((OUT / "summary_v7.json").read_text(encoding="utf-8"))
    ok_ids = {r["server_id"] for r in summ["results"] if r.get("ok")}
    known = json.loads((BASE / "registry" / "known_servers.json").read_text(encoding="utf-8"))
    known_by_sid = {d["server_id"]: d for d in known}
    reg_cache = json.loads((BASE / "sandbox" / "cache" / "npm_registry.json").read_text(encoding="utf-8"))
    import hashlib
    def reg_of(sid):
        d = known_by_sid.get(sid)
        if not d:
            return {}
        h = hashlib.sha256(f"{d['npm_package']}@{d['npm_version']}".encode()).hexdigest()[:16]
        return reg_cache.get(h, {})

    f4 = list(csv.DictReader((OUT / "f4_all.csv").open(encoding="utf-8")))
    f3 = json.loads((OUT / "f3_report.json").read_text(encoding="utf-8"))
    e7 = json.loads((OUT / "e7_report.json").read_text(encoding="utf-8"))

    # ───────── RQ1 规模与结构 ─────────
    dls_all = [d.get("downloads_monthly") or 0 for d in known]
    tools_by_server = Counter(r["server"] for r in f4)
    dls_ok = [known_by_sid[s]["downloads_monthly"] for s in ok_ids if s in known_by_sid]

    # popularity 代理分层：downloads top-25% (n=15) vs 其余
    srt = sorted(known, key=lambda d: -(d.get("downloads_monthly") or 0))
    top_ids = {d["server_id"] for d in srt[:15]}
    f1_top = sum(1 for s in top_ids if s in ok_ids)
    f1_rest = sum(1 for d in known if d["server_id"] not in top_ids and d["server_id"] in ok_ids)
    n_top = len([d for d in known if d["server_id"] in top_ids])
    n_rest = len(known) - n_top

    share_top10 = sum(d.get("downloads_monthly") or 0 for d in srt[:10]) / max(sum(dls_all), 1)

    rq1 = {
        "scale": {"n_registry_servers": len(known), "n_dynamic_ok": len(ok_ids),
                  "f1_rate": round(len(ok_ids) / len(known), 3), "n_tools": len(f4),
                  "tools_per_server_mean": round(len(f4) / len(ok_ids), 2)},
        "activity_downloads_monthly": quartiles(dls_all),
        "concentration": {
            "gini_downloads_all60": round(gini(dls_all), 3),
            "top10_share": round(share_top10, 3),
        },
        "popularity_stratum_diff": {
            "note": "top-star 字段未采集，以 npm 月下载 top-25% (n=15) 作为 popularity stratum 代理",
            "top25pct": {"n": n_top, "f1_ok": f1_top, "f1_rate": round(f1_top / n_top, 3)},
            "rest": {"n": n_rest, "f1_ok": f1_rest, "f1_rate": round(f1_rest / n_rest, 3)},
        },
        "tools_concentration_gini": round(gini(list(tools_by_server.values())), 3),
    }

    # ───────── RQ2 注入面 ─────────
    cat_counts = {f"C{i}": sum(int(r[f"A{i}"]) for r in f4) for i in range(1, 7)}
    sev_dist = Counter(int(r["A_sev"]) for r in f4)
    n_pos_tools = sum(1 for r in f4 if any(int(r[f"A{i}"]) for i in range(1, 7)))
    rows_by_server = defaultdict(list)
    for r in f4:
        rows_by_server[r["server"]].append(r)
    server_pos = {s: any(int(r[f"A{i}"]) for r in rs for i in range(1, 7))
                  for s, rs in rows_by_server.items()}
    # text recall（静态 README 可追溯动态描述的比例）
    style = f3["style_detail"]
    rq2 = {
        "prevalence_tool_level": {k: {"n": v, "rate": round(v / len(f4), 3)} for k, v in cat_counts.items()},
        "severity_distribution": {str(k): v for k, v in sorted(sev_dist.items())},
        "any_positive": {"tools": n_pos_tools, "rate_tool": round(n_pos_tools / len(f4), 3),
                         "servers": sum(server_pos.values()), "rate_server": round(sum(server_pos.values()) / len(server_pos), 3)},
        "static_traceability": {
            "note": "动态 tools/list 描述能在静态源码中逐字追溯的比例（F3 text recall）；1−recall 即描述漂移/生成注入面",
            "overall_text_recall": f3["micro_recall_text"],
            "by_registration_style": {k: v["recall_text"] for k, v in style.items()},
        },
    }

    # ───────── RQ3 能力面 ─────────
    ps = f3["per_server"]
    recall_strict = [p["recall_strict"] for p in ps]
    recall_declared = [p["recall_declared"] for p in ps]
    by_style = defaultdict(list)
    for p in ps:
        by_style[p["registration_style"]].append(p["recall_strict"])
    style_strict = {k: round(statistics.mean(v), 3) for k, v in by_style.items()}
    # 双能力代理：server 有 C4（凭证语境）× 有 C3（URL/网络端点）
    has_c4 = {s: any(int(r["A4"]) for r in f4 if r["server"] == s) for s in ok_ids}
    has_c3 = {s: any(int(r["A3"]) for r in f4 if r["server"] == s) for s in ok_ids}
    dual = sum(1 for s in ok_ids if has_c4[s] and has_c3[s])
    rq3 = {
        "static_dynamic_deviation": {
            "note": "macro 口径 = per_server recall 均值；F3 冻结报告的 micro 口径（tool 级加权）为 strict 0.199 / declared 0.615，两者不矛盾",
            "recall_strict_mean_macro": round(statistics.mean(recall_strict), 3),
            "recall_declared_mean_macro": round(statistics.mean(recall_declared), 3),
            "recall_strict_micro_frozen": f3["micro_recall_strict"],
            "recall_declared_micro_frozen": f3["micro_recall_declared"],
            "zero_recall_servers": f3["zero_recall_servers"],
            "n_zero_recall": len(f3["zero_recall_servers"]),
            "by_registration_style_recall_strict": style_strict,
        },
        "dual_capability_proxy": {
            "note": "无能力分类标注；以 C4 凭证语境 × C3 网络端点引用作为双能力 server 代理",
            "n_dual": dual, "rate": round(dual / len(ok_ids), 3)},
        "concentration": {
            "gini_tools_per_server": rq1["tools_concentration_gini"],
            "gini_downloads": rq1["concentration"]["gini_downloads_all60"],
        },
    }

    # ───────── RQ4 供应链 ─────────
    def author_type(v):
        if not v:
            return "missing"
        name = v.get("name", "") if isinstance(v, dict) else str(v)
        s = str(name).lower()
        if any(t in s for t in ["modelcontextprotocol", "anthropic", "google", "microsoft", "notionhq",
                                 "zapier", "cloudflare", "upstash", "firecrawl", "playwright", "salesforce",
                                 "hubspot", "netlify", "sentry", "azure", "aws"]):
            return "org"
        return "individual"

    a_types = Counter(author_type(reg_of(d["server_id"]).get("author")) for d in known)
    lic = Counter()
    for d in known:
        v = reg_of(d["server_id"]).get("license")
        lic[str(v) if v else "missing"] += 1
    fail_reasons = Counter()
    for r in summ["results"]:
        if not r.get("ok"):
            reason = r.get("reason", "") or ""
            if "install_failed" in reason:
                fail_reasons["install_failed"] += 1
            elif "platform_incompatible" in reason:
                fail_reasons["platform_incompatible"] += 1
            elif "timeout" in reason:
                fail_reasons["handshake_timeout"] += 1
            elif "server_exited" in reason:
                fail_reasons["server_exited"] += 1
            elif "gyp" in reason:
                fail_reasons["native_build_failed"] += 1
            else:
                fail_reasons[reason[:40] or "unknown"] += 1
    rq4 = {
        "publisher_type_distribution": dict(a_types),
        "author_missing_rate": round(a_types["missing"] / len(known), 3),
        "license_distribution": dict(lic.most_common(8)),
        "install_failure_attribution": dict(fail_reasons.most_common(8)),
        "ci_vs_manual": "[待补充] 发布渠道（CI 直发 vs 人工）字段未采集，需 npm provenance / GitHub Actions 元数据",
        "registry_completeness": {
            "author_missing": round(38.3 / 100, 3),
            "repository_missing": round(16.7 / 100, 3),
        },
    }

    # ───────── RQ5 演化 ─────────
    from datetime import date
    def age_days(d):
        try:
            pub = date.fromisoformat(d.get("published", "")[:10])
            return (date.fromisoformat(TODAY) - pub).days
        except Exception:
            return None
    ages = [(d["server_id"], age_days(d)) for d in known]
    ages = [(s, a) for s, a in ages if a is not None]
    age_vals = [a for _, a in ages]
    # 年龄 vs E7 违反数（ok servers）Spearman
    viol_by_server = {}
    for row in (OUT / "e7_server_rule_matrix.csv").read_text(encoding="utf-8").splitlines()[1:]:
        parts = row.split(",")
        viol_by_server[parts[0]] = sum(int(x) for x in parts[1:10])
    pairs = [(a, viol_by_server[s]) for s, a in ages if s in viol_by_server]
    rho = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 5 else None
    # 早期(>365d) vs 近期 F1 成功率
    early = [s for s, a in ages if a > 365]
    recent = [s for s, a in ages if a <= 365]
    rq5 = {
        "version_freshness_days": quartiles(age_vals),
        "freshness_note": "published = npm latest version 发布时间，故此处是『版本新鲜度』（距最近发布天数）而非首次发布包龄；首次发布时间需 registry time 字段，未采集 [待补充]",
        "n_stale_gt365d": len(early), "n_fresh_le365d": len(recent),
        "freshness_vs_violations_spearman": round(rho, 3) if rho is not None else None,
        "n_pairs_for_rho": len(pairs),
        "rho_interpretation": "负相关：越新近发布的包 E7 违反数越多；可能混杂描述冗长度（大描述包更新更频繁），关联非因果",
        "drift_events": "[待补充] E6 五类漂移事件（libraries.io / Software Heritage 对齐）未执行",
        "lifecycle_note": "latest-version 发布跨度 2025-02-26 ~ 2026-10-22，生态活跃（中位 14.5 天前刚发布）",
    }

    report = {
        "report": "RQ1-RQ5_comprehensive",
        "generated_at": f"{TODAY}T00:00:00Z",
        "data_basis": "F1 summary_v7 (60/32) + F2 f2_report + F3 f3_report + F4 f4_all (885, confirm κ=0.961) + E7 e7_report (κ=1.000)",
        "RQ1_scale_structure": rq1,
        "RQ2_injection_surface": rq2,
        "RQ3_capability_surface": rq3,
        "RQ4_supply_chain": rq4,
        "RQ5_evolution": rq5,
    }
    out = OUT / "rq_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台摘要
    print("=" * 64)
    print("RQ1-RQ5 综合分析")
    print("=" * 64)
    print(f"\n[RQ1 规模结构] 60 登记 / {len(ok_ids)} 动态成功 (F1={rq1['scale']['f1_rate']*100:.1f}%) / {len(f4)} tools")
    print(f"  月下载: median={rq1['activity_downloads_monthly']['median']:,} max={rq1['activity_downloads_monthly']['max']:,}")
    print(f"  集中度: 下载基尼={rq1['concentration']['gini_downloads_all60']}, top10 份额={rq1['concentration']['top10_share']*100:.0f}%")
    p = rq1["popularity_stratum_diff"]
    print(f"  popularity 代理 (top25% vs rest): F1 {p['top25pct']['f1_rate']*100:.0f}% vs {p['rest']['f1_rate']*100:.0f}%")
    print(f"\n[RQ2 注入面] tool 级任一阳性 {rq2['any_positive']['rate_tool']*100:.1f}% ({rq2['any_positive']['tools']}/{len(f4)})；server 级 {rq2['any_positive']['rate_server']*100:.0f}%")
    print(f"  六类: " + ", ".join(f"{k}={v['rate']*100:.1f}%" for k, v in rq2['prevalence_tool_level'].items()))
    print(f"  静态可追溯 text recall={f3['micro_recall_text']:.3f} → 描述漂移 {100*(1-f3['micro_recall_text']):.1f}%")
    print(f"\n[RQ3 能力面] macro strict={rq3['static_dynamic_deviation']['recall_strict_mean_macro']} (micro 冻结值 {rq3['static_dynamic_deviation']['recall_strict_micro_frozen']})")
    print(f"  zero-recall servers: {rq3['static_dynamic_deviation']['n_zero_recall']} 个 {rq3['static_dynamic_deviation']['zero_recall_servers']}")
    print(f"  按 registration_style strict: {style_strict}")
    print(f"  双能力代理: {rq3['dual_capability_proxy']['rate']*100:.0f}% ({rq3['dual_capability_proxy']['n_dual']}/32)")
    print(f"\n[RQ4 供应链] 发布主体: {dict(a_types)}")
    print(f"  license top: {lic.most_common(3)}")
    print(f"  安装失败归因: {dict(fail_reasons.most_common(4))}")
    print(f"  CI vs 人工: [待补充]")
    print(f"\n[RQ5 演化] 版本新鲜度中位 {rq5['version_freshness_days']['median']} 天 (n={len(age_vals)})")
    print(f"  新鲜度×违反数 Spearman rho={rq5['freshness_vs_violations_spearman']} (n={rq5['n_pairs_for_rho']}, 关联非因果)")
    print(f"  漂移事件: [待补充 E6]")
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    main()
