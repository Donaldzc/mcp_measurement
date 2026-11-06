# -*- coding: utf-8 -*-
"""E6 漂移 + RQ4 CI 发布方式 补采。

数据源（单源 npm registry，已验证可达，无需认证）：
  1. Full packument（60 包）：time / maintainers / per-version description, scripts,
     dependencies, keywords, _npmUser —— 操作化五类漂移中的四类半
  2. npm attestations 端点 https://registry.npmjs.org/-/npm/v1/attestations/{pkg}@{ver}
     —— provenance 只能在 CI 中生成 → CI 直发的权威信号

五类漂移操作化（设计 §50）：
  D1 描述漂移：相邻正式版本 description 字面变化（difflib 相似度 < 0.85；
     AST 级语义相似度 0.85 需模型扫描，本采集为 registry 字面代理，A5 消融另测）
  D2 能力漂移：[代理] keywords 变化；AST/SBOM 级能力漂移需逐版本源码采集，[部分覆盖]
  D3 依赖漂移：相邻版本 dependencies 的增/删/版本变更
  D4 安装脚本漂移：相邻版本 preinstall/install/postinstall/prepare 变化；
     其中"新增 postinstall"单独计数（供应链最敏感事件）
  D5 维护者漂移：相邻版本 _npmUser 发布者身份变化

RQ4 CI vs 人工：
  ci_provenance：attestations 端点 200
  ci_bot：latest _npmUser.name 含 bot/web-flow/github-actions 标记
  manual：两者皆否
  交叉：与 E7 违反数、F1 成功率

漂移缓存：sandbox/cache/npm_packument_trimmed.json（裁剪后缓存，避免全量 packument 体积）
输出：sandbox/output/e6_rq4_report.json；并回写 rq_report.json 的 RQ4/RQ5 [待补充] 字段
"""
import json, pathlib, urllib.request, concurrent.futures, difflib, statistics, math, time
from collections import Counter, defaultdict
from datetime import date

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
CACHE = BASE / "sandbox" / "cache" / "npm_packument_trimmed.json"
ATTEST_BASE = "https://registry.npmjs.org/-/npm/v1/attestations/"
PACK_BASE = "https://registry.npmjs.org/"
TODAY = date(2026, 10, 29)

SCRIPT_FIELDS = ["preinstall", "install", "postinstall", "prepare"]
BOT_MARKS = ["bot", "web-flow", "github-actions", "npm-cli", "renovate", "dependabot", "greenkeeper"]


def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "mcp-measurement-e6/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def collect_packuments(known):
    """拉取并裁剪缓存 60 个 packument。"""
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    todo = [d["npm_package"] for d in known if d["npm_package"] not in cache]
    print(f"  packument 缓存命中 {len(known) - len(todo)} / 待拉取 {len(todo)}")

    def _fetch(pkg):
        try:
            data = fetch_json(PACK_BASE + pkg.replace("/", "%2F"))
            versions = {}
            for v, vo in (data.get("versions") or {}).items():
                versions[v] = {
                    "description": vo.get("description"),
                    "keywords": vo.get("keywords"),
                    "scripts": {k: vo.get("scripts", {}).get(k) for k in SCRIPT_FIELDS
                                if vo.get("scripts", {}).get(k)},
                    "dependencies": vo.get("dependencies") or {},
                    "npm_user": (vo.get("_npmUser") or {}).get("name"),
                }
            return pkg, {
                "time": {k: v for k, v in (data.get("time") or {}).items() if k not in ("created", "modified")},
                "maintainers": data.get("maintainers"),
                "versions": versions,
            }
        except Exception as e:
            return pkg, {"error": str(e)[:120]}

    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            for i, (pkg, trimmed) in enumerate(ex.map(_fetch, todo), 1):
                cache[pkg] = trimmed
                if i % 20 == 0 or i == len(todo):
                    print(f"    packument progress {i}/{len(todo)}")
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        print(f"  缓存已写入 {CACHE.name}（{len(cache)} 条，裁剪版）")
    return cache


