# -*- coding: utf-8 -*-
"""npm registry 抽样框构建（v0.1）。

为什么用 npm registry 作可复现抽样框：
  四聚合 registry 的公开枚举链路在当前时点均不可用或不可复现（实测 2026-10-22）：
    - PulseMCP  api.pulsemcp.com/v0beta/servers → HTTP 410 Gone
    - Glama     glama.ai/api/mcp/v1/servers   → HTTP 401（需 API key）
    - Smithery  CLI search 可用，但详情页 GitHub 链接 100% 404，无法回溯 artifact
    - mcp.so    HTML 可拉，但为 SPA，列表页几乎不含结构化字段
  npm registry 搜索 API 提供 name/version/published/description/keywords/downloads，
  且与分析单元"可独立部署的 MCP server"直接对应（可安装性可被客观验证）。

输出:
  registry/npm_frame.jsonl     全量候选（含排除标记）
  registry/known_servers.json  可用于容器化握手的种子（真实版本号，非构造）

用法: python registry_npm_frame.py [--per-query 250] [--top 40]
"""
import argparse
import json
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "registry"
OUT.mkdir(exist_ok=True)

QUERIES = [
    "mcp server",
    "mcp-server",
    "modelcontextprotocol server",
    "mcp tools server",
]

EXCLUDE_KW = {
    "sdk", "client", "framework", "awesome", "list", "directory", "inspector",
    "proxy", "gateway", "template", "boilerplate", "example", "demo",
    "transport", "toolkit", "library", "builder", "wrapper", "types",
    "create-", "-cli", "agent-install", "monorepo",
}

# 必须出现 server 语义信号，否则不视为"独立可部署的 MCP server"
SERVER_SIGNAL = re.compile(r"(^|[/_-])mcp([/_-]|$)|mcp server|model context protocol server", re.I)


def search(text, size):
    url = ("https://registry.npmjs.org/-/v1/search?text="
           + urllib.parse.quote(text) + f"&size={size}")
    req = urllib.request.Request(url, headers={"User-Agent": "mcp-ecosystem-measurement/0.3"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except Exception:
            time.sleep(2)
    return {"objects": []}


def stable_id(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


PCACHE = OUT / ".packument_cache"
PCACHE.mkdir(exist_ok=True)


def fetch_packument(name, version):
    """拉 registry.npmjs.org/{name}/{version}，读 bin/main/dependencies。带文件缓存。"""
    default = {"bin_entries": {}, "main": "", "has_mcp_sdk": False, "dep_count": 0}
    key = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{name}@{version}")
    f = PCACHE / f"{key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    url = f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@')}/{version}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mcp-ecosystem-measurement/0.3"})
        with urllib.request.urlopen(req, timeout=25) as r:
            doc = json.loads(r.read().decode("utf-8"))
    except Exception:
        return default
    bin_field = doc.get("bin") or {}
    bins = bin_field if isinstance(bin_field, dict) else {name: bin_field}
    deps = doc.get("dependencies") or {}
    meta = {
        "bin_entries": {k: v for k, v in bins.items()},
        "main": doc.get("main") or "",
        "has_mcp_sdk": any("modelcontextprotocol/sdk" in d or "model-context-protocol" in d
                           for d in deps),
        "dep_count": len(deps),
    }
    try:
        f.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-query", type=int, default=250)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--no-verify", action="store_true", help="跳过 packument 校验（快但污染）")
    args = ap.parse_args()

    seen = {}
    raw_counts = {}
    for q in QUERIES:
        data = search(q, args.per_query)
        objs = data.get("objects", [])
        raw_counts[q] = len(objs)  # 各查询原始返回数（去重前），供复算重复候选数
        print(f"[npm] query={q!r} → {len(objs)} objects", flush=True)
        for o in objs:
            p = o.get("package", {})
            name = p.get("name", "")
            if not name:
                continue
            if name in seen:
                seen[name]["queries"].append(q)  # 记录返回该包的全部查询
                continue
            kws = [k.lower() for k in (p.get("keywords") or [])]
            desc = (p.get("description") or "").strip()
            blob = (name + " " + desc + " " + " ".join(kws)).lower()
            if "mcp" not in blob and "modelcontextprotocol" not in blob:
                continue
            seen[name] = {
                "npm_package": name,
                "npm_version": p.get("version", ""),
                "description": desc,
                "keywords": kws,
                "published": p.get("date", ""),
                "downloads_monthly": o.get("downloads", {}).get("monthly", 0),
                "search_score": o.get("searchScore", 0),
                "queries": [q],
            }
        time.sleep(1.0)

    # 纳入/排除：可部署 server vs SDK/框架/传输层/清单
    rows = []
    for name, rec in seen.items():
        low = (name + " " + rec["description"] + " " + " ".join(rec["keywords"])).lower()
        reasons = []
        hit = [k for k in EXCLUDE_KW if k in low]
        if hit:
            reasons.append("artifact_type=" + ",".join(sorted(hit)[:3]))
        if not SERVER_SIGNAL.search(name) and "mcp server" not in low and "mcp-server" not in low:
            reasons.append("no server semantics")
        if rec["downloads_monthly"] == 0:
            reasons.append("zero downloads (unused artifact)")
        rec["included"] = not reasons
        rec["exclusion_reason"] = "; ".join(reasons)
        rec["server_id"] = stable_id(name)
        rows.append(rec)

    # 客观可部署性判定：拉 package.json，看 bin 可执行入口 + MCP SDK 依赖
    # （"独立可部署"不靠描述关键词，靠可验证的包元数据）
    if not args.no_verify:
        targets = [r for r in rows if r["included"]]
        print(f"[verify] fetching package metadata × {len(targets)}", flush=True)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            metas = list(ex.map(lambda r: fetch_packument(r["npm_package"], r["npm_version"]), targets))
        for r, m in zip(targets, metas):
            r.update(m)
            # 硬判据 = bin 可执行入口（客观、可验证）；SDK 依赖只作属性记录
            # 实测 server-filesystem / @playwright/mcp 等真实 server 不直接依赖 sdk
            if not m["bin_entries"]:
                r["included"] = False
                r["exclusion_reason"] = "no bin entry (library, not deployable server)"

    rows.sort(key=lambda r: (-r["downloads_monthly"], r["npm_package"]))
    with open(OUT / "npm_frame.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    seeds = [r for r in rows if r["included"] and r["npm_version"]][:args.top]
    seed_list = [{
        "server_id": r["server_id"],
        "npm_package": r["npm_package"],
        "npm_version": r["npm_version"],
        "source": "npm_registry_search",
        "downloads_monthly": r["downloads_monthly"],
        "published": r["published"],
    } for r in seeds]
    (OUT / "known_servers.json").write_text(
        json.dumps(seed_list, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "candidates": len(rows),
        "raw_per_query": raw_counts,
        "cross_query_duplicates": sum(raw_counts.values()) - len(rows),
        "included": sum(1 for r in rows if r["included"]),
        "excluded": sum(1 for r in rows if not r["included"]),
        "seeds": len(seed_list),
        "frame": str(OUT / "npm_frame.jsonl"),
        "seeds_file": str(OUT / "known_servers.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
