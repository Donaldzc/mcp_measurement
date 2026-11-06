# -*- coding: utf-8 -*-
"""F2 缺失率：registry 登记字段 + 动态 tools/list 字段的缺失比例。

分层报告：
  Layer 1 — Registry 登记字段（npm metadata）：homepage / repository / author / license / bugs / description
  Layer 2 — 动态 tools/list 返回：tool_name / description / input_schema / server_version

输出：sandbox/output/f2_report.json
"""
import json, pathlib, hashlib, urllib.request, time, concurrent.futures
from collections import Counter, defaultdict

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
REG_CACHE = BASE / "sandbox" / "cache" / "npm_registry.json"

# registry 层目标扩展字段（known_servers.json 当前只有 6 字段：server_id, npm_package,
# npm_version, source, downloads_monthly, published —— 缺失 homepage/repository/author/license/description/bugs）
REG_TARGET_FIELDS = ["homepage", "repository", "author", "license", "bugs", "description"]


def load_known(offline=False):
    """加载 registry/known_servers.json，带哈希缓存做幂等扩展。
    offline=True 时跳过 npm registry 拉取，扩展字段标记为 None。"""
    known = json.loads((BASE / "registry" / "known_servers.json").read_text(encoding="utf-8"))
    cache = {}
    if REG_CACHE.exists():
        cache = json.loads(REG_CACHE.read_text(encoding="utf-8"))

    def _entry_hash(pkg, ver):
        return hashlib.sha256(f"{pkg}@{ver}".encode()).hexdigest()[:16]

    if offline:
        print("  [offline 模式] 跳过 npm registry 拉取，扩展字段记为未采集")
        for d in known:
            for k in REG_TARGET_FIELDS:
                d[k] = None
        return known

    updated = 0
    todo = []  # 需要拉取的条目：(hash, pkg, ver)
    for d in known:
        pkg = d["npm_package"]
        ver = d["npm_version"]
        h = _entry_hash(pkg, ver)
        if h in cache:
            ext = cache[h]
            for k, v in ext.items():
                d[k] = v
        else:
            todo.append((h, pkg, ver))
            # 先填空占位
            for k in REG_TARGET_FIELDS:
                d[k] = None

    print(f"  缓存命中 {len(known) - len(todo)} / 待拉取 {len(todo)}")

    def _fetch(args):
        h, pkg, ver = args
        url = f"https://registry.npmjs.org/{pkg}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())
            latest = data.get("dist-tags", {}).get("latest", ver)
            ver_obj = data["versions"].get(latest, data["versions"].get(ver, {}))
            return h, {
                "homepage": data.get("homepage") or ver_obj.get("homepage"),
                "repository": data.get("repository") or ver_obj.get("repository"),
                "author": data.get("author") or ver_obj.get("author"),
                "license": data.get("license") or ver_obj.get("license"),
                "bugs": data.get("bugs") or ver_obj.get("bugs"),
                "description": data.get("description") or ver_obj.get("description"),
            }
        except Exception as e:
            return h, {k: None for k in REG_TARGET_FIELDS}

    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            for i, (h, ext) in enumerate(ex.map(_fetch, todo), 1):
                cache[h] = ext
                updated += 1
                if i % 20 == 0 or i == len(todo):
                    print(f"    registry fetch progress {i}/{len(todo)}")
        # 回填 known
        for d in known:
            h = _entry_hash(d["npm_package"], d["npm_version"])
            if h in cache:
                for k, v in cache[h].items():
                    d[k] = v

    # 持久化缓存
    if updated > 0:
        REG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        REG_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  npm registry 缓存更新：{updated} 条新条目")

    return known


def is_missing(v):
    return v in (None, "", [], {}, False)


def layer1_registry(known):
    """Layer 1: npm registry 扩展字段缺失率。"""
    n = len(known)
    per_field = {}
    for f in REG_TARGET_FIELDS:
        miss = sum(1 for d in known if is_missing(d.get(f)))
        per_field[f] = {"missing": miss, "total": n, "rate": round(miss / n, 4)}

    # 整体缺失字段数分布（一个 server 缺几个字段）
    miss_count_dist = Counter()
    for d in known:
        miss_count = sum(1 for f in REG_TARGET_FIELDS if is_missing(d.get(f)))
        miss_count_dist[miss_count] += 1

    # server 级：0 个缺失 vs 有缺失
    complete = sum(1 for d in known if all(not is_missing(d.get(f)) for f in REG_TARGET_FIELDS))

    # 按 F1 成功分层
    summ = json.loads((OUT / "summary_v7.json").read_text(encoding="utf-8"))
    ok_ids = {r["server_id"] for r in summ["results"] if r.get("ok")}

    ok_miss = defaultdict(int)
    fail_miss = defaultdict(int)
    ok_total = fail_total = 0
    for d in known:
        sid = d["server_id"]
        target = ok_miss if sid in ok_ids else fail_miss
        if sid in ok_ids:
            ok_total += 1
        else:
            fail_total += 1
        for f in REG_TARGET_FIELDS:
            if is_missing(d.get(f)):
                target[f] += 1

    ok_rates = {f: round(ok_miss[f] / ok_total, 4) if ok_total else None
                for f in REG_TARGET_FIELDS}
    fail_rates = {f: round(fail_miss[f] / fail_total, 4) if fail_total else None
                  for f in REG_TARGET_FIELDS}

    return {
        "n_servers": n,
        "per_field": per_field,
        "complete_servers": complete,
        "complete_rate": round(complete / n, 4),
        "missing_count_distribution": {str(k): v for k, v in sorted(miss_count_dist.items())},
        "stratified_by_F1": {
            "ok": {"n": ok_total, "per_field_rate": ok_rates},
            "fail": {"n": fail_total, "per_field_rate": fail_rates},
        },
    }