def collect_attestations(known):
    """npm provenance attestations：200=CI 直发，404=无。"""
    cache_p = BASE / "sandbox" / "cache" / "npm_attestations.json"
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    todo = [(d["npm_package"], d["npm_version"]) for d in known
            if d["npm_package"] not in cache]
    print(f"  attestations 缓存命中 {len(known) - len(todo)} / 待查 {len(todo)}")

    def _check(args):
        pkg, ver = args
        try:
            fetch_json(ATTEST_BASE + f"{pkg.replace('/', '%2F')}@{ver}")
            return pkg, True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return pkg, False
            return pkg, None  # 其他错误
        except Exception:
            return pkg, None

    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            for pkg, ok in ex.map(_check, todo):
                cache[pkg] = ok
        cache_p.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def sim(a, b):
    if a is None and b is None:
        return 1.0
    if a is None or b is None:
        return 0.0
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


def drift_analysis(known, packs):
    """五类漂移事件检测（相邻正式版本对）。"""
    events = {k: {"n_events": 0, "n_servers": 0} for k in ["D1_desc", "D2_keywords",
               "D3_deps", "D4_scripts", "D4_new_postinstall", "D5_maintainer"]}
    per_server = {}
    pair_total = 0
    quarterly = Counter()

    for d in known:
        pkg = d["npm_package"]
        p = packs.get(pkg) or {}
        if "error" in p or not p.get("versions"):
            continue
        tmap = p["time"]
        vs = [v for v in p["versions"] if v in tmap and "-" not in v]  # 排除 prerelease
        vs.sort(key=lambda v: tmap[v])
        n_pairs = max(0, len(vs) - 1)
        pair_total += n_pairs
        sv = {"n_releases": len(vs), "n_pairs": n_pairs, "events": Counter()}

        for a, b in zip(vs, vs[1:]):
            va, vb = p["versions"][a], p["versions"][b]
            q = tmap[b][:7]
            if sim(va.get("description"), vb.get("description")) < 0.85:
                events["D1_desc"]["n_events"] += 1; sv["events"]["D1_desc"] += 1; quarterly[q] += 1
            if va.get("keywords") != vb.get("keywords"):
                events["D2_keywords"]["n_events"] += 1; sv["events"]["D2_keywords"] += 1; quarterly[q] += 1
            if va.get("dependencies") != vb.get("dependencies"):
                events["D3_deps"]["n_events"] += 1; sv["events"]["D3_deps"] += 1; quarterly[q] += 1
            if va.get("scripts") != vb.get("scripts"):
                events["D4_scripts"]["n_events"] += 1; sv["events"]["D4_scripts"] += 1; quarterly[q] += 1
                if vb.get("scripts", {}).get("postinstall") and not va.get("scripts", {}).get("postinstall"):
                    events["D4_new_postinstall"]["n_events"] += 1; sv["events"]["D4_new_postinstall"] += 1
            if (va.get("npm_user") or "") != (vb.get("npm_user") or ""):
                events["D5_maintainer"]["n_events"] += 1; sv["events"]["D5_maintainer"] += 1; quarterly[q] += 1
        per_server[d["server_id"]] = sv

    for k in events:
        events[k]["n_servers"] = sum(1 for sv in per_server.values() if sv["events"][k])
        events[k]["rate_per_pair"] = round(events[k]["n_events"] / pair_total, 4) if pair_total else 0

    return {"events": events, "per_server": per_server, "n_version_pairs": pair_total,
            "n_servers_analyzed": len(per_server), "events_by_quarter": dict(sorted(quarterly.items()))}


