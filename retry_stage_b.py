# -*- coding: utf-8 -*-
"""Retry stage B (container handshake) for servers that timeout'd in the first run.

读入:
  sandbox/output/summary.json   首轮结果
  registry/known_servers.json   server_id → npm_package/npm_version 映射
  sandbox_cache/deps/{sid}/     已装好的 node_modules（跳过 stage A'）

输出:
  sandbox/output/{sid}.jsonl    每个 server 的 tools/list 结果
  sandbox/output/summary_v2.json  merge 后的完整结果（保留 run1 对比）

与首轮差异:
  - 握手器超时已从 60s → 120s（handshake_client.js 已更新）
  - 容器间 sleep 3s 让 Docker overlayfs 释放
  - 剔除平台不兼容（Windows .exe 在 Linux 容器跑不了）
  - 对明显需 API 凭证的 server 单独标记
"""
import json, pathlib, shutil, subprocess, sys, time

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
CACHE = BASE / "sandbox_cache"
DEPS = CACHE / "deps"
IMAGE = "mcp-sandbox:0.1"

NPM = shutil.which("npm") or shutil.which("npm.cmd") or "npm"
DOCKER = shutil.which("docker") or "docker"

# 平台不兼容: Windows .exe / .cmd 在 Linux 容器跑不了
PLATFORM_INCOMPAT = {
    "azure_mcp_win32_x64",   # azmcp.exe
}

# 高概率需要 API 凭证的 server（即使握手成功也可能 hang）
# 首轮 19 个成功的 server 里没有这些，说明它们确实可能因缺少凭证而挂起
LIKELY_NEED_CREDENTIALS = {
    "notionhq_notion_mcp_server",   # Notion API token
    "firecrawl_mcp",                 # Firecrawl API key
    "sentry_mcp_server",             # Sentry DSN / token
    "supabase_mcp_server_supabase",  # Supabase anon key / url
    "salesforce_mcp",                # Salesforce OAuth
    "hubspot_mcp_server",            # HubSpot API key
    "zereight_mcp_gitlab",           # GitLab token
    "rea_agents",                    # Rea API
    "sap_ux_fiori_mcp_server",       # SAP BTP destination
    "azure_mcp",                     # Azure AD
    "azure_mcp_linux_x64",           # 同 azure_mcp
}


def find_entry(pkg_root):
    """从 package.json 找 bin/main/dist/index.js（与 sandbox_handshake_runner.py 一致）。"""
    pkg_json = pkg_root / "package.json"
    if not pkg_json.exists():
        return None
    pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
    pkg_name = pkg.get("name", pkg_root.name)
    bin_field = pkg.get("bin")
    if bin_field:
        if isinstance(bin_field, dict):
            entry = list(bin_field.values())[0]
        else:
            entry = bin_field
        return f"{pkg_name}/{entry}"
    main = pkg.get("main")
    if main:
        return f"{pkg_name}/{main}"
    for guess in ["dist/index.js", "src/index.js", "index.js", "dist/server.js"]:
        if (pkg_root / guess).exists():
            return f"{pkg_name}/{guess}"
    return None


