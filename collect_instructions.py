# -*- coding: utf-8 -*-
"""Probe the `instructions` field for the 32 deployable servers only."""
import csv
import json
import pathlib
import subprocess
import time

BASE = pathlib.Path(__file__).resolve().parent
DEPS = BASE / "sandbox_cache" / "deps"
OUT = BASE / "sandbox" / "output"
RUNS = OUT / "instr_runs"
RUNS.mkdir(parents=True, exist_ok=True)
IMAGE = "mcp-sandbox:0.1"
CLIENT = BASE / "sandbox" / "handshake_client_instr.js"


def find_entry(pkg_root):
    pkg = json.loads((pkg_root / "package.json").read_text(encoding="utf-8"))
    name = pkg.get("name")
    b = pkg.get("bin")
    if isinstance(b, str):
        return f"{name}/{b}"
    if isinstance(b, dict) and b:
        return f"{name}/{list(b.values())[0]}"
    if pkg.get("main"):
        return f"{name}/{pkg['main']}"
    for g in ("dist/index.js", "src/index.js", "index.js", "dist/server.js"):
        if (pkg_root / g).exists():
            return f"{name}/{g}"
    return None


def run(sid, deps_dir, entry_rel, timeout=60):
    inner = ["node", "/opt/handshake/handshake_client_instr.js",
             f"/opt/output/instr_runs/{sid}.jsonl",
             "node", f"/opt/deps/node_modules/{entry_rel}"]
    cmd = ["docker", "run", "--rm", "--network=none", "--read-only",
           "--tmpfs", "/tmp:rw,size=64m,mode=1777", "--cap-drop=ALL",
           "--security-opt=no-new-privileges", "--cpus=1", "--memory=256m",
           "--pids-limit=50", "--stop-timeout", "120",
           "-e", "PATH=/usr/local/bin:/usr/bin:/bin",
           "-v", f"{deps_dir / 'node_modules'}:/opt/deps/node_modules:ro",
           "-v", f"{OUT}:/opt/output",
           "-v", f"{CLIENT}:/opt/handshake/handshake_client_instr.js:ro",
           IMAGE] + inner
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.returncode
    except subprocess.TimeoutExpired:
        return None


def main():
    servers = json.loads((BASE / "registry" / "known_servers.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader((OUT / "f4_all_v2.csv").open(encoding="utf-8", newline="")))
    deployable = sorted({r["server"] for r in rows})
    summary = []
    todo = [s for s in servers if s["server_id"] in deployable]
    print(f"deployable={len(deployable)} matched={len(todo)}", flush=True)
    for i, s in enumerate(todo, 1):
        sid = s["server_id"]
        pkg_root = DEPS / sid / "node_modules" / s["npm_package"]
        if not pkg_root.exists():
            summary.append({"server_id": sid, "status": "no_deps"})
            print(f"[{i}/{len(todo)}] {sid} no_deps", flush=True)
            continue
        if (RUNS / f"{sid}.jsonl.instr.json").exists():
            summary.append({"server_id": sid, "status": "cached", "sidecar": True})
            print(f"[{i}/{len(todo)}] {sid} cached", flush=True)
            continue
        entry = find_entry(pkg_root)
        if not entry:
            summary.append({"server_id": sid, "status": "no_entry"})
            print(f"[{i}/{len(todo)}] {sid} no_entry", flush=True)
            continue
        rc = run(sid, DEPS / sid, entry)
        side = RUNS / f"{sid}.jsonl.instr.json"
        status = "ok" if rc == 0 and side.exists() else ("timeout" if rc is None else f"rc={rc}")
        summary.append({"server_id": sid, "status": status, "entry": entry, "sidecar": side.exists()})
        print(f"[{i}/{len(todo)}] {sid} {status} sidecar={side.exists()}", flush=True)
        time.sleep(0.3)
    (OUT / "instructions_run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("done", sum(1 for x in summary if x.get("sidecar")), "sidecars")


if __name__ == "__main__":
    main()