def rq4_publisher(known, packs, attest):
    """CI vs 人工发布分类 + 安全姿态交叉。"""
    summ = json.loads((OUT / "summary_v7.json").read_text(encoding="utf-8"))
    ok_ids = {r["server_id"] for r in summ["results"] if r.get("ok")}
    e7 = json.loads((OUT / "e7_report.json").read_text(encoding="utf-8"))
    viol = {}
    for row in (OUT / "e7_server_rule_matrix.csv").read_text(encoding="utf-8").splitlines()[1:]:
        parts = row.split(",")
        viol[parts[0]] = sum(int(x) for x in parts[1:len(e7["rules"]) + 1])

    pub = {}
    for d in known:
        pkg, sid = d["npm_package"], d["server_id"]
        p = packs.get(pkg) or {}
        latest_user = ""
        if p.get("time"):
            vs = [v for v in p["versions"] if v in p["time"] and "-" not in v]
            if vs:
                vs.sort(key=lambda v: p["time"][v])
                latest_user = p["versions"][vs[-1]].get("npm_user") or ""
        if attest.get(pkg) is True:
            t = "ci_provenance"
        elif any(m in (latest_user or "").lower() for m in BOT_MARKS):
            t = "ci_bot"
        elif latest_user:
            t = "manual"
        else:
            t = "unknown"
        pub[sid] = {"type": t, "latest_publisher": latest_user}

    groups = defaultdict(list)
    for sid, info in pub.items():
        groups[info["type"]].append(sid)
    cross = {}
    for t, sids in groups.items():
        n_ok = sum(1 for s in sids if s in ok_ids)
        vs = [viol[s] for s in sids if s in viol]
        cross[t] = {"n": len(sids), "f1_ok": n_ok, "f1_rate": round(n_ok / len(sids), 3),
                    "e7_violations_mean": round(statistics.mean(vs), 2) if vs else None,
                    "n_with_violations": sum(1 for v in vs if v > 0)}
    return {"classification": pub, "cross_with_posture": cross,
            "note": "provenance 仅可在 GitHub Actions/GitLab CI 等 OIDC 环境生成，故 ci_provenance 为 CI 直发权威信号；"
                    "ci_bot 由 _npmUser bot 标记启发；GitHub releases 作者层未采（匿名限额），作为可选增强 [待补充]"}


def spearman(a, b):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v); i = 0
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
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else 0.0


