# -*- coding: utf-8 -*-
"""按 publisher 聚类的 bootstrap 置信区间（RQ1 集中度 + 可部署性差异）。

publisher 解析优先级：
  1) npm metadata author.name（归一化小写）
  2) repository URL 的 owner（github.com/owner/repo）
  3) npm scope（@scope）
  4) 该 server 自身作为单例簇

统计量：
  - gini(downloads, n=60)
  - gini(tools_per_server, n=32 deployable)
  - popular stratum（下载量 top-25%，n=15）与其余的可部署率差

输出 sandbox/output/publisher_bootstrap.json
"""
import hashlib
import json
import pathlib
import random
import re
from collections import Counter, defaultdict

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
REG_CACHE = BASE / "sandbox" / "cache" / "npm_registry.json"
B = 5000
SEED = 20261029


def entry_hash(pkg, ver):
    return hashlib.sha256(f"{pkg}@{ver}".encode()).hexdigest()[:16]


def gini(xs):
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if n == 0:
        return 0.0
    s = sum(xs)
    if s == 0:
        return 0.0
    # 标准公式: G = (2*sum(i*x_i)/(n*sum(x)) - (n+1)/n)
    return (2 * sum(i * x for i, x in enumerate(xs, 1)) / (n * s)) - (n + 1) / n


def repo_owner(v):
    if not v:
        return None
    if isinstance(v, dict):
        v = v.get("url") or ""
    v = str(v)
    m = re.search(r"github\.com[:/]([^/]+)/", v, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r"gitlab\.com[:/]([^/]+)/", v, re.I)
    if m:
        return m.group(1).lower()
    return None


def publisher_of(d, meta):
    a = meta.get("author")
    if isinstance(a, dict):
        a = a.get("name")
    if isinstance(a, str) and a.strip():
        name = re.sub(r"<.*?>", "", a).strip().lower()
        name = re.sub(r"\s+", " ", name)
        if name:
            return "author:" + name
    owner = repo_owner(meta.get("repository"))
    if owner:
        return "gh:" + owner
    pkg = d.get("npm_package") or ""
    if pkg.startswith("@"):
        return "scope:" + pkg[1:].split("/")[0].lower()
    return "solo:" + d["server_id"]


def pct(vals, q):
    vals = sorted(vals)
    if not vals:
        return None
    k = (len(vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 4)


def main():
    known = json.loads((BASE / "registry" / "known_servers.json").read_text(encoding="utf-8"))
    cache = json.loads(REG_CACHE.read_text(encoding="utf-8")) if REG_CACHE.exists() else {}
    summ = json.loads((OUT / "summary_v7.json").read_text(encoding="utf-8"))
    ok_ids = {r["server_id"] for r in summ["results"] if r.get("ok")}
    f4 = json.loads((OUT / "f4_all_summary_v1_superseded.json").read_text(encoding="utf-8"))
    tools_by = {sid: v["n"] for sid, v in f4["by_server"].items()}

    rows = []
    for d in known:
        meta = cache.get(entry_hash(d["npm_package"], d["npm_version"]), {})
        rows.append({
            "server_id": d["server_id"],
            "downloads": d.get("downloads_monthly") or 0,
            "ok": d["server_id"] in ok_ids,
            "tools": tools_by.get(d["server_id"], 0),
            "publisher": publisher_of(d, meta),
            "author": (meta.get("author") or {}).get("name") if isinstance(meta.get("author"), dict) else meta.get("author"),
        })

    # 流行度分层沿用 rq_analysis 口径：下载量 top-25%（n=15）
    srt = sorted(rows, key=lambda r: -r["downloads"])
    n_pop = max(1, int(len(srt) * 0.25))
    for i, r in enumerate(srt):
        r["popular"] = i < n_pop

    clusters = defaultdict(list)
    for r in rows:
        clusters[r["publisher"]].append(r)
    cluster_sizes = sorted((len(v) for v in clusters.values()), reverse=True)

    point = {
        "gini_downloads_all60": round(gini([r["downloads"] for r in rows]), 4),
        "gini_tools_ok": round(gini([r["tools"] for r in rows if r["ok"]]), 4),
        "deploy_rate_popular": round(sum(1 for r in rows if r["popular"] and r["ok"]) / n_pop, 4),
        "deploy_rate_rest": round(sum(1 for r in rows if not r["popular"] and r["ok"]) / (len(rows) - n_pop), 4),
    }
    point["deploy_diff"] = round(point["deploy_rate_popular"] - point["deploy_rate_rest"], 4)

    keys = list(clusters.keys())
    rng = random.Random(SEED)
    samples = defaultdict(list)
    valid_diff = 0
    for _ in range(B):
        picked = [keys[rng.randrange(len(keys))] for _ in range(len(keys))]
        boot = [r for k in picked for r in clusters[k]]
        if not boot:
            continue
        samples["gini_downloads"].append(gini([r["downloads"] for r in boot]))
        ok_tools = [r["tools"] for r in boot if r["ok"]]
        if ok_tools:
            samples["gini_tools"].append(gini(ok_tools))
        pop = [r for r in boot if r["popular"]]
        rest = [r for r in boot if not r["popular"]]
        if pop and rest:
            samples["deploy_diff"].append(
                sum(1 for r in pop if r["ok"]) / len(pop) - sum(1 for r in rest if r["ok"]) / len(rest))
            valid_diff += 1

    ci = {
        "gini_downloads_all60": [pct(samples["gini_downloads"], 0.025), pct(samples["gini_downloads"], 0.975)],
        "gini_tools_ok": [pct(samples["gini_tools"], 0.025), pct(samples["gini_tools"], 0.975)],
        "deploy_diff": [pct(samples["deploy_diff"], 0.025), pct(samples["deploy_diff"], 0.975)],
    }

    report = {
        "method": (f"cluster bootstrap by publisher, B={B}, seed={SEED}; "
                   "publishers resampled with replacement, all their servers kept together"),
        "n_servers": len(rows),
        "n_publishers": len(clusters),
        "cluster_size_distribution": dict(Counter(cluster_sizes)),
        "largest_clusters": [
            {"publisher": k, "servers": len(v), "server_ids": [r["server_id"] for r in v]}
            for k, v in sorted(clusters.items(), key=lambda kv: -len(kv[1]))[:6]
        ],
        "n_popular": n_pop,
        "point": point,
        "ci95": ci,
        "valid_diff_replicates": valid_diff,
        "publisher_resolution": {k: len(v) for k, v in clusters.items()},
        "per_server": [{k: r[k] for k in ("server_id", "publisher", "downloads", "ok", "tools", "popular")}
                       for r in rows],
    }
    (OUT / "publisher_bootstrap.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== publisher 聚类 bootstrap ===")
    print(f"  servers={len(rows)}  publishers={len(clusters)}  cluster sizes={report['cluster_size_distribution']}")
    for e in report["largest_clusters"]:
        print(f"    {e['publisher']}: {e['servers']} -> {', '.join(e['server_ids'])}")
    print(f"  gini(downloads)={point['gini_downloads_all60']} CI={ci['gini_downloads_all60']}")
    print(f"  gini(tools)={point['gini_tools_ok']} CI={ci['gini_tools_ok']}")
    print(f"  deploy popular={point['deploy_rate_popular']} rest={point['deploy_rate_rest']} "
          f"diff={point['deploy_diff']} CI={ci['deploy_diff']} (valid reps={valid_diff})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
