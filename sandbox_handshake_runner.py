# sandbox_handshake_runner.py —— 容器化动态 tools/list v3（纯离线 + 直接 node）
#
# 两阶段流水线（v2 设计 §4 sandbox 七项要求全部满足）:
#   阶段 A'（主机有网）: npm install --prefix sandbox_cache/{sid} pkg@ver
#                       解析 package.json 找入口文件（bin/main/dist/index.js）
#   阶段 B（Docker --network=none 禁网）: mount 整个 node_modules
#                       + 直接 node /opt/deps/node_modules/{pkg}/dist/index.js
#                       → tools/list（不经过 npx，不触发依赖解析）
#
# 安全配置:
#   --network=none         禁网络
#   --read-only            只读根
#   --tmpfs /tmp           唯一可写
#   --cap-drop=ALL         丢所有 capabilities
#   --security-opt=no-new-privileges  禁特权提升
#   --cpus=1 --memory=256m --pids-limit=50
#   --stop-timeout=120     握手器内部 60s
#
# 用法:
#   python sandbox_handshake_runner.py --known 10            # 批量 install + 容器跑
#   python sandbox_handshake_runner.py --known 26 --stage-a-only  # 只 install
#   python sandbox_handshake_runner.py --known 5 --force-install  # 强制重装
import argparse, json, os, pathlib, shutil, subprocess, sys, time

BASE = pathlib.Path(__file__).resolve().parent
CACHE = BASE / "sandbox_cache"
DEPS = CACHE / "deps"
OUT = BASE / "sandbox" / "output"
DEPS.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
IMAGE = "mcp-sandbox:0.1"

# Windows 上 npm/docker 是 .cmd/.exe 包装，subprocess 传字面量会 WinError 2
NPM = shutil.which("npm") or shutil.which("npm.cmd") or "npm"
DOCKER = shutil.which("docker") or "docker"


def host_install(server_id, pkg_ver):
    """主机侧 npm install --prefix deps/{server_id} pkg@ver。返回 (deps_dir, pkg_root_path, entry_rel, ok)。"""
    target = DEPS / server_id
    pkg_name = pkg_ver.rsplit("@", 1)[0]   # @scope/pkg 或 pkg
    pkg_root = target / "node_modules" / pkg_name
    entry_rel = None

    skips = _load_skips()
    if server_id in skips and not args_force_install:
        LAST_ERR[server_id] = "install_timeout_300s (previous run, skipped)"
        return target, pkg_root, None, False

    if (pkg_root).exists() and not args_force_install:
        # 已安装，直接读入口
        try:
            entry_rel = find_entry(pkg_root)
        except Exception:
            pass
        return target, pkg_root, entry_rel, True

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            [NPM, "install", "--prefix", str(target), pkg_ver,
             "--no-audit", "--no-fund", "--no-save", "--loglevel=error"],
            capture_output=True, text=True, timeout=300, shell=False)
    except subprocess.TimeoutExpired:
        # 例: desktop-commander postinstall 下载 Chrome for Testing >5min
        LAST_ERR[server_id] = "install_timeout_300s"
        _add_skip(server_id)
        shutil.rmtree(target, ignore_errors=True)
        return target, pkg_root, None, False
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        LAST_ERR[server_id] = err[-1] if err else f"rc={r.returncode}"
        return target, pkg_root, None, False

    entry_rel = find_entry(pkg_root)
    return target, pkg_root, entry_rel, True


def find_entry(pkg_root):
    """从 package.json 找 bin/main/dist/index.js。返回相对 node_modules 的路径。"""
    pkg_json = pkg_root / "package.json"
    if not pkg_json.exists():
        return None
    pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
    pkg_name = pkg.get("name", pkg_root.name)

    # 1) bin 字段（对象取第一个 value，字符串直接用）
    bin_field = pkg.get("bin")
    if bin_field:
        if isinstance(bin_field, dict):
            entry = list(bin_field.values())[0]
        else:
            entry = bin_field
        # bin 通常是 dist/index.js 或 dist/cli.js
        return f"{pkg_name}/{entry}"

    # 2) main 字段
    main = pkg.get("main")
    if main:
        return f"{pkg_name}/{main}"

    # 3) 常规猜测: dist/index.js, src/index.js, index.js
    for guess in ["dist/index.js", "src/index.js", "index.js", "dist/server.js"]:
        if (pkg_root / guess).exists():
            return f"{pkg_name}/{guess}"
    return None


