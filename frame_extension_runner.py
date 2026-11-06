# -*- coding: utf-8 -*-
"""Frame-extension runner (blind-audit protocol Section 3).

Runs servers ranked 61-120 of the frozen 322-server launchable list
(registry/npm_frame.jsonl, included==True, downloads_monthly desc) through the
identical v7 pin/handshake pipeline as the main study (linux_install_runner):
stage A npm install inside a networked throwaway container with a named volume,
stage B handshake in a --network=none read-only container.

Artifacts are kept disjoint from the frozen main-study run:
  progress  -> sandbox/output/ext_progress.jsonl   (v7_progress.jsonl untouched)
  summary   -> sandbox/output/summary_ext61_120.json (summary_v7.json untouched)
  per-server transcripts -> sandbox/output/{server_id}.jsonl (same schema as v7;
      server_ids are unique vs the frozen 60, so no collision)

Failed handshakes are recorded; replacement by next-ranked servers is handled
by re-running with a wider seed file, never by dropping for content.

用法:
  python frame_extension_runner.py            # 跑全部 60 台（断点续跑）
  python frame_extension_runner.py --limit 5  # 先试跑 5 台
"""
import argparse, json, pathlib, sys, time

import linux_install_runner as v7

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
OUT.mkdir(parents=True, exist_ok=True)
PROGRESS = OUT / "ext_progress.jsonl"
SUMMARY = OUT / "summary_ext61_120.json"
SEEDS = BASE / "registry" / "known_servers_ext61_120.json"


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
    ap.add_argument("--only", default="")
    ap.add_argument("--seeds", default="", help="override seed JSON (e.g. ranks 121-180)")
    ap.add_argument("--tag", default="", help="output tag: progress=ext_progress_{tag}.jsonl, summary=summary_{tag}.json")
    args = ap.parse_args()

    global PROGRESS, SUMMARY, SEEDS
    if args.seeds:
        SEEDS = pathlib.Path(args.seeds)
    if args.tag:
        PROGRESS = OUT / f"ext_progress_{args.tag}.jsonl"
        SUMMARY = OUT / f"summary_{args.tag}.json"

    servers = json.loads(SEEDS.read_text(encoding="utf-8"))
    if args.only:
        keep = set(args.only.split(","))
        servers = [s for s in servers if s["server_id"] in keep]
    if args.limit:
        servers = servers[:args.limit]

    v7.sh([v7.DOCKER, "volume", "create", v7.CACHE_VOL], 60)

    done = load_progress()
    todo = [s for s in servers if s["server_id"] not in done]
    print(f"[ext] total {len(servers)}, done {len(servers)-len(todo)}, to run {len(todo)}", flush=True)

    for i, s in enumerate(todo):
        sid = s["server_id"]
        pkg, ver = s["npm_package"], s["npm_version"]
        vol = v7.volume_name(sid)
        print(f"  [{i+1}/{len(todo)}] rank{s.get('frame_rank')} {sid} ...", end=" ", flush=True)

        t0 = time.time()
        if not v7.volume_exists(vol):
            v7.mk_volume(vol)
        entry, err = v7.stage_a_install(sid, pkg, ver, vol)
        if not entry:
            append_progress({"server_id": sid, "ok": False, "n_tools": 0,
                             "frame_rank": s.get("frame_rank"), "reason": err,
                             "elapsed_a": round(time.time() - t0, 1)})
            print("INSTALL FAIL")
            continue
        print(f"entry ok ({round(time.time()-t0,1)}s) ...", end=" ", flush=True)

        rr = v7.stage_b_handshake(sid, vol, entry, {}, [])
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

        append_progress({"server_id": sid, "ok": ok, "n_tools": n_tools,
                         "frame_rank": s.get("frame_rank"),
                         "pkg_ver": f"{pkg}@{ver}", "reason": reason,
                         "elapsed_total": round(time.time() - t0, 1)})
        print(f"OK {n_tools} tools" if ok else "FAIL")
        time.sleep(1)

    done = load_progress()
    rows = [done[s["server_id"]] for s in servers if s["server_id"] in done]
    n_ok = sum(1 for r in rows if r.get("ok"))
    summary = {
        "run_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "protocol": "frame extension, ranks 61-120 of frozen 322 launchable list; "
                    "identical v7 pipeline (linux container install + offline handshake)",
        "seed_file": str(SEEDS),
        "n_attempted": len(rows),
        "n_ok": n_ok,
        "F1": round(n_ok / len(rows), 3) if rows else 0.0,
        "tools_total": sum(r.get("n_tools", 0) for r in rows if r.get("ok")),
        "results": rows,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done ext] {n_ok}/{len(rows)} OK  tools={summary['tools_total']} -> {SUMMARY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
