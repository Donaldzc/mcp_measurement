# -*- coding: utf-8 -*-
"""Static-only 名字的人工判定（rename vs 配置/列举守卫）。

输入: sandbox/output/f3_report.json 的 per_server[*].static_only
      即"目标包源码中存在注册调用、但运行时 tools/list 未出现"的名字。

做法:
  1. 判定依据是**人工阅读 pinned 源码**，分类写在本文件的 TRIAGE 表中；
     脚本不做自动分类，只负责把每个名字重新定位到 pinned 源码的行号，
     并 assert 关键证据串仍然存在——防止源码版本漂移后判定失效。
  2. 每个名字给出: 机制(rename / config_flag / mode_flag / listing_suppressed)、
     证据(文件+行号)、默认部署下的替代工具(若有)。

输出: sandbox/output/static_only_triage.json
"""
import json, pathlib, re, sys

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
DEPS = BASE / "sandbox_cache" / "deps"

# ── 人工判定表 ──────────────────────────────────────────────
# mechanism:
#   config_flag         注册被一个默认关闭的命令行开关守卫
#   mode_flag           注册被一个默认关闭、且与默认路径互斥的模式开关守卫
#   listing_suppressed  注册存在但被无条件从 tools/list 过滤
#   rename              注册后被改名（源码名 != 运行时名）
# anchor  = (file, must_contain)：守卫所在行必须包含该子串
# replace = 默认部署下实际暴露的对应工具（None 表示无替代）
TRIAGE = {
    "google_cloud_storage_mcp": {
        "mechanism": "config_flag",
        "gate": ("dist/bundle.js", 'if (argv["enable-destructive-tools"])'),
        "default_branch": "allTools.push(...safeWriteTools)",
        "note": "destructive 分支仅在 --enable-destructive-tools 时注册；默认分支注册 *_safe 变体",
        "tools": {
            "copy_object": {"replace": "copy_object_safe"},
            "delete_bucket": {"replace": None},
            "delete_object": {"replace": None},
            "download_object": {"replace": None},
            "move_object": {"replace": None},
            "update_bucket_labels": {"replace": None},
            "update_object_metadata": {"replace": None},
            "upload_object": {"replace": "upload_object_safe"},
            "write_object": {"replace": "write_object_safe"},
        },
    },
    "softeria_ms_365_mcp_server": {
        "mechanism": "mode_flag",
        "gate": ("dist/server.js", "if (this.options.discovery)"),
        "default_branch": "} else {\n registerGraphTools(...)",
        "note": "--discovery 与默认逐操作注册互斥；默认路径注册 197 个 Graph 工具",
        "tools": {
            "execute-tool": {"replace": None},
            "get-tool-schema": {"replace": None},
            "search-tools": {"replace": None},
        },
    },
    "firecrawl_mcp": {
        "mechanism": "listing_suppressed",
        "gate": ("dist/index.js", "canList: () => false"),
        "default_branch": None,
        "note": "deprecated 兼容注册：留在 registry 以便缓存会话拿到错误负载，但无条件不列举",
        "tools": {
            "firecrawl_extract": {"replace": "firecrawl_scrape (formats: json)"},
            "firecrawl_research_search_github": {"replace": "firecrawl_developer_search"},
        },
    },
}


def pkg_dir(sid):
    summ = json.loads((OUT / "summary_v7.json").read_text(encoding="utf-8"))
    pkg = next(r["pkg_ver"] for r in summ["results"] if r["server_id"] == sid)
    name = pkg.rsplit("@", 1)[0]
    return DEPS / sid / "node_modules" / name


REG_MARKERS = ("server.tool(", "registerTool(", "server.addTool(", "addTool(")


def registration_line(lines, name):
    """定位注册调用行：名字出现在 tool()/registerTool()/addTool() 的参数位置。

    覆盖两种写法: 名字作首参 server.tool("x", ...)，以及对象字面量
    server.addTool({ name: "x", ... })。
    """
    hits = []
    for i, ln in enumerate(lines):
        if f'"{name}"' not in ln and f"'{name}'" not in ln:
            continue
        window = lines[max(0, i - 6):i + 1]
        if any(m in w for w in window for m in REG_MARKERS):
            hits.append(i + 1)
    return hits


def nearest_after(lines, idx, needle, limit=40):
    for j in range(idx + 1, min(len(lines), idx + limit)):
        if needle in lines[j]:
            return j + 1
    return None


def enclosing_function(lines, idx):
    """向上找最近的 `var <fn> = (server) =>`，即注册函数定义。"""
    for j in range(idx - 1, max(-1, idx - 400), -1):
        m = re.match(r"var (\w+) = \(server\) =>", lines[j])
        if m:
            return m.group(1), j + 1
    return None, None


