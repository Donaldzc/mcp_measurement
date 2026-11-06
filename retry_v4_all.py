# -*- coding: utf-8 -*-
"""v4 重跑：修复握手器三个 bug 后，重跑全部失败 server。

修复（handshake_client.js + docker 配置）：
  1. 子进程 env 未继承 → -e 注入的凭证全部丢失
  2. stderr 管道未消费 → 日志写满 64KB 后 server 阻塞（静默挂起）
  3. HOME 不可写（--read-only 根）→ mkdir ~/.config 类崩溃
     → 新增 --tmpfs /home/sandbox + HOME=/home/sandbox

输入: sandbox/output/summary_v3.json
输出: sandbox/output/summary_v4.json
"""
import json, pathlib, shutil, subprocess, sys, time

from retry_v3_custom import OVERRIDES, find_entry

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
DEPS = BASE / "sandbox_cache" / "deps"
IMAGE = "mcp-sandbox:0.1"
DOCKER = shutil.which("docker") or "docker"

PLATFORM_INCOMPAT = {"azure_mcp_win32_x64"}


def docker_run(server_id, deps_dir, entry_rel, extra_env, extra_args):
    env_flags = []
    for k, v in extra_env.items():
        env_flags += ["-e", f"{k}={v}"]

    inner = ["node", "/opt/handshake/handshake_client.js",
             f"/opt/output/{server_id}.jsonl",
             "node", f"/opt/deps/node_modules/{entry_rel}"] + extra_args

    docker = [
        DOCKER, "run", "--rm",
        "--network=none", "--read-only",
        "--tmpfs", "/tmp:rw,size=64m,mode=1777",
        "--tmpfs", "/home/sandbox:rw,size=64m,mode=1777",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=1", "--memory=512m", "--pids-limit=50",
        "--stop-timeout", "180",
        "-e", "PATH=/usr/local/bin:/usr/bin:/bin",
        "-e", "HOME=/home/sandbox",
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
    s = json.loads((OUT / "summary_v5.json").read_text(encoding="utf-8"))
    known = json.loads((BASE / "registry" / "known_servers.json").read_text(encoding="utf-8"))
    kmap = {k["server_id"]: k for k in known}

    failed = [r for r in s["results"]
              if not r.get("ok")
              and r["server_id"] not in PLATFORM_INCOMPAT
              and not (r.get("reason") or "").startswith("install_failed")]

    print(f"[retry v4] 重跑失败 server × {len(failed)}（修复 env/stderr/HOME 三个 bug）")

    new_results = {}
    for i, r in enumerate(failed):
        sid = r["server_id"]
        ov = OVERRIDES.get(sid, {"env": {}, "args": []})
        deps_dir = DEPS / sid
        info = kmap.get(sid, {})
        pkg_name = info.get("npm_package", sid.split("_", 1)[0])
        pkg_root = deps_dir / "node_modules" / pkg_name
        if not pkg_root.exists():
            pkg_root = deps_dir / "node_modules" / sid
        entry_rel = find_entry(pkg_root) if pkg_root.exists() else None

        print(f"  [{i+1}/{len(failed)}] {sid} ...", end=" ", flush=True)
        if not entry_rel:
            print("ENTRY_UNKNOWN")
            new_results[sid] = {"server_id": sid, "ok": False, "n_tools": 0,
                                "pkg_ver": r.get("pkg_ver", ""), "reason": "entry_unknown"}
            continue

        rr = docker_run(sid, deps_dir, entry_rel, ov["env"], ov["args"])
        # WSL2 资源压力会导致偶发超时：超时自动重试一次
        out = OUT / f"{sid}.jsonl"
        def _is_timeout():
            if not (out.exists() and out.stat().st_size > 0):
                return False
            txt = out.read_text(encoding="utf-8")
            return '"error":"timeout"' in txt and '"tool_name"' not in txt
        if rr is None or _is_timeout():
            print("(retry once) ", end="", flush=True)
            time.sleep(10)
            rr = docker_run(sid, deps_dir, entry_rel, ov["env"], ov["args"])
        ok, n_tools, reason = False, 0, ""
        if rr is None:
            reason = "container_timeout_180s"
            print("TIMEOUT")
        elif rr.returncode != 0:
            reason = f"container_rc_{rr.returncode}: {(rr.stderr or rr.stdout or '')[-200:]}"
            print(f"FAIL rc={rr.returncode}")
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
                    reason = "handshake_error: " + (lines[0][:200] if lines else "")
                    print("ERR")
            else:
                reason = "no_output_file"
                print("NO OUTPUT")

        new_results[sid] = {"server_id": sid, "ok": ok, "n_tools": n_tools,
                            "pkg_ver": r.get("pkg_ver", ""), "reason": reason}
        time.sleep(5)

    merged = list(s["results"])
    for i, r in enumerate(merged):
        if r["server_id"] in new_results:
            merged[i] = new_results[r["server_id"]]

    n_ok = sum(1 for r in merged if r.get("ok"))
    s4 = {
        "run_ts": s.get("run_ts"),
        "retry_v6_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "retry_v6_n": len(failed),
        "retry_v6_ok": sum(1 for r in new_results.values() if r.get("ok")),
        "n_attempted": len(merged),
        "n_ok": n_ok,
        "n_fail": len(merged) - n_ok,
        "sandbox_config": {
            "network": "none", "read_only": True,
            "writable": ["/tmp", "/home/sandbox"],
            "cap_drop": "ALL", "no_new_privileges": True,
            "cpus": 1, "memory_mb": 512, "pids_limit": 50, "time_sec": 120,
        },
        "results": merged,
    }
    (OUT / "summary_v6.json").write_text(
        json.dumps(s4, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[done v6] +{s4['retry_v6_ok']}/{len(failed)}")
    print(f"  F1 v5: 26/60 = 0.433")
    print(f"  F1 v6: {n_ok}/{len(merged)} = {n_ok/len(merged):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