def main():
    known = json.loads((BASE / "registry" / "known_servers.json").read_text(encoding="utf-8"))
    print("=== E6+RQ4 补采 ===")
    packs = collect_packuments(known)
    attest = collect_attestations(known)
    print(f"  provenance 覆盖: {sum(1 for v in attest.values() if v is True)}/{len(known)} 包有 CI provenance")

    drift = drift_analysis(known, packs)
    rq4 = rq4_publisher(known, packs, attest)

    # RQ5 关联：per-server 总漂移事件 vs E7 违反数
    e7 = json.loads((OUT / "e7_report.json").read_text(encoding="utf-8"))
    viol = {}
    for row in (OUT / "e7_server_rule_matrix.csv").read_text(encoding="utf-8").splitlines()[1:]:
        parts = row.split(",")
        viol[parts[0]] = sum(int(x) for x in parts[1:len(e7["rules"]) + 1])
    drift_total = {s: sum(sv["events"].values()) for s, sv in drift["per_server"].items()}
    pairs = [(drift_total[s], viol[s]) for s in drift_total if s in viol]
    rho = round(spearman([p[0] for p in pairs], [p[1] for p in pairs]), 3) if len(pairs) >= 5 else None

    # 首次发布年龄（time.created earliest = time 字段里最早的，即包创建）
    first_pub = {}
    for d in known:
        p = packs.get(d["npm_package"]) or {}
        if p.get("time"):
            try:
                first = min(t for k, t in p["time"].items() if k not in ("created", "modified"))
                first_pub[d["server_id"]] = (TODAY - date.fromisoformat(first[:10])).days
            except Exception:
                pass
    ages = sorted(first_pub.values())
    median_age = ages[len(ages) // 2] if ages else None

    report = {
        "report": "E6_drift_and_RQ4_publisher_backfill",
        "collected_at": f"{TODAY.isoformat()}T00:00:00Z",
        "source": "npm full packument (trimmed cache) + npm attestations endpoint，单源可复现",
        "E6_drift": drift,
        "RQ4_publisher": rq4,
        "RQ5_enrichment": {
            "first_publish_age_days_median": median_age,
            "n_first_publish_known": len(first_pub),
            "note": "首次发布年龄已从 packument time 字段补齐，替代此前 latest-version 新鲜度代理",
            "drift_total_vs_e7_violations_spearman": rho,
            "n_pairs": len(pairs),
            "rho_interpretation": "漂移事件越多 E7 违反越多为正相关；关联非因果，且可能与版本总数（暴露量）混杂，需版本数偏相关检验",
        },
        "coverage_gaps": [
            "D2 能力漂移为 keywords 字面代理，AST/SBOM 级需逐版本源码采集 [部分覆盖]",
            "D1 描述漂移为 difflib 字面相似度 0.85 代理，语义级（模型）相似度未采，A5 消融另测",
            "GitHub releases 作者层未采（匿名 API 限额 60/h），CI 分类以 npm provenance + _npmUser 为准",
        ],
    }
    (OUT / "e6_rq4_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 回写 rq_report.json 的 RQ4 / RQ5
    rq_path = OUT / "rq_report.json"
    rq = json.loads(rq_path.read_text(encoding="utf-8"))
    rq["RQ4_supply_chain"]["ci_vs_manual"] = rq4["cross_with_posture"]
    rq["RQ4_supply_chain"]["ci_vs_manual_note"] = rq4["note"]
    rq["RQ5_evolution"]["drift_events"] = {
        k: v for k, v in drift["events"].items()
    }
    rq["RQ5_evolution"]["drift_events_note"] = "npm registry 元数据级漂移；AST/SBOM 级见 coverage_gaps"
    rq["RQ5_evolution"]["first_publish_age_days_median"] = median_age
    rq["RQ5_evolution"]["drift_total_vs_e7_violations_spearman"] = rho
    rq["RQ5_evolution"]["lifecycle_note"] = f"首次发布年龄中位 {median_age} 天（n={len(first_pub)}），已从 latest-version 新鲜度升级为真实包龄"
    rq["backfill"] = {"e6_rq4_report": "e6_rq4_report.json", "collected_at": report["collected_at"]}
    rq_path.write_text(json.dumps(rq, ensure_ascii=False, indent=2), encoding="utf-8")

    # 摘要
    print("\n" + "=" * 64)
    print("E6 漂移事件（npm 元数据级）")
    print("=" * 64)
    for k, v in drift["events"].items():
        print(f"  {k:<20} 事件 {v['n_events']:>4} | 影响 server {v['n_servers']:>2}/{drift['n_servers_analyzed']} | 每版本对 {v['rate_per_pair']*100:.1f}%")
    print(f"  版本对总数: {drift['n_version_pairs']}（{drift['n_servers_analyzed']} server，正式版本相邻对）")
    print(f"  漂移总量 × E7 违反数 Spearman rho={rho} (n={len(pairs)})")
    print("\n" + "=" * 64)
    print("RQ4 CI vs 人工发布")
    print("=" * 64)
    for t, c in sorted(rq4["cross_with_posture"].items()):
        print(f"  {t:<14} n={c['n']:>2} | F1={c['f1_rate']*100:.0f}% | E7 违反均值={c['e7_violations_mean']} | 有违反={c['n_with_violations']}")
    print(f"\n  首次发布年龄中位: {median_age} 天")
    print(f"→ {OUT / 'e6_rq4_report.json'}（rq_report.json 已回写）")
    return 0


if __name__ == "__main__":
    main()