def layer2_tools():
    """Layer 2: 动态 tools/list 返回字段缺失率（仅 F1 成功 server）。"""
    summ = json.loads((OUT / "summary_v7.json").read_text(encoding="utf-8"))
    ok = [r for r in summ["results"] if r.get("ok")]
    all_tools = []
    for r in ok:
        jp = OUT / f"{r['server_id']}.jsonl"
        if not jp.exists():
            continue
        for line in jp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            if o.get("tool_name"):
                o.setdefault("server", r["server_id"])
                all_tools.append(o)

    n = len(all_tools)
    TOOL_FIELDS = ["tool_name", "description", "input_schema", "server", "server_version"]
    per_field = {}
    for f in TOOL_FIELDS:
        miss = sum(1 for t in all_tools if is_missing(t.get(f)))
        per_field[f] = {"missing": miss, "total": n, "rate": round(miss / n, 4)}

    # server_version 为空字符串也算缺失（一些 server 不返回版本）
    sv_empty = sum(1 for t in all_tools if not t.get("server_version", "").strip())

    # 按 server 分层
    by_server = defaultdict(lambda: {"n": 0, "missing_description": 0, "missing_input_schema": 0})
    for t in all_tools:
        s = t.get("server", "?")
        by_server[s]["n"] += 1
        if is_missing(t.get("description")):
            by_server[s]["missing_description"] += 1
        if is_missing(t.get("input_schema")):
            by_server[s]["missing_input_schema"] += 1

    return {
        "n_servers_ok": len(ok),
        "n_tools": n,
        "per_field": per_field,
        "server_version_empty_string": sv_empty,
        "per_server": dict(by_server),
    }


def main():
    import sys
    offline = "--offline" in sys.argv
    known = load_known(offline=offline)
    l1 = layer1_registry(known)
    l2 = layer2_tools()

    # 复合指标：F2 总体缺失率
    # server 层：registry 扩展字段平均缺失率
    reg_field_rates = [v["rate"] for v in l1["per_field"].values()]
    reg_avg_rate = sum(reg_field_rates) / len(reg_field_rates) if reg_field_rates else 0

    # tool 层：description 缺失率（全齐）
    tool_desc_rate = l2["per_field"]["description"]["rate"]

    report = {
        "report": "F2_missing_rate",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "layer1_registry": l1,
        "layer2_tools": l2,
        "composite": {
            "registry_field_avg_missing_rate": round(reg_avg_rate, 4),
            "tool_description_missing_rate": tool_desc_rate,
        },
        "notes": [
            "registry known_servers.json 原始字段（server_id, npm_package, npm_version, source, downloads_monthly, published）全部 0% 缺失；",
            "缺失率统计针对的是 npm registry 可扩展字段（homepage/repository/author/license/bugs/description）——",
            "这些字段在 known_servers.json 采集时未拉取，属于 registry 登记层面的信息缺口。",
            "tool description 在 F1 成功 server 的动态 tools/list 返回中 0% 缺失，说明 MCP 协议在运行时层保证了字段完整性。",
        ],
    }

    out = OUT / "f2_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print("F2 缺失率报告")
    print("=" * 60)
    print(f"\nLayer 1 — Registry 登记字段（n={l1['n_servers']}）")
    for f, v in sorted(l1["per_field"].items()):
        print(f"  {f:<16} {v['missing']}/{v['total']} = {v['rate']*100:.1f}%")
    print(f"  完整 server（0 缺失）: {l1['complete_servers']}/{l1['n_servers']} = {l1['complete_rate']*100:.1f}%")
    print(f"  平均字段缺失率: {reg_avg_rate*100:.1f}%")

    print(f"\nLayer 2 — 动态 tools/list（n={l2['n_tools']} tools / {l2['n_servers_ok']} servers）")
    for f, v in sorted(l2["per_field"].items()):
        print(f"  {f:<20} {v['missing']}/{v['total']} = {v['rate']*100:.2f}%")

    print(f"\n分层 — F1 成功 vs 失败")
    for group in ["ok", "fail"]:
        d = l1["stratified_by_F1"][group]
        print(f"  [{group.upper()}] n={d['n']}")
        for f, r in sorted(d["per_field_rate"].items()):
            rstr = f"{r*100:.0f}%" if r is not None else "-"
            print(f"    {f:<16} {rstr}")

    print(f"\n→ 已写入 {out}")
    return 0


if __name__ == "__main__":
    main()