def docker_run(server_id, deps_dir, entry_rel):
    """禁网容器直接 node 启动。"""
    inner = ["node", "/opt/handshake/handshake_client.js",
             f"/opt/output/{server_id}.jsonl",
             "node", f"/opt/deps/node_modules/{entry_rel}"]
    docker = [
        DOCKER, "run", "--rm",
        "--network=none", "--read-only",
        "--tmpfs", "/tmp:rw,size=64m,mode=1777",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=1", "--memory=512m", "--pids-limit=50",
        "--stop-timeout", "180",    # 握手器 120s + 容器余量
        "-e", "PATH=/usr/local/bin:/usr/bin:/bin",
        "-v", f"{deps_dir / 'node_modules'}:/opt/deps/node_modules:ro",
        "-v", f"{OUT}:/opt/output",
        IMAGE,
    ] + inner
    try:
        return subprocess.run(docker, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None


def tail(text, n=300):
    t = (text or "").strip().splitlines()
    return " | ".join(t[-3:])[:n]


def main():
    # 1) 读首轮 summary
    summary_path = OUT / "summary.json"
    s = json.loads(summary_path.read_text(encoding="utf-8"))

    # 2) 筛 handshake_error 条目 + 剔除平台不兼容
    to_retry = []
    skipped = []
    for r in s.get("results", []):
        sid = r["server_id"]
        if r.get("ok"):
            continue
        reason = r.get("reason", "")
        if "handshake_error" in reason:
            if sid in PLATFORM_INCOMPAT:
                skipped.append({"server_id": sid, "reason": "platform_incompatible"})
            else:
                to_retry.append(r)

    print(f"[retry v2] handshake_error × {len(to_retry)}, 跳过 × {len(skipped)}")
    for sk in skipped:
        print(f"  SKIP [{sk['reason']}] {sk['server_id']}")

    # 3) 逐个 docker run + sleep 3s
    new_results = {}
    for i, r in enumerate(to_retry):
        sid = r["server_id"]
        deps_dir = DEPS / sid
        pkg_ver = r.get("pkg_ver", "")
        pkg_name = pkg_ver.rsplit("@", 1)[0] if pkg_ver else sid

        # 从 deps 目录构造 entry_rel
        pkg_root = deps_dir / "node_modules" / pkg_name
        if not pkg_root.exists():
            # 有些 server 的 deps 目录名 = server_id（非 package name）
            pkg_root = deps_dir / "node_modules" / sid
        entry_rel = find_entry(pkg_root) if pkg_root.exists() else None

        print(f"  [{i+1}/{len(to_retry)}] {sid} ...", end=" ", flush=True)

        if not entry_rel:
            print("ENTRY_UNKNOWN (deps dir missing?)")
            new_results[sid] = {"server_id": sid, "ok": False, "n_tools": 0,
                                "reason": "entry_unknown (deps not found)"}
            continue

        r2 = docker_run(sid, deps_dir, entry_rel)
        ok = False
        n_tools = 0
        reason = ""

        if r2 is None:
            reason = "container_timeout_180s"
            print("TIMEOUT 180s")
        elif r2.returncode != 0:
            reason = f"container_rc_{r2.returncode}: {tail(r2.stderr or r2.stdout)}"
            print(f"FAIL rc={r2.returncode}")
        else:
            out = OUT / f"{sid}.jsonl"
            if out.exists() and out.stat().st_size > 0:
                lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
                real = [l for l in lines if '"error"' not in l]
                n_tools = len(real)
                if n_tools > 0:
                    ok = True
                    print(f"OK {n_tools} tools")
                else:
                    err = next((l for l in lines if '"error"' in l), "")
                    reason = "handshake_error: " + err[:300]
                    print("ERRORS ONLY")
            else:
                reason = "no_output_file"
                print("NO OUTPUT")

        # 标记可能需凭证
        if not ok and sid in LIKELY_NEED_CREDENTIALS:
            reason = (reason + " | likely_requires_api_credentials").strip()

        new_results[sid] = {
            "server_id": sid, "ok": ok, "n_tools": n_tools,
            "pkg_ver": pkg_ver, "reason": reason,
        }
        time.sleep(10)   # 让 Docker overlayfs + 内存彻底释放（Windows 主机上需要更长）

    # 4) merge 回 summary_v2.json
    merged = list(s.get("results", []))
    for i, r in enumerate(merged):
        sid = r["server_id"]
        if sid in new_results:
            merged[i] = new_results[sid]
    # 追加 skipped（平台不兼容）
    for sk in skipped:
        sid = sk["server_id"]
        if not any(r["server_id"] == sid for r in merged):
            merged.append({
                "server_id": sid, "ok": False, "n_tools": 0,
                "reason": sk["reason"],
            })
    # 追加 install_failures
    for f in s.get("install_failures", []):
        sid = f["server_id"]
        if not any(r["server_id"] == sid for r in merged):
            merged.append({
                "server_id": sid, "ok": False, "n_tools": 0,
                "pkg_ver": f.get("pkg_ver", ""),
                "reason": "install_failed: " + f.get("detail", "")[:200],
            })

    n_ok = sum(1 for r in merged if r.get("ok"))
    n_total = len(merged)
    s2 = {
        "run_ts": s.get("run_ts"),
        "retry_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "retry_n": len(to_retry),
        "retry_ok": sum(1 for r in new_results.values() if r.get("ok")),
        "retry_skip": len(skipped),
        "n_attempted": n_total,
        "n_ok": n_ok,
        "n_fail": n_total - n_ok,
        "sandbox_config": s.get("sandbox_config", {}),
        "results": merged,
    }
    (OUT / "summary_v2.json").write_text(
        json.dumps(s2, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[done retry v2] +{sum(1 for r in new_results.values() if r.get('ok'))}/{len(to_retry)} → summary_v2.json")
    print(f"  F1 首轮: {s.get('n_ok', 0)}/{s.get('n_attempted', 0)} = {s.get('n_ok', 0)/s.get('n_attempted', 1):.3f}")
    print(f"  F1 v2:  {n_ok}/{n_total} = {n_ok/n_total:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
