# -*- coding: utf-8 -*-
"""Retry servers with fixed credentials/args.

针对 10 个可修复 server + 7 个 transcend ESM server 定制化重跑。
每个 server 单独配置: extra_env, extra_args
"""
import json, pathlib, shutil, subprocess, sys, time

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
CACHE = BASE / "sandbox_cache"
DEPS = CACHE / "deps"
IMAGE = "mcp-sandbox:0.1"
DOCKER = shutil.which("docker") or "docker"

# === Server-specific overrides ===
# env 会注入到容器，args 会加到 node <entry> 后面
# 如果 server 在 Linux 容器里能用，env/args 就填容器内的值
OVERRIDES = {
    # needs_credentials: 假凭证让 server 启动到 stdio 监听
    "zencoderai_slack_mcp_server": {
        "env": {"SLACK_BOT_TOKEN": "dummy", "SLACK_TEAM_ID": "dummy"},
        "args": [],
    },
    "firecrawl_mcp": {
        "env": {"FIRECRAWL_API_KEY": "dummy", "FIRECRAWL_API_URL": "http://localhost"},
        "args": [],
    },
    "zereight_mcp_gitlab": {
        "env": {"GITLAB_PERSONAL_ACCESS_TOKEN": "dummy", "GITLAB_API_URL": "https://gitlab.com"},
        "args": [],
    },
    "supabase_mcp_server_supabase": {
        "env": {"SUPABASE_ACCESS_TOKEN": "dummy"},
        "args": [],
    },
    "currents_mcp": {
        "env": {"CURRENTS_API_KEY": "dummy"},
        "args": [],
    },
    "hubspot_mcp_server": {
        "env": {"HUBSPOT_API_KEY": "dummy"},
        "args": [],
    },
    "tacticlaunch_mcp_linear": {
        "env": {"LINEAR_API_TOKEN": "dummy"},
        "args": [],
    },
    # needs_args: 加命令行参数（容器内路径）
    "modelcontextprotocol_server_filesystem": {
        "env": {},
        "args": ["/tmp"],   # 容器内 /tmp 一定存在
    },
    "nitrostack_cli": {
        "env": {},
        "args": ["serve"],  # 试试 serve 子命令
    },
    # transcend ESM: Linux 容器里 ESM 路径没问题，直接重跑
    # (silent_hang 类也重跑一下，也许在 Linux 容器里行为不同)
    "transcend_io_mcp_server_admin": {"env": {}, "args": []},
    "transcend_io_mcp_server_assessment": {"env": {}, "args": []},
    "transcend_io_mcp_server_discovery": {"env": {}, "args": []},
    "transcend_io_mcp_server_dsr": {"env": {}, "args": []},
    "transcend_io_mcp_server_policy": {"env": {}, "args": []},
    "transcend_io_mcp_server_workflows": {"env": {}, "args": []},
    "hasna_todos": {"env": {}, "args": []},   # ESM 路径问题，Linux 容器里可能 OK
    # pandacss: 缺 config，试 --help 看看能不能列出 tool
    "pandacss_mcp": {"env": {}, "args": ["--help"]},
}


def find_entry(pkg_root):
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