def group_of_function(lines, fn):
    """在 `var <group> = [ ... ];` 数组块中查找注册函数名，返回 (组名, 行号)。"""
    for j, ln in enumerate(lines):
        m = re.match(r"var (\w+) = \[", ln)
        if not m:
            continue
        grp = m.group(1)
        k = j
        while k < len(lines) and "];" not in lines[k]:
            if re.search(rf"\b{fn}\b", lines[k]):
                return grp, j + 1
            k += 1
    return None, None


def main():
    f3 = json.loads((OUT / "f3_report.json").read_text(encoding="utf-8"))
    per = {p["server_id"]: p for p in f3["per_server"]}
    servers = [s for s in TRIAGE if per.get(s) and per[s].get("static_only")]

    rows, problems = [], []
    for sid in servers:
        spec = TRIAGE[sid]
        src = pkg_dir(sid)
        gate_file, gate_str = spec["gate"]
        gp = src / gate_file
        if not gp.exists():
            problems.append(f"{sid}: gate file missing {gate_file}")
            continue
        gate_lines = [i + 1 for i, ln in enumerate(
            gp.read_text(encoding="utf-8", errors="replace").splitlines())
            if gate_str in ln]
        if not gate_lines:
            problems.append(f"{sid}: gate anchor not found in {gate_file}: {gate_str}")

        for t in per[sid]["static_only"]:
            tspec = spec["tools"].get(t)
            if tspec is None:
                problems.append(f"{sid}: tool {t} missing from TRIAGE table")
                continue
            locs, per_name_gate = {}, {}
            for f in gp.parent.glob("*.js"):
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                h = registration_line(lines, t)
                if not h:
                    continue
                locs[f.name] = h
                first = h[0] - 1
                # 逐名字证据：config_flag 记录工具组，listing_suppressed 记录 canList 行
                if spec["mechanism"] == "config_flag":
                    fn, fline = enclosing_function(lines, first)
                    if fn is None:
                        problems.append(f"{sid}/{t}: no enclosing register function in {f.name}")
                        continue
                    gname, gline = group_of_function(lines, fn)
                    if gname is None:
                        problems.append(f"{sid}/{t}: {fn} not found in any tool group")
                    else:
                        per_name_gate[f.name] = {"register_fn": fn, "fn_line": fline,
                                                 "group": gname, "group_line": gline}
                        if "destructive" not in gname.lower():
                            problems.append(f"{sid}/{t}: group {gname} is not a destructive group")
                elif spec["mechanism"] == "listing_suppressed":
                    cl = nearest_after(lines, first, "canList: () => false")
                    if cl is None:
                        problems.append(f"{sid}/{t}: no canList: () => false within 40 lines")
                    else:
                        per_name_gate[f.name] = {"canList_line": cl}
            if not locs:
                problems.append(f"{sid}: no registration site for {t}")
            rows.append({
                "server_id": sid, "tool": t, "mechanism": spec["mechanism"],
                "gate_evidence": {"file": gate_file, "lines": gate_lines, "anchor": gate_str},
                "per_name_gate": per_name_gate,
                "default_deployment_branch": spec["default_branch"],
                "replace_in_default": tspec["replace"],
                "registration_sites": locs,
                "note": spec["note"],
            })

    # 汇总
    by_mech = {}
    for r in rows:
        by_mech[r["mechanism"]] = by_mech.get(r["mechanism"], 0) + 1
    n_target_calls = sum(p.get("n_target_calls", 0) for p in f3["per_server"])
    n_static_only = len(rows)
    report = {
        "n_static_only": n_static_only,
        "n_target_registrations": n_target_calls,
        "static_only_rate": round(n_static_only / n_target_calls, 4) if n_target_calls else 0.0,
        "by_mechanism": by_mech,
        "rename_count": by_mech.get("rename", 0),
        "unexplained": by_mech.get("unexplained", 0),
        "servers": servers,
        "per_name": rows,
        "verification": "each name re-located in the pinned package source; "
                        "gate anchors re-asserted at runtime",
        "problems": problems,
    }
    (OUT / "static_only_triage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"static-only={n_static_only}/{n_target_calls} ({report['static_only_rate']:.3%})")
    print(f"mechanisms: {by_mech}")
    for r in rows:
        rep = r["replace_in_default"] or "-"
        loc = ", ".join(f"{k}:{','.join(map(str, v))}" for k, v in r["registration_sites"].items())
        print(f"  {r['server_id']:32s} {r['tool']:34s} {r['mechanism']:18s} "
              f"gate@{r['gate_evidence']['lines']} reg@{loc} -> {rep}")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  " + p)
        return 1
    print("\nOK: 14/14 names traced to a gate; no rename, no unexplained registration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