def docker_run(server_id, deps_dir, entry_rel):
    """禁网容器直接 node 启动 server。entry_rel = pkg/dist/index.js 相对 node_modules。"""
    if not entry_rel:
        return None  # 无法确定入口
    inner = ["node", "/opt/handshake/handshake_client.js",
             f"/opt/output/{server_id}.jsonl",
             "node", f"/opt/deps/node_modules/{entry_rel}"]
    docker = [
        DOCKER, "run", "--rm",
        "--network=none", "--read-only",
        "--tmpfs", "/tmp:rw,size=64m,mode=1777",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=1", "--memory=256m", "--pids-limit=50",
        "--stop-timeout", "120",
        "-e", "PATH=/usr/local/bin:/usr/bin:/bin",
        "-v", f"{deps_dir / 'node_modules'}:/opt/deps/node_modules:ro",
        "-v", f"{OUT}:/opt/output",
        IMAGE,
    ] + inner
    try:
        return subprocess.run(docker, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None


args_force_install = False
LAST_ERR = {}
SKIPS = CACHE / "install_skips.json"


def _load_skips():
    if SKIPS.exists():
        try:
            return set(json.loads(SKIPS.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _add_skip(sid):
    s = _load_skips()
    s.add(sid)
    SKIPS.write_text(json.dumps(sorted(s)), encoding="utf-8")


def tail(text, n=300):
    t = (text or "").strip().splitlines()
    return " | ".join(t[-3:])[:n]


def main():
    global args_force_install
    ap = argparse.ArgumentParser()
    ap.add_argument("--known", type=int, default=10)
    ap.add_argument("--stage-a-only", action="store_true")
    ap.add_argument("--force-install", action="store_true")
    args = ap.parse_args()
    args_force_install = args.force_install

    servers = json.loads((BASE / "registry" / "known_servers.json").read_text(encoding="utf-8"))
    servers = servers[:args.known]
    print(f"[stage A'] npm install × {len(servers)}", flush=True)

    staged = []
    failures = []
    for s in servers:
        sid = s["server_id"]
        pkg_ver = f"{s['npm_package']}@{s['npm_version']}"
        print(f"  [{sid}] {pkg_ver} ...", end=" ", flush=True)
        try:
            deps_dir, pkg_root, entry_rel, ok = host_install(sid, pkg_ver)
        except Exception as e:
            print(f"HOST ERR {type(e).__name__}")
            failures.append({"server_id": sid, "pkg_ver": pkg_ver,
                             "reason": "install_failed",
                             "detail": f"{type(e).__name__}: {e}"})
            continue
        if ok and entry_rel:
            size_mb = sum(f.stat().st_size for f in (deps_dir / "node_modules").rglob("*") if f.is_file()) / 1e6
            print(f"entry={entry_rel}  {size_mb:.1f} MB")
            staged.append({"server_id": sid, "pkg_ver": pkg_ver,
                           "deps_dir": str(deps_dir), "entry_rel": entry_rel})
        elif ok:
            print("OK but ENTRY_UNKNOWN")
            staged.append({"server_id": sid, "pkg_ver": pkg_ver,
                           "deps_dir": str(deps_dir), "entry_rel": None})
        else:
            print("INSTALL FAIL: " + LAST_ERR.get(sid, "?"))
            failures.append({"server_id": sid, "pkg_ver": pkg_ver,
                             "reason": "install_failed", "detail": LAST_ERR.get(sid, "")})
        time.sleep(0.1)

    if args.stage_a_only:
        print(f"\n[done stage A'] {len([s for s in staged if s['entry_rel']])}/{len(servers)} ready")
        return

    # 阶段 B: --network=none 直接 node 启动
    print(f"\n[stage B] docker --network=none × {len(staged)}", flush=True)
    results = []
    for i, s in enumerate(staged):
        sid = s["server_id"]
        if not s["entry_rel"]:
            results.append({"server_id": sid, "ok": False, "reason": "entry unknown"})
            print(f"  [{i+1}/{len(staged)}] {sid} → SKIP entry unknown")
            continue
        print(f"  [{i+1}/{len(staged)}] {sid} ...", end=" ", flush=True)
        r = docker_run(sid, pathlib.Path(s["deps_dir"]), s["entry_rel"])
        ok = False
        n_tools = 0
        reason = ""
        if r is None:
            reason = "container_timeout"
            print("TIMEOUT")
        elif r.returncode != 0:
            reason = "container_rc_" + str(r.returncode) + ": " + tail(r.stderr or r.stdout)
            print(f"FAIL rc={r.returncode}")
        else:
            out = OUT / f"{sid}.jsonl"
            if out.exists() and out.stat().st_size > 0:
                lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
                real = [l for l in lines if '"error"' not in l]
                n_tools = len(real)
                if n_tools > 0:
                    print(f"OK {n_tools} tools")
                    ok = True
                else:
                    err = next((l for l in lines if '"error"' in l), "")
                    reason = "handshake_error: " + err[:300]
                    print("ERRORS ONLY")
            else:
                reason = "no_output_file"
                print("NO OUTPUT")
        results.append({"server_id": sid, "ok": ok, "n_tools": n_tools,
                        "pkg_ver": s["pkg_ver"], "reason": reason})
        time.sleep(0.2)

    n_ok = sum(1 for r in results if r["ok"])
    summary = {
        "run_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_attempted": len(results),
        "n_ok": n_ok, "n_fail": len(results) - n_ok,
        "sandbox_config": {
            "network": "none", "read_only": True, "tmpfs": "/tmp",
            "cap_drop": "ALL", "no_new_privileges": True,
            "cpus": 1, "memory_mb": 256, "pids_limit": 50, "time_sec": 120,
        },
        "results": results,
        "install_failures": failures,
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] {n_ok}/{len(results)} OK → {OUT}")


if __name__ == "__main__":
    sys.exit(main())