def docker_run(server_id, deps_dir, entry_rel, extra_env, extra_args):
    # 构造 env flags
    env_flags = []
    for k, v in extra_env.items():
        env_flags += ["-e", f"{k}={v}"]

    # 容器内命令: node handshake_client ... node <entry> [extra_args]
    inner = ["node", "/opt/handshake/handshake_client.js",
             f"/opt/output/{server_id}.jsonl",
             "node", f"/opt/deps/node_modules/{entry_rel}"] + extra_args

    docker = [
        DOCKER, "run", "--rm",
        "--network=none", "--read-only",
        "--tmpfs", "/tmp:rw,size=64m,mode=1777",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=1", "--memory=512m", "--pids-limit=50",
        "--stop-timeout", "180",
        "-e", "PATH=/usr/local/bin:/usr/bin:/bin",
    ] + env_flags + [
        "-v", f"{deps_dir / 'node_modules'}:/opt/deps/node_modules:ro",
        "-v", f"{OUT}:/opt/output",
        IMAGE,
    ] + inner

    try:
        return subprocess.run(docker, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None


def main():
    summary_path = OUT / "summary_v2.json"
    if summary_path.exists():
        s = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        s = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))

    to_retry = [sid for sid in OVERRIDES if not any(r["server_id"] == sid and r.get("ok") for r in s.get("results", []))]
    print(f"[retry v3] 定制化重跑 × {len(to_retry)}")

    new_results = {}
    for i, sid in enumerate(to_retry):
        ov = OVERRIDES[sid]
        deps_dir = DEPS / sid

        # 从 known_servers.json 取 pkg_name
        known = json.loads((BASE / "registry" / "known_servers.json").read_text(encoding="utf-8"))
        info = next((k for k in known if k["server_id"] == sid), {})
        pkg_ver = info.get("npm_package", "") + "@" + info.get("npm_version", "")
        pkg_name = info.get("npm_package", sid.split("_", 1)[0])

        pkg_root = deps_dir / "node_modules" / pkg_name
        if not pkg_root.exists():
            pkg_root = deps_dir / "node_modules" / sid
        entry_rel = find_entry(pkg_root) if pkg_root.exists() else None

        print(f"  [{i+1}/{len(to_retry)}] {sid} ...", end=" ", flush=True)
        if ov["args"]:
            print(f"(args={ov['args']}) ", end="")
        if ov["env"]:
            print(f"(env={list(ov['env'].keys())}) ", end="")

        if not entry_rel:
            print("ENTRY_UNKNOWN")
            new_results[sid] = {"server_id": sid, "ok": False, "n_tools": 0,
                                "reason": "entry_unknown"}
            continue

        r2 = docker_run(sid, deps_dir, entry_rel, ov["env"], ov["args"])
        ok = False
        n_tools = 0
        reason = ""

        if r2 is None:
            reason = "container_timeout_180s"
            print("TIMEOUT")
        elif r2.returncode != 0:
            reason = f"container_rc_{r2.returncode}"
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
                    reason = "handshake_error: " + err[:200]
                    errs = len([l for l in lines if '"error"' in l])
                    if errs == len(lines):
                        reason = reason.split(":")[0] + " (timeout after fake credentials/args)"
                    print(f"ERR ({errs} error lines)")
            else:
                reason = "no_output_file"
                print("NO OUTPUT")

        if not ok:
            reason += " | custom_retry_with_args_env"

        new_results[sid] = {
            "server_id": sid, "ok": ok, "n_tools": n_tools,
            "pkg_ver": pkg_ver, "reason": reason,
        }
        time.sleep(5)

    # Merge into summary_v3.json
    merged = list(s.get("results", []))
    # 先移除 OVERRIDES 里的旧条目
    merged = [r for r in merged if r["server_id"] not in OVERRIDES]
    merged.extend(new_results.values())
    # 确保 install_failures 也在（如果之前 summary 里是单独数组）
    for f in s.get("install_failures", []):
        sid = f["server_id"]
        if not any(r["server_id"] == sid for r in merged):
            merged.append({"server_id": sid, "ok": False, "n_tools": 0,
                           "pkg_ver": f.get("pkg_ver", ""),
                           "reason": "install_failed: " + f.get("detail", "")[:200]})

    n_ok = sum(1 for r in merged if r.get("ok"))
    n_total = len(merged)

    s3 = {
        "run_ts": s.get("run_ts"),
        "retry_v3_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "retry_v3_n": len(to_retry),
        "retry_v3_ok": sum(1 for r in new_results.values() if r.get("ok")),
        "n_attempted": n_total,
        "n_ok": n_ok,
        "n_fail": n_total - n_ok,
        "results": merged,
    }
    (OUT / "summary_v3.json").write_text(
        json.dumps(s3, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[done v3] +{sum(1 for r in new_results.values() if r.get('ok'))}/{len(to_retry)}")
    print(f"  F1 v1: 19/53 = 0.358")
    print(f"  F1 v2: 20/60 = 0.333")
    print(f"  F1 v3: {n_ok}/{n_total} = {n_ok/n_total:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
