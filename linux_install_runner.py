# -*- coding: utf-8 -*-
"""v7: 两阶段全容器化测量（消除 Windows 主机安装的平台错配）。

阶段 A（联网，named volume）:
  docker run --network=bridge -v mcpdeps_<sid>:/opt/deps -v mcp_npm_cache:/opt/npm-cache
      node:20-slim sh -c "npm install --prefix /opt/deps/<sid> pkg@ver && node -e <resolve entry>"

阶段 B（禁网，同一 volume 只读挂载）:
  docker run --network=none --read-only --tmpfs /tmp --tmpfs /home/sandbox
      -e HOME=/home/sandbox -v mcpdeps_<sid>:/opt/deps:ro mcp-sandbox:0.1
      node handshake_client.js <out> node /opt/deps/<sid>/node_modules/<pkg>/<entry>

断点续跑: 结果逐条 append 到 sandbox/output/v7_progress.jsonl，重跑时跳过已完成项。
"""
import argparse, json, pathlib, shutil, subprocess, sys, time

from retry_v3_custom import OVERRIDES

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
OUT.mkdir(parents=True, exist_ok=True)
PROGRESS = OUT / "v7_progress.jsonl"
IMAGE = "mcp-sandbox:0.1"
NODE_IMAGE = "node:20-slim"
CACHE_VOL = "mcp_npm_cache"
DOCKER = shutil.which("docker") or "docker"

PLATFORM_INCOMPAT = {"azure_mcp_win32_x64"}   # Windows 原生 .exe，Linux 容器不可执行

# 需要同装的平台分包（postinstall 脚本会校验）
EXTRA_PKGS = {"@azure/mcp": ["@azure/mcp-linux-x64"]}


def sh(args, timeout):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def volume_name(sid):
    return "mcpdeps_" + sid.replace("-", "_")


def volume_exists(vol):
    r = sh([DOCKER, "volume", "inspect", vol], 30)
    return r is not None and r.returncode == 0


def mk_volume(vol):
    sh([DOCKER, "volume", "create", vol], 60)


def stage_a_install(sid, pkg, ver, vol):
    """容器内 npm install + 解析入口。返回 entry_rel 或 None。"""
    pkg_root = f"/opt/deps/{sid}/node_modules/{pkg}"
    resolve = (
        "const fs=require('fs');"
        f"const p='{pkg_root}/package.json';"
        "if(!fs.existsSync(p)){console.log('ENTRY:none');process.exit(0)}"
        "const j=JSON.parse(fs.readFileSync(p,'utf8'));"
        "let e=null;"
        "if(j.bin){if(typeof j.bin==='string'){e=j.bin}"
        "  else{const k=Object.keys(j.bin).sort();"
        "    const stdio=k.filter(x=>!/http|sse|streamable/i.test(x+JSON.stringify(j.bin[x])));"
        "    const pool=stdio.length?stdio:k;"
        "    const pref=pool.find(x=>/mcp|server|index|cli/i.test(x))||pool[0]; e=j.bin[pref]}}"
        "else if(j.main){e=j.main}"
        "else{for(const g of ['dist/index.js','src/index.js','index.js','dist/server.js'])"
        "  if(fs.existsSync(p.replace('package.json',g))){e=g;break}}"
        f"console.log('ENTRY:'+({pkg!r}+'/'+e));"
    )
    extra = " ".join(f"{p}@{ver}" for p in EXTRA_PKGS.get(pkg, []))
    extra = (" " + extra) if extra else ""
    cmd = [
        DOCKER, "run", "--rm",
        "--network=bridge",
        "--memory=2g", "--cpus=2",
        "-v", f"{vol}:/opt/deps",
        "-v", f"{CACHE_VOL}:/opt/npm-cache",
        "-e", "npm_config_cache=/opt/npm-cache",
        "-e", "npm_config_update_notifier=false",
        NODE_IMAGE,
        "sh", "-c",
        f"npm install --prefix /opt/deps/{sid} {pkg}@{ver}{extra} "
        f"--no-audit --no-fund --no-save --loglevel=error >/tmp/npm.log 2>&1; "
        f"echo NPMRC:$?; tail -3 /tmp/npm.log | sed 's/^/NPMTAIL:/'; "
        f"node -e {shell_quote(resolve)}",
    ]
    r = sh(cmd, 600)
    if r is None:
        return None, "install_timeout_600s"
    entry = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("ENTRY:") and line.strip() != "ENTRY:none":
            entry = line[len("ENTRY:"):].strip()
    if entry:
        if entry.lower().endswith((".exe", ".dll")):
            return None, f"runtime_out_of_scope: {entry} (native binary, sandbox image has no dotnet runtime)"
        return entry, ""
    tail = [l[len("NPMTAIL:"):] for l in (r.stdout or "").splitlines()
            if l.startswith("NPMTAIL:")]
    npm_rc = next((l for l in (r.stdout or "").splitlines() if l.startswith("NPMRC:")), "NPMRC:?")
    detail = " | ".join(tail)[:300] or f"{npm_rc} rc={r.returncode}"
    return None, "install_failed: " + detail


