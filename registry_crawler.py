# -*- coding: utf-8 -*-
"""MCP registry 枚举与聚合脚本。

支持来源:
  [1] Smithery CLI: npx --yes smithery mcp search --json --page N
      字段: name, qualifiedName (namespace/name), description, useCount
      分页: --page N (返回 hasMore bool)
  [2] mcp.so: HTML 列表 https://mcp.so/servers?page=N
      字段: name, description, author, category, links (Homepage/Repository)
  [3] GitHub 反查: 从 qualifiedName 或 mcp.so 的 Repository 链接拉 package.json
      字段: name (npm), version, dependencies, scripts

统一输出: registry_corpus.jsonl，每行一个 server（多源聚合），字段:
  server_id, canonical_name, sources, registry_entries[n], github_repo,
  npm_package, npm_version, py_package, transport, category, verified,
  use_count, description, included (按 v2 纳入/排除规则)

分层抽样: --stratified 按 source × transport × language × publisher_type 分层
          --sample N 每层抽 N 个，总量向上取整

安全: 全静态爬（HTML + GitHub API），不执行任何 server；不写凭证文件。
"""
import argparse
import csv
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

BASE = pathlib.Path(__file__).resolve().parent
DATA = BASE / "data"
OUT = BASE / "registry"
OUT.mkdir(exist_ok=True)


