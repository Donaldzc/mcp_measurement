# -*- coding: utf-8 -*-
"""F3 静态-动态工具注册一致率。

静态: 用 acorn AST 从包源码抽取工具名候选（注册调用字面量 + name 属性字面量）
动态: 容器 tools/list 结果（sandbox/output/<sid>.jsonl 的 tool_name）

指标:
  recall     = |D ∩ S| / |D|        (动态存在的工具，静态能否找到)
  precision  = |S_call ∩ D| / |S_call|   (静态注册调用是否都有对应动态工具，幽灵工具检测)
  exact / normalized 两种匹配都算，normalized 用于排除命名风格差异

输出: sandbox/output/f3_report.json
"""
import json, pathlib, re, subprocess, shutil, sys

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
CACHE = BASE / "sandbox_cache" / "deps"
EXTRACT = BASE / "sandbox_cache" / "_f3_src"
ACORN = CACHE / "mastra_mcp_docs_server" / "node_modules" / "acorn"
SCRIPT = BASE / "static_tools.js"
DOCKER = shutil.which("docker") or "docker"


def norm(s):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def compact(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def pkg_dir_for(sid, pkg):
    d = CACHE / sid / "node_modules" / pkg
    if d.exists():
        return d
    # 回退: 从 docker volume 导出（仅源码体积，不重装）
    EXTRACT.mkdir(parents=True, exist_ok=True)
    target = EXTRACT / sid
    vol = "mcpdeps_" + sid.replace("-", "_")
    if not target.exists():
        r = subprocess.run(
            [DOCKER, "run", "--rm", "-v", f"{vol}:/opt/deps:ro",
             "-v", f"{EXTRACT}:/out", "node:20-slim",
             "sh", "-c", f"cd /opt/deps/{sid}/node_modules/{pkg} && tar cf /out/{sid}.tar ."],
            capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return None
        target.mkdir(exist_ok=True)
        subprocess.run(["tar", "xf", str(EXTRACT / f"{sid}.tar"), "-C", str(target)],
                       capture_output=True, text=True, timeout=300)
    return target if target.exists() else None


def extract(src):
    r = subprocess.run(["node", str(SCRIPT), str(ACORN), str(src)],
                       capture_output=True, text=True, timeout=900)
    try:
        st = json.loads(r.stdout)
        return (set(st["calls"]), set(st["nameProps"]), set(st.get("literals", [])), st)
    except Exception:
        return set(), set(), set(), {"files": 0, "parse_failed": 0,
                                     "err": (r.stderr or "")[:200]}


def dep_dirs(sid, pkg, pkg_dir):
    """目标包的运行时直接依赖（浅层），用于 thin-wrapper 形态的源码追踪。"""
    try:
        j = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    nm = CACHE / sid / "node_modules"
    for d in list((j.get("dependencies") or {}).keys()):
        p = nm / d
        if p.exists() and p != pkg_dir:
            out.append(p)
    return out


def main():
    summ = json.loads((OUT / "summary_v7.json").read_text(encoding="utf-8"))
    ok = [r for r in summ["results"] if r.get("ok")]
    print(f"F3: {len(ok)} 个动态成功 server")

    per = []
    for r in ok:
        sid = r["server_id"]
        pkg = r["pkg_ver"].rsplit("@", 1)[0]
        dyn_file = OUT / f"{sid}.jsonl"
        D = set()
        for line in dyn_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("tool_name"):
                D.add(o["tool_name"])

        src = pkg_dir_for(sid, pkg)
        if src is None:
            per.append({"server_id": sid, "n_dynamic": len(D), "static_ok": False,
                        "note": "source_unavailable"})
            print(f"  {sid}: source unavailable")
            continue

        calls, props, lits, st = extract(src)
        calls_target = set(calls)          # 仅目标包内注册调用，用于 precision
        # thin-wrapper: 目标包本身无命中时，扩展到直接依赖
        expanded = False
        if not (calls & D):
            for dep in dep_dirs(sid, pkg, src):
                c2, p2, l2, st2 = extract(dep)
                calls |= c2
                props |= p2
                lits |= l2
                st["files"] = st.get("files", 0) + st2.get("files", 0)
                expanded = True
        declared = calls | props
        union = declared | lits

        hit_exact = D & union
        hit_declared = D & declared
        hit_strict = D & calls
        hit_calls_exact = D & calls_target
        dn = {norm(x) for x in D}
        un = {norm(x) for x in union}
        hit_norm = dn & un
        dc = {compact(x) for x in D}
        uc = {compact(x) for x in union}
        hit_compact = dc & uc

        recall = len(hit_exact) / len(D) if D else 0.0
        recall_norm = len(hit_norm) / len(D) if D else 0.0
        recall_compact = len(hit_compact) / len(D) if D else 0.0
        prec_denom = len(calls_target)
        precision = (len(hit_calls_exact) / prec_denom) if prec_denom else None

        # 命名变换匹配：动态名 = 静态名 + 前后缀（如 hostinger_<tool>）
        sn = {norm(x) for x in union if len(norm(x)) >= 4}
        transform = sum(1 for d in dn if d not in sn and any(d.endswith(s) or d.startswith(s) for s in sn))
        if recall_norm >= 0.99:
            style = "literal_registration"
        elif recall_compact >= 0.99:
            style = "case_convention"
        elif recall_compact >= 0.3:
            style = "partial_literal"
        elif (transform / len(D) >= 0.3) if D else False:
            style = "name_transformed"
        else:
            style = "runtime_generated"

        per.append({
            "server_id": sid, "n_dynamic": len(D),
            "n_static_calls": len(calls), "n_target_calls": len(calls_target),
            "n_static_nameprops": len(props),
            "matched_exact": len(hit_exact),
            "matched_declared": len(hit_declared),
            "matched_strict": len(hit_strict),
            "matched_compact": len(hit_compact),
            "recall_strict": round(len(hit_strict) / len(D), 3) if D else 0.0,
            "recall_declared": round(len(hit_declared) / len(D), 3) if D else 0.0,
            "target_matched": len(hit_calls_exact),
            "recall": round(recall, 3),
            "recall_normalized": round(recall_norm, 3),
            "recall_case_compact": round(recall_compact, 3),
            "precision_calls": None if precision is None else round(precision, 3),
            "transform_matched": transform,
            "registration_style": style,
            "deps_expanded": expanded,
            "files_parsed": st["files"], "files_failed": st["parse_failed"],
            "missing_in_static": sorted(D - union)[:12],
            "static_only": sorted(calls_target - D),
            "n_static_only": len(calls_target - D),
        })
        print(f"  {sid}: dyn={len(D):3d} static={len(union):4d} "
              f"recall={recall:.2f} norm={recall_norm:.2f} prec={precision if precision is None else round(precision,2)}")

    valid = [p for p in per if p.get("static_ok") is not False]
    sumD = sum(p["n_dynamic"] for p in valid)
    sumHit = sum(p["matched_exact"] for p in valid)
    sumStrict = sum(p.get("matched_strict", 0) for p in valid)
    sumDeclared = sum(p.get("matched_declared", 0) for p in valid)
    sumCall = sum(p.get("n_target_calls", 0) for p in valid)
    sumCallHit = sum(p.get("target_matched", 0) for p in valid)
    micro_recall = sumHit / sumD if sumD else 0
    sumCompact = sum(p.get("matched_compact", 0) for p in valid)
    micro_recall_compact = sumCompact / sumD if sumD else 0
    macro_recall = sum(p["recall"] for p in valid) / len(valid) if valid else 0
    micro_precision = (sumCallHit / sumCall) if sumCall else None

    zero = [p["server_id"] for p in valid if p["recall"] == 0]
    full = [p["server_id"] for p in valid if p["recall"] >= 0.99]
    styles = {}
    style_detail = {}
    for p in valid:
        styles[p["registration_style"]] = styles.get(p["registration_style"], 0) + 1
        e = style_detail.setdefault(p["registration_style"],
                                    {"servers": 0, "tools": 0, "matched_strict": 0,
                                     "matched_declared": 0, "matched_text": 0})
        e["servers"] += 1
        e["tools"] += p["n_dynamic"]
        e["matched_strict"] += p["matched_strict"]
        e["matched_declared"] += p["matched_declared"]
        e["matched_text"] += p["matched_exact"]
    for e in style_detail.values():
        n = e["tools"] or 1
        e["recall_strict"] = round(e["matched_strict"] / n, 3)
        e["recall_declared"] = round(e["matched_declared"] / n, 3)
        e["recall_text"] = round(e["matched_text"] / n, 3)
    report = {
        "n_servers": len(per), "n_source_ok": len(valid),
        "tools_dynamic_total": sumD,
        "matched_total": sumHit,
        "micro_recall_text": round(micro_recall, 3),
        "micro_recall_declared": round(sumDeclared / sumD, 3) if sumD else 0.0,
        "micro_recall_strict": round(sumStrict / sumD, 3) if sumD else 0.0,
        "matched_declared_total": sumDeclared,
        "matched_strict_total": sumStrict,
        "micro_recall": round(micro_recall, 3),
        "micro_recall_compact": round(micro_recall_compact, 3),
        "macro_recall": round(macro_recall, 3),
        "micro_precision_calls": None if micro_precision is None else round(micro_precision, 3),
        "servers_full_recall": len(full),
        "servers_zero_recall": len(zero),
        "zero_recall_servers": zero,
        "registration_styles": styles,
        "style_detail": style_detail,
        "per_server": per,
    }
    (OUT / "f3_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(f"\n=== F3 汇总 ===")
    print(f"  servers={report['n_servers']} (source ok {report['n_source_ok']})")
    print(f"  dynamic tools={sumD}  matched={sumHit}")
    print(f"  recall(strict 注册调用)={report['micro_recall_strict']}  "
          f"declared(含 name/常量)={report['micro_recall_declared']}  "
          f"text(任意文本,上界)={report['micro_recall_text']}")
    print(f"  micro precision(calls)={report['micro_precision_calls']}")
    print(f"  完全命中 server={len(full)}  零命中 server={len(zero)}: {zero}")
    print(f"  注册风格分布: {styles}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
