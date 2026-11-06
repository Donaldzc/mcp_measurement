# -*- coding: utf-8 -*-
"""论文级图表生成：RQ1-RQ5 + E6 + E7。

设计准则（S&P/CCS 期刊风格）：
  * 统一 6x4 英寸 + 300dpi + PDF/PNG 双输出
  * usetex=True（系统 LaTeX 可用），中文字体优先 Microsoft YaHei
  * 每张图标题含 caption 前缀 Fig. N —— 便于论文直接搬入
  * 数据来源标注 "F1-F4 冻结 / E6+RQ4 补采"
"""
import json, pathlib, math, statistics, csv
from collections import Counter, defaultdict
import matplotlib
matplotlib.use("agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
FIG = OUT / "figures"
FIG.mkdir(exist_ok=True)

# ── 字体配置 ──
plt.rcParams["text.usetex"] = True
plt.rcParams["font.family"] = "serif"
plt.rcParams["font.serif"] = ["Times New Roman"]
plt.rcParams["mathtext.fontset"] = "stix"
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 300
plt.rcParams["savefig.dpi"] = 300
plt.rcParams["savefig.bbox"] = "tight"
# 中文字体 fallback
_cn = [f for f in font_manager.findSystemFonts()
       if any(k in f.lower() for k in ["msyh", "simhei", "simsun", "dengxian"])]
CN_FONT = font_manager.FontProperties(fname=_cn[0], size=10) if _cn else None


def save(fig, name):
    fig.savefig(FIG / f"{name}.pdf")
    fig.savefig(FIG / f"{name}.png")
    plt.close(fig)


def cn_label(ax, txt):
    """usetex 下中文字段禁用，返回原文本。"""
    return txt


# ═══════════════════════════════════════════════════
# 加载全部冻结产物
# ═══════════════════════════════════════════════════
summ = json.loads((OUT / "summary_v7.json").read_text(encoding="utf-8"))
ok_ids = sorted({r["server_id"] for r in summ["results"] if r.get("ok")})
known = json.loads((BASE / "registry" / "known_servers.json").read_text(encoding="utf-8"))
known_by_sid = {d["server_id"]: d for d in known}
f4 = list(csv.DictReader((OUT / "f4_all.csv").open(encoding="utf-8")))
f3 = json.loads((OUT / "f3_report.json").read_text(encoding="utf-8"))
e6 = json.loads((OUT / "e6_rq4_report.json").read_text(encoding="utf-8"))
e7 = json.loads((OUT / "e7_report.json").read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════
# Fig 1 — RQ1 集中度：月下载分布 + top-10 份额
# ═══════════════════════════════════════════════════
def fig1_concentration():
    dls = sorted([d.get("downloads_monthly") or 0 for d in known], reverse=True)
    ranks = list(range(1, len(dls) + 1))
    top10_share = sum(dls[:10]) / sum(dls)
    dls_asc = sorted(dls)
    n = len(dls_asc)
    gini = (2 * sum(i * x for i, x in enumerate(dls_asc, 1)) / (n * sum(dls_asc))) - (n + 1) / n

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    # (a) rank-size log-log
    ax1.loglog(ranks, dls, "o", color="#1f77b4", markersize=5, zorder=3)
    ax1.set_xlabel("Rank (by monthly downloads)")
    ax1.set_ylabel("Monthly downloads")
    ax1.set_title(r"(a) Rank-size distribution ($n=60$)")
    ax1.grid(alpha=0.3, which="both")

    # (b) Lorenz curve（升序，曲线位于对角线下方）
    cum = np.cumsum(dls_asc) / sum(dls_asc)
    cum_x = np.linspace(0, 1, len(cum))
    ax2.plot(cum_x, cum, color="#d62728", linewidth=2, zorder=3)
    ax2.plot([0, 1], [0, 1], "k--", linewidth=1)
    ax2.fill_between(cum_x, cum, cum_x, alpha=0.15, color="#d62728")
    ax2.text(0.05, 0.88, f"Gini = {gini:.3f}", transform=ax2.transAxes, fontsize=10)
    ax2.text(0.05, 0.80, f"Top-10 share = {top10_share*100:.0f}\\%", transform=ax2.transAxes, fontsize=10)
    ax2.set_xlabel("Cumulative share of servers")
    ax2.set_ylabel("Cumulative share of downloads")
    ax2.set_title(r"(b) Lorenz curve (inequality)")
    ax2.set_xlim(0, 1); ax2.set_ylim(0, 1)
    ax2.set_aspect("equal")

    save(fig, "fig1_concentration")


# ═══════════════════════════════════════════════════
# Fig 2 — RQ2 F4 六类 prevalence（双口径：tool + server）
# ═══════════════════════════════════════════════════
def fig2_f4_prevalence():
    cats = ["C1", "C2", "C3", "C4", "C5", "C6"]
    labels = ["Direct\ncommand", "Cross-tool\nreference", "Embedded\nURL/code",
              "Credential\nrequest", "Sensitive\npath", "Hidden\ninstruction"]
    tool_rates = [sum(int(r[f"A{i}"]) for r in f4) / len(f4) * 100 for i in range(1, 7)]
    rows_by_s = defaultdict(list)
    for r in f4:
        rows_by_s[r["server"]].append(r)
    n_servers = len(ok_ids)
    server_rates = []
    for i in range(1, 7):
        cnt = sum(1 for s in ok_ids if any(int(r[f"A{i}"]) for r in rows_by_s.get(s, [])))
        server_rates.append(cnt / n_servers * 100)

    x = np.arange(len(cats))
    fig, ax = plt.subplots(figsize=(8, 4))
    w = 0.35
    bars1 = ax.bar(x - w / 2, tool_rates, w, label="Tool-level", color="#4c78a8",
                   edgecolor="black", linewidth=0.5)
    bars2 = ax.bar(x + w / 2, server_rates, w, label="Server-level", color="#f58518",
                   edgecolor="black", linewidth=0.5)
    for bars in [bars1, bars2]:
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.6,
                    f"{b.get_height():.1f}\\%", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels(cats)
    ax.set_ylabel(r"Prevalence (\%)")
    ax.set_title(r"Instruction-like prevalence by category")
    ax.legend(loc="upper right")
    ax.set_ylim(0, max(server_rates) * 1.25)
    ax.grid(axis="y", alpha=0.3)
    save(fig, "fig2_f4_prevalence")


# ═══════════════════════════════════════════════════
# Fig 3 — F4 × server 热力图
# ═══════════════════════════════════════════════════
def fig3_f4_heatmap():
    """F4 逐 server 注入面密度（单栏版，论文里作 Figure 8）。

    按 print size 生成：单栏 3.45 in 宽、32 行竖排，行高约 8 pt，标签 5.8 pt；
    图按 1:1 宽度插入 \\columnwidth 时可读。行序与 Fig 9 一致，便于逐行对照。
    取值 = 该 server 上带该类别标记的工具占比（0-1），格内数字为整数百分比。
    """
    rows_by_s = defaultdict(list)
    for r in f4:
        rows_by_s[r["server"]].append(r)
    order = [p[0] for p in _e7_rows()]          # 与 Fig 9 相同的行序
    cats = ["C1", "C2", "C3", "C4", "C5", "C6"]

    mat = np.zeros((len(order), len(cats)))
    for i, s in enumerate(order):
        rs = rows_by_s.get(s, [])
        if not rs:
            continue
        for j in range(len(cats)):
            mat[i, j] = sum(int(r[f"A{j+1}"]) for r in rs) / len(rs)

    servers = [_row_label(s) for s in order]

    fig, ax = plt.subplots(figsize=(3.45, 5.6))
    fig.subplots_adjust(left=0.455, right=0.93, top=0.935, bottom=0.085)

    im = ax.imshow(mat, cmap="Reds", aspect="auto", vmin=0, vmax=1)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(servers, fontsize=7.2)
    ax.set_xticks(range(len(cats)))
    ax.set_xticklabels(cats, fontsize=8)
    ax.xaxis.set_ticks_position("top")
    ax.xaxis.set_label_position("top")
    ax.tick_params(axis="x", pad=2, length=0)
    for i in range(len(order)):
        for j in range(len(cats)):
            v = mat[i, j]
            if v >= 0.05:
                ax.text(j, i, f"{v*100:.0f}", ha="center", va="center",
                        fontsize=6.4, color="white" if v > 0.55 else "black")
    ax.set_xticks(np.arange(-0.5, len(cats), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(order), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.2)
    ax.tick_params(which="both", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    cax = fig.add_axes([0.455, 0.030, 0.40, 0.016])
    cbar = fig.colorbar(im, cax=cax, orientation="horizontal")
    cbar.set_ticks([0, 0.5, 1.0])
    cbar.ax.tick_params(labelsize=7, length=0)
    cbar.outline.set_visible(False)
    save(fig, "fig3_f4_heatmap")


# ═══════════════════════════════════════════════════
# Fig 4 — REMOVED (review round 3, R5): the recall-by-registration-style bar
# chart carried exactly the three rows of tab:f3 (two of them zero) and no
# information beyond the table, so the panel was dropped and the space given
# to the length-normalized strata (tab:length). The style numbers stay in
# f3["style_detail"] and in tab:f3.
# ═══════════════════════════════════════════════════


# ═══════════════════════════════════════════════════
# Fig 5 — E6 漂移事件谱
# ═══════════════════════════════════════════════════
def fig5_drift():
    ev = e6["E6_drift"]["events"]
    cats = ["D3_deps", "D5_maintainer", "D4_scripts", "D2_keywords", "D1_desc", "D4_new_postinstall"]
    labels = ["D3 Dependencies", "D5 Maintainer", "D4 Scripts", "D2 Keywords", "D1 Description", "D4b New postinstall"]
    rates = [ev[c]["rate_per_pair"] * 100 for c in cats]
    colors = ["#4c78a8"] * 5 + ["#e45756"]  # 最后一个红
    fig, ax = plt.subplots(figsize=(7, 3.5))
    bars = ax.barh(range(len(cats)), rates, color=colors, edgecolor="black", linewidth=0.5)
    ax.set_yticks(range(len(cats))); ax.set_yticklabels(labels)
    ax.set_xlabel(r"Events per version pair (\%)")
    ax.set_title(r"Drift event spectrum (4,479 version pairs)")
    ax.invert_yaxis()
    for b in bars:
        ax.text(b.get_width() + 0.05, b.get_y() + b.get_height() / 2,
                f"{b.get_width():.2f}\%", va="center", fontsize=8)
    ax.grid(axis="x", alpha=0.3)
    save(fig, "fig5_drift")


# ═══════════════════════════════════════════════════
# Fig 6 — REMOVED (review round 3, R6): CI-vs-manual bars repeated numbers
# that Section 5.6 states in the text.
# ═══════════════════════════════════════════════════



# ═══════════════════════════════════════════════════
# Fig 7 — E7 server × rule 热力图
# ═══════════════════════════════════════════════════
def _short_server(sid):
    """行标签缩写：只做可解释的截断，不改接口/规则语义。

    `modelcontextprotocol_server_*` 是官方参考实现，记为 `mcp-*`；
    去掉冗余的 `_mcp` / `_server` 后缀；其余保持原样。完整 id 在
    e7_server_rule_matrix.csv 里有据可查。
    """
    s = sid
    prefix = "modelcontextprotocol_server_"
    if s.startswith(prefix):
        s = "mcp_" + s[len(prefix):]
    for suf in ("_mcp_server", "_mcp", "_server"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s.replace("_", "-")


# ── 任务5：未确认 flag 的 server 匿名化 ─────────────────────
# 只有至少一个工具在双人盲标注中被确认存在 C1–C6 标记的 server 保留真名；
# 其余（含未被抽进确认样本的 server）在 Fig 3 / Fig 4 中记为 S01…。
_AUDIT = list(csv.DictReader((OUT / "f4_sample_annotated.csv").open(encoding="utf-8")))
AUDIT_CONFIRMED = {
    r["server"] for r in _AUDIT
    if any((r.get(c) or "").strip() == "1"
           for c in [f"A{i}" for i in range(1, 7)] + [f"B{i}" for i in range(1, 7)])
}
_UNCONFIRMED = sorted({r["server"] for r in _AUDIT} | set(ok_ids)) 
_UNCONFIRMED = [s for s in _UNCONFIRMED if s not in AUDIT_CONFIRMED]
ANON_LABEL = {s: f"S{i:02d}" for i, s in enumerate(_UNCONFIRMED, 1)}


def _row_label(sid):
    """图 3/图 4 的行标签：已确认的保留可识别名，未确认的匿名。"""
    return _short_server(sid) if sid in AUDIT_CONFIRMED else ANON_LABEL[sid]


def _e7_rows():
    """读 E7 服务器×规则矩阵，按违反规则数降序返回 [(server_id, 9 维向量)]。

    Fig 7（规则违反矩阵）与 Fig 9（F4 密度矩阵）共用同一行序，
    便于两张图逐行对照；排序属展示性调整，不改变任何取值。
    """
    rules = e7["rules"]
    n_rules = len(rules)
    rows_txt = (OUT / "e7_server_rule_matrix.csv").read_text(encoding="utf-8").splitlines()[1:]
    pairs = []
    for line in rows_txt:
        parts = line.split(",")
        if parts[0] in ok_ids:
            pairs.append((parts[0], [int(x) for x in parts[1:n_rules + 1]]))
    pairs.sort(key=lambda p: (-sum(p[1]), p[0]))
    return pairs


def fig7_e7_heatmap():
    """E7 热力图（单栏版）。

    按 print size 生成：单栏 3.45 in 宽、32 行竖排，行高约 8 pt，server 标签
    5.8 pt、规则标签 6 pt 竖排于顶部；图按 1:1 宽度插入 \\columnwidth 时可读。
    """
    rule_ids = [r["rule_id"] for r in e7["rules"]]
    pairs = _e7_rows()

    servers = [_row_label(p[0]) for p in pairs]
    mat = np.array([p[1] for p in pairs])

    fig, ax = plt.subplots(figsize=(3.45, 5.2))
    fig.subplots_adjust(left=0.455, right=0.93, top=0.90, bottom=0.075)

    im = ax.imshow(mat, cmap="Greys", aspect="auto", vmin=0, vmax=1)
    ax.set_yticks(range(len(servers)))
    ax.set_yticklabels(servers, fontsize=7.2)
    ax.set_xticks(range(len(rule_ids)))
    ax.set_xticklabels(rule_ids, fontsize=7.5, rotation=90, va="bottom")
    ax.xaxis.set_ticks_position("top")
    ax.xaxis.set_label_position("top")
    ax.tick_params(axis="x", pad=2, length=0)
    ax.set_xticks(np.arange(-0.5, len(rule_ids), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(servers), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.2)
    ax.tick_params(which="both", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    cax = fig.add_axes([0.455, 0.024, 0.40, 0.016])
    cbar = fig.colorbar(im, cax=cax, orientation="horizontal")
    cbar.set_ticks([0, 1])
    cbar.set_ticklabels(["no", "yes"])
    cbar.ax.tick_params(labelsize=7, length=0)
    cbar.outline.set_visible(False)
    save(fig, "fig7_e7_heatmap")


# ═══════════════════════════════════════════════════
# Fig 8 — 偏相关散点对比（控制版本数前后）
# ═══════════════════════════════════════════════════
# fig8_partial — REMOVED in review round 3 (R6, length): numbers stay in Section 5.7 text.
# ═══════════════════════════════════════════════════





# ═══════════════════════════════════════════════════
# 主程序
# ═══════════════════════════════════════════════════
def main():
    print("Generating paper figures...")
    fig1_concentration(); print("  ✓ Fig1")
    fig2_f4_prevalence(); print("  ✓ Fig2")
    fig3_f4_heatmap(); print("  ✓ Fig3")
    fig5_drift(); print("  ✓ Fig5")
    fig7_e7_heatmap(); print("  ✓ Fig7")
    pngs = sorted(FIG.glob("*.png"))
    pdfs = sorted(FIG.glob("*.pdf"))
    print(f"\n✓ 生成完毕：{len(pngs)} PNG + {len(pdfs)} PDF in {FIG}")
    for p in pngs:
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