def http(url, token=None):
    UA = {"User-Agent": "mcp-registry-measurement/0.1", "Accept": "application/json,text/html"}
    if token:
        UA["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ct = r.headers.get("Content-Type", "")
            raw = r.read().decode("utf-8", errors="replace")
            if "json" in ct:
                return json.loads(raw)
            return raw
    except urllib.error.HTTPError as e:
        return None
    except Exception:
        return None


def _gh_token():
    t = os.environ.get("GITHUB_TOKEN", "")
    if t:
        return t
    f = BASE.parent / ".env.local"
    if f.exists():
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if line.startswith("GITHUB_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def fetch_package_json(repo_full):
    """从 GitHub Contents API 拉 package.json（可选 pyproject.toml 留扩展）。"""
    gh = http(f"https://api.github.com/repos/{repo_full}/contents/package.json", _gh_token())
    if not gh or gh.get("encoding") != "base64":
        return None
    import base64
    try:
        return json.loads(base64.b64decode(gh["content"]).decode("utf-8", errors="replace"))
    except Exception:
        return None


def _run(cmd):
    import shutil
    # Windows: resolve .cmd for npx/node
    resolved = []
    for i, tok in enumerate(cmd):
        if i == 0 and shutil.which(tok):
            resolved.append(shutil.which(tok))
        else:
            resolved.append(tok)
    try:
        return subprocess.run(resolved, capture_output=True, text=True, timeout=60,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return None


# ---------- Source 1: Smithery CLI ----------

# ---------- Smithery 详情页补 GitHub repo ----------

_SMITHERY_REPO_RE = re.compile(r'https?://github\.com/([^/"\\<>]+/[^/"\\<>]+)', re.I)


def crawl_smithery_detail(qualified_name):
    """从 smithery.ai/server/{qn} 抓 GitHub repo 链接。"""
    url = f"https://smithery.ai/server/{qualified_name}"
    raw = http(url)
    if not raw or not isinstance(raw, str):
        return None
    m = _SMITHERY_REPO_RE.search(raw)
    if m:
        repo = m.group(1).rstrip("/\\")
        # 排除非 repo 的 github.com 路径（如 github.com/sponsors/...）
        if repo.count("/") == 1:
            return repo
    return None


def crawl_smithery(max_pages=5):
    out = []
    for page in range(1, max_pages + 1):
        r = _run(["npx", "--yes", "smithery", "--json", "mcp", "search", "--page", str(page), "mcp"])
        if not r or r.returncode not in (0, 1):
            break
        text = r.stdout + "\n" + r.stderr
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0:
            break
        try:
            data = json.loads(text[start:end + 1])
        except Exception:
            break
        for s in data.get("servers", []):
            rec = {
                "source": "smithery",
                "qualified_name": s.get("qualifiedName", ""),
                "name": s.get("name", ""),
                "description": s.get("description", ""),
                "use_count": s.get("useCount", 0),
            }
            # 详情页补 GitHub repo（列表页没有）
            qn = rec["qualified_name"]
            if qn:
                repo = crawl_smithery_detail(qn)
                if repo:
                    rec["github_repo"] = repo
                    time.sleep(0.6)  # 详情页限速
            out.append(rec)
        if not data.get("hasMore"):
            break
        time.sleep(1.0)
    return out


# ---------- Source 2: mcp.so HTML ----------

MCP_SO_LINK_RE = re.compile(r'href="(https://github\.com/[^"/]+/[^"/"]+)"', re.I)
MCP_SO_NAME_RE = re.compile(r'<h3[^>]*class="[^"]*text-[^"]*font-semibold[^"]*"[^>]*>([^<]+)</h3>|<h3[^>]*>([^<]+)</h3>', re.I)
MCP_SO_DESC_RE = re.compile(r'<p[^>]*class="[^"]*text-[^"]*text-slate[^"]*"[^>]*>([^<]+)</p>', re.I)


def crawl_mcp_so(max_pages=5):
    out = []
    for page in range(1, max_pages + 1):
        raw = http(f"https://mcp.so/servers?page={page}")
        if not raw or not isinstance(raw, str) or len(raw) < 500:
            break
        repos = list(set(m.group(1) for m in MCP_SO_LINK_RE.finditer(raw)))
        for repo in repos:
            out.append({"source": "mcp_so", "github_repo": repo})
        time.sleep(1.0)
    return out


# ---------- 聚合与分层抽样 ----------

def qualify(candidate):
    """纳入/排除规则（v2 设计第二章）。"""
    desc = (candidate.get("description", "") or "").lower()
    qn = (candidate.get("qualified_name", "") or "").lower()
    repo = (candidate.get("github_repo", "") or "").lower()
    # 排除清单
    for kw, reason in [
        ("awesome", "awesome 清单"),
        ("headroom", "代理/中继层非 server"),
        ("private-gpt", "完整应用非 MCP server"),
        ("proxy", "代理层"),
        ("gateway", "网关层"),
        ("sandbox", "沙箱层"),
    ]:
        if kw in desc or kw in qn or kw in repo:
            candidate["included"] = False
            candidate["exclusion_reason"] = reason
            return candidate
    candidate["included"] = True
    return candidate


def aggregate(smithery_records, mcpso_records):
    """按 qualified_name / github_repo 聚合多源条目。"""
    entities = {}

    # Smithery → server_id 用 qualifiedName
    for r in smithery_records:
        sid = "smithery_" + (r["qualified_name"].replace("/", "_").lower()
                             or r["name"].lower().replace(" ", "_"))
        entities.setdefault(sid, {"sources": [], "registry_entries": []})
        e = entities[sid]
        e["sources"].append("smithery")
        e["registry_entries"].append({
            "registry": "smithery", "qualified_name": r["qualified_name"],
            "description": r["description"], "use_count": r["use_count"],
        })
        e.setdefault("canonical_name", r["name"])
        e.setdefault("description", r["description"])
        # 尝试从 qualifiedName 推 GitHub repo: namespace/name → namespace/name（很多 smithery server 就是 GitHub org/repo）
        if r["qualified_name"] and r["qualified_name"].count("/") == 1:
            e.setdefault("github_repo", r["qualified_name"])

    # mcp.so → server_id 用 github_repo
    for r in mcpso_records:
        repo = r.get("github_repo", "")
        if not repo:
            continue
        sid = "gh_" + repo.replace("/", "_").lower()
        entities.setdefault(sid, {"sources": [], "registry_entries": []})
        e = entities[sid]
        e["sources"].append("mcp_so")
        e["registry_entries"].append({
            "registry": "mcp_so", "github_repo": repo,
        })
        e.setdefault("canonical_name", repo.split("/", 1)[-1])
        e.setdefault("github_repo", repo)

    # 去重：若 smithery 的 qualifiedName 恰好等于某个 mcp_so 的 github_repo，合并
    merged = {}
    used = set()
    for sid, e in entities.items():
        if sid in used:
            continue
        repo_a = e.get("github_repo", "")
        if repo_a and repo_a.count("/") == 1:
            # 找另一个 sid 是 smithery_{repo_a} 的实体
            merge_target = f"smithery_{repo_a.replace('/', '_').lower()}"
            if merge_target in entities and merge_target != sid and merge_target not in used:
                other = entities[merge_target]
                for k, v in other.items():
                    if k in ("sources", "registry_entries"):
                        e[k].extend(v[k])
                    elif not e.get(k):
                        e[k] = v[k]
                used.add(merge_target)
        merged[sid] = e
    return merged


def enrich_package_json(entity):
    repo = entity.get("github_repo")
    if not repo:
        return entity
    pkg = fetch_package_json(repo)
    if pkg:
        entity["npm_package"] = pkg.get("name", "")
        entity["npm_version"] = pkg.get("version", "")
        entity["py_package"] = ""  # 留扩展：pypi_name 反查
        deps = {}
        for d in (pkg.get("dependencies") or {}, pkg.get("devDependencies") or {}):
            deps.update(d)
        mcp_sdks = [k for k in deps if "mcp" in k.lower() or "modelcontext" in k.lower()]
        entity["mcp_sdk_deps"] = mcp_sdks
        entity["transport_hint"] = "stdio" if any("@modelcontextprotocol" in s for s in mcp_sdks) else ""
    return entity


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smithery-pages", type=int, default=5)
    ap.add_argument("--mcpso-pages", type=int, default=3)
    ap.add_argument("--out", default=str(OUT / "registry_corpus.jsonl"))
    ap.add_argument("--aggregate-only", action="store_true", help="跳过枚举，只聚合已有 registry_corpus.jsonl")
    args = ap.parse_args()

    smithery = []
    mcpso = []
    if not args.aggregate_only:
        print(f"[smithery] crawling {args.smithery_pages} pages...", flush=True)
        smithery = crawl_smithery(args.smithery_pages)
        print(f"  → {len(smithery)} records", flush=True)
        print(f"[mcp.so] crawling {args.mcpso_pages} pages...", flush=True)
        mcpso = crawl_mcp_so(args.mcpso_pages)
        print(f"  → {len(mcpso)} records", flush=True)

    merged = aggregate(smithery, mcpso)
    print(f"[aggregate] {len(merged)} unique entities", flush=True)

    # 包名反查（抽样 200 个，全量限额够但慢）
    enriched = {}
    for i, (sid, e) in enumerate(merged.items()):
        if i % 20 == 0:
            print(f"[enrich] {i}/{len(merged)}", flush=True)
        e = enrich_package_json(e)
        e = qualify(e)
        e["server_id"] = sid
        enriched[sid] = e
        time.sleep(0.3)  # 限速

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(exist_ok=True)
    included = sum(1 for e in enriched.values() if e.get("included"))
    with open(out_path, "w", encoding="utf-8") as f:
        for e in enriched.values():
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # summary CSV
    summary = pathlib.Path(args.out).with_name("registry_summary.csv")
    with open(summary, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["server_id", "canonical_name", "sources", "included",
                     "exclusion_reason", "github_repo", "npm_package",
                     "transport_hint", "mcp_sdk_deps", "description"])
        for e in enriched.values():
            w.writerow([e["server_id"], e.get("canonical_name", ""),
                        ",".join(e.get("sources", [])),
                        e.get("included", ""), e.get("exclusion_reason", ""),
                        e.get("github_repo", ""), e.get("npm_package", ""),
                        e.get("transport_hint", ""),
                        ",".join(e.get("mcp_sdk_deps", [])),
                        (e.get("description", "") or "")[:120]])

    print(json.dumps({
        "entities_total": len(enriched),
        "entities_included": included,
        "entities_excluded": len(enriched) - included,
        "output": str(out_path),
        "summary": str(summary),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