def shell_quote(js):
    # 传给 sh -c 的单引号包裹（JS 内不含单引号）
    return "'" + js.replace("'", "'\\''") + "'"


def stage_b_handshake(sid, vol, entry_rel, env, args):
    env_flags = []
    for k, v in env.items():
        env_flags += ["-e", f"{k}={v}"]
    inner = ["node", "/opt/handshake/handshake_client.js",
             f"/opt/output/{sid}.jsonl",
             "node", f"/opt/deps/{sid}/node_modules/{entry_rel}"] + args
    cmd = [
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
        "-v", f"{vol}:/opt/deps:ro",
        "-v", f"{OUT}:/opt/output",
        IMAGE,
    ] + inner
    return sh(cmd, 200)


def load_progress():
    done = {}
    if PROGRESS.exists():
        for line in PROGRESS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                done[r["server_id"]] = r
            except Exception:
                pass
    return done


def append_progress(rec):
    with PROGRESS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="", help="逗号分隔的 server_id，只跑这些")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--clean-volumes", action="store_true")
    args = ap.parse_args()

    if args.reset and PROGRESS.exists():
        PROGRESS.unlink()

    sh([DOCKER, "volume", "create", CACHE_VOL], 60)

    servers = json.loads((BASE / "registry" / "known_servers.json").read_text(encoding="utf-8"))
    if args.only:
        keep = set(args.only.split(","))
        servers = [s for s in servers if s["server_id"] in keep]
    if args.limit:
        servers = servers[:args.limit]

    done = load_progress()
    todo = [s for s in servers if s["server_id"] not in done]
    print(f"[v7] 总 {len(servers)}，已完成 {len(servers)-len(todo)}，本轮待跑 {len(todo)}", flush=True)

    for i, s in enumerate(todo):
        sid = s["server_id"]
        pkg, ver = s["npm_package"], s["npm_version"]
        vol = volume_name(sid)
        ov = OVERRIDES.get(sid, {"env": {}, "args": []})
        print(f"  [{i+1}/{len(todo)}] {sid} ...", end=" ", flush=True)

        if sid in PLATFORM_INCOMPAT:
            rec = {"server_id": sid, "ok": False, "n_tools": 0,
                   "reason": "platform_incompatible: windows native exe"}
            append_progress(rec)
            print("SKIP platform")
            continue

        t0 = time.time()
        if not volume_exists(vol):
            mk_volume(vol)
        entry, err = stage_a_install(sid, pkg, ver, vol)
        if not entry:
            rec = {"server_id": sid, "ok": False, "n_tools": 0, "reason": err,
                   "elapsed_a": round(time.time() - t0, 1)}
            append_progress(rec)
            print("INSTALL FAIL")
            continue
        print(f"entry ok ({round(time.time()-t0,1)}s) ...", end=" ", flush=True)

        rr = stage_b_handshake(sid, vol, entry, ov["env"], ov["args"])
        ok, n_tools, reason = False, 0, ""
        if rr is None:
            reason = "container_timeout_200s"
        elif rr.returncode != 0:
            reason = f"container_rc_{rr.returncode}: {(rr.stderr or '')[-200:]}"
        else:
            out = OUT / f"{sid}.jsonl"
            if out.exists() and out.stat().st_size > 0:
                lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
                real = [l for l in lines if '"error"' not in l]
                n_tools = len(real)
                ok = n_tools > 0
                if not ok:
                    reason = lines[0][:260] if lines else "empty"
            else:
                reason = "no_output_file"

        rec = {"server_id": sid, "ok": ok, "n_tools": n_tools, "pkg_ver": f"{pkg}@{ver}",
               "reason": reason, "elapsed_total": round(time.time() - t0, 1)}
        append_progress(rec)
        print(f"OK {n_tools} tools" if ok else "FAIL")
        time.sleep(3)

    # 汇总
    done = load_progress()
    rows = [done[s["server_id"]] for s in servers if s["server_id"] in done]
    n_ok = sum(1 for r in rows if r.get("ok"))
    summary = {
        "run_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "protocol": "v7: npm install inside linux container (named volume), handshake offline",
        "n_attempted": len(rows),
        "n_ok": n_ok,
        "F1": round(n_ok / len(rows), 3) if rows else 0.0,
        "tools_total": sum(r.get("n_tools", 0) for r in rows if r.get("ok")),
        "results": rows,
    }
    (OUT / "summary_v7.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(f"\n[done v7] F1 {n_ok}/{len(rows)} = {summary['F1']}  tools={summary['tools_total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
