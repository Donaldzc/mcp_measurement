# -*- coding: utf-8 -*-
"""Regenerate flag-dependent figures under v2 checker labels.
Incorporates reviewer fixes:
  m27: tool-level and server-level rates split into two subplots (own y-axes)
  m28: 95% intervals on prevalence bars (cluster bootstrap) and deployability
       bars (Wilson)
  m30: no in-figure titles (captions carry the information)
  m30b: heatmap row labels abbreviated to <=24 chars; ordering stated in caption
Outputs PDF+PNG into sandbox/output/figures (overwrites v1 figures).
"""
import csv, json, math, pathlib, random, statistics
from collections import defaultdict
import matplotlib
matplotlib.use("agg")
import matplotlib.pyplot as plt
import numpy as np

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
FIG = OUT / "figures"

plt.rcParams["text.usetex"] = True
plt.rcParams["font.family"] = "serif"
plt.rcParams["font.serif"] = ["Times New Roman"]
plt.rcParams["mathtext.fontset"] = "stix"
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 300
plt.rcParams["savefig.dpi"] = 300
plt.rcParams["savefig.bbox"] = "tight"

f4 = list(csv.DictReader((OUT / "f4_all_v2.csv").open(encoding="utf-8")))
e6 = json.loads((OUT / "e6_rq4_report.json").read_text(encoding="utf-8"))
e7m = {r["server"]: [int(r[k]) for k in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "I1", "I2")]
       for r in csv.DictReader((OUT / "analysis" / "e7_server_rule_matrix_v2.csv").open(encoding="utf-8"))}
ok_ids = sorted(e7m)
rows_by_s = defaultdict(list)
for r in f4:
    rows_by_s[r["server"]].append(r)
CATS = ["C1", "C2", "C3", "C4", "C5", "C6"]


def save(fig, name):
    fig.savefig(FIG / f"{name}.pdf")
    fig.savefig(FIG / f"{name}.png")
    plt.close(fig)


def pct(xs, p):
    k = (len(xs) - 1) * p / 100
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def wilson(x, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = x / n
    den = 1 + z * z / n
    ctr = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0, ctr - half), min(1, ctr + half))


# ── Fig 2 v2: prevalence, split subplots + bootstrap CIs (m27, m28, m30) ──
def fig2_f4_prevalence():
    tool_rates = [sum(int(r[f"A{i}"]) for r in f4) / len(f4) * 100 for i in range(1, 7)]
    server_rates = [sum(1 for s in ok_ids if any(int(r[f"A{i}"]) for r in rows_by_s[s])) / 32 * 100
                    for i in range(1, 7)]
    # tool-level server-cluster bootstrap CIs (2000 iters, seed 42)
    rng = random.Random(42)
    boot = [[] for _ in range(6)]
    units = {s: (len(rows_by_s[s]), [sum(int(r[f"A{i}"]) for r in rows_by_s[s]) for i in range(1, 7)])
             for s in ok_ids}
    for _ in range(2000):
        samp = [ok_ids[rng.randrange(32)] for _ in range(32)]
        den = sum(units[s][0] for s in samp)
        for j in range(6):
            num = sum(units[s][1][j] for s in samp)
            boot[j].append(num / den * 100 if den else 0.0)
    cis = []
    for j in range(6):
        boot[j].sort()
        cis.append((tool_rates[j] - pct(boot[j], 2.5), pct(boot[j], 97.5) - tool_rates[j]))
    cis = np.array(cis).T
    # server-level Wilson CIs (n=32)
    scis = []
    for j in range(6):
        x = sum(1 for s in ok_ids if any(int(r[f"A{j+1}"]) for r in rows_by_s[s]))
        lo, hi = wilson(x, 32)
        scis.append((server_rates[j] - lo * 100, hi * 100 - server_rates[j]))
    scis = np.array(scis).T

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 3.0))
    x = np.arange(6)
    b1 = ax1.bar(x, tool_rates, 0.62, color="#4c78a8", edgecolor="black", linewidth=0.5,
                 yerr=cis, capsize=2.5, error_kw={"elinewidth": 0.7, "ecolor": "#444444"})
    ax1.set_xticks(x); ax1.set_xticklabels(CATS, fontsize=8)
    ax1.set_ylabel(r"Tool-level rate (\%)")
    ax1.set_title(r"(a) Per tool ($n=885$)", fontsize=9)
    ax1.set_ylim(0, max(r + e for r, e in zip(tool_rates, cis[1])) * 1.12)
    ax1.grid(axis="y", alpha=0.3)
    for i, b in enumerate(b1):
        ax1.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.35,
                 f"{b.get_height():.1f}", ha="center", va="bottom", fontsize=7)
    b2 = ax2.bar(x, server_rates, 0.62, color="#f58518", edgecolor="black", linewidth=0.5,
                 yerr=scis, capsize=2.5, error_kw={"elinewidth": 0.7, "ecolor": "#444444"})
    ax2.set_xticks(x); ax2.set_xticklabels(CATS, fontsize=8)
    ax2.set_ylabel(r"Server-level rate (\%)")
    ax2.set_title(r"(b) Per server ($n=32$)", fontsize=9)
    ax2.set_ylim(0, max(r + e for r, e in zip(server_rates, scis[1])) * 1.12)
    ax2.grid(axis="y", alpha=0.3)
    for i, b in enumerate(b2):
        ax2.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.8,
                 f"{b.get_height():.1f}", ha="center", va="bottom", fontsize=7)
    save(fig, "fig2_f4_prevalence")


def _short_server(sid):
    s = sid
    prefix = "modelcontextprotocol_server_"
    if s.startswith(prefix):
        s = "mcp_" + s[len(prefix):]
    for suf in ("_mcp_server", "_mcp", "_server"):
        if s.endswith(suf):
            s = s[:-len(suf)]
            break
    s = s.replace("_", "-")
    return s if len(s) <= 34 else s[:33] + "…"


def _row_order():
    pairs = sorted(((s, v) for s, v in e7m.items()), key=lambda p: (-sum(p[1]), p[0]))
    return [p[0] for p in pairs]


# ── Fig 3 v2: per-server category density heatmap (v2 labels) ──
def fig3_f4_heatmap():
    order = _row_order()
    mat = np.zeros((len(order), 6))
    for i, s in enumerate(order):
        rs = rows_by_s[s]
        for j in range(6):
            mat[i, j] = sum(int(r[f"A{j+1}"]) for r in rs) / len(rs)
    servers = [_short_server(s) for s in order]
    fig, ax = plt.subplots(figsize=(3.45, 4.02))
    fig.subplots_adjust(left=0.42, right=0.93, top=0.925, bottom=0.105)
    im = ax.imshow(mat, cmap="Reds", aspect="auto", vmin=0, vmax=1)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(servers, fontsize=5.6)
    ax.set_xticks(range(6))
    ax.set_xticklabels(CATS, fontsize=7.2)
    ax.xaxis.set_ticks_position("top")
    ax.xaxis.set_label_position("top")
    ax.tick_params(axis="x", pad=2, length=0)
    for i in range(len(order)):
        for j in range(6):
            v = mat[i, j]
            if v <= 0:
                ax.text(j, i, "0", ha="center", va="center", fontsize=5.6, color="#9a9a9a")
            elif v < 0.05:
                ax.text(j, i, f"{v*100:.1f}", ha="center", va="center", fontsize=5.6, color="#6f6f6f")
            else:
                ax.text(j, i, f"{v*100:.0f}", ha="center", va="center",
                        fontsize=6.0, color="white" if v > 0.55 else "black")
    ax.set_xticks(np.arange(-0.5, 6, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(order), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.2)
    ax.tick_params(which="both", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    cax = fig.add_axes([0.42, 0.035, 0.40, 0.018])
    cbar = fig.colorbar(im, cax=cax, orientation="horizontal")
    cbar.set_ticks([0, 0.5, 1.0])
    cbar.ax.tick_params(labelsize=5.8, length=0)
    cbar.outline.set_visible(False)
    save(fig, "fig3_f4_heatmap")


# ── Fig 5 (drift): unchanged data, in-figure title removed (m30) ──
def fig5_drift():
    ev = e6["E6_drift"]["events"]
    cats = ["D3_deps", "D5_maintainer", "D4_scripts", "D2_keywords", "D1_desc", "D4_new_postinstall"]
    labels = ["D3 Dependencies", "D5 Maintainer", "D4 Scripts", "D2 Keywords", "D1 Description", "D4b New postinstall"]
    rates = [ev[c]["rate_per_pair"] * 100 for c in cats]
    colors = ["#4c78a8"] * 5 + ["#e45756"]
    fig, ax = plt.subplots(figsize=(7, 3.0))
    bars = ax.barh(range(len(cats)), rates, color=colors, edgecolor="black", linewidth=0.5)
    ax.set_yticks(range(len(cats))); ax.set_yticklabels(labels)
    ax.set_xlabel(r"Events per version pair (\%)")
    ax.invert_yaxis()
    for b in bars:
        ax.text(b.get_width() + 0.05, b.get_y() + b.get_height() / 2,
                f"{b.get_width():.2f}\\%", va="center", fontsize=8)
    ax.grid(axis="x", alpha=0.3)
    save(fig, "fig5_drift")


# ── fig6_ci_vs_manual: REMOVED in review round 3 (R6, length) --- the three bars carried no number Section 5.6 does not state.
#    The numbers it plotted stay in the text of Section 5.6 / 5.7.



# ── Fig 7 v2: server × rule heatmap from the v2 matrix ──
def fig7_e7_heatmap():
    rule_ids = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "I1", "I2"]
    order = _row_order()
    servers = [_short_server(s) for s in order]
    mat = np.array([e7m[s] for s in order])
    fig, ax = plt.subplots(figsize=(3.45, 3.65))
    fig.subplots_adjust(left=0.42, right=0.93, top=0.88, bottom=0.095)
    im = ax.imshow(mat, cmap="Greys", aspect="auto", vmin=0, vmax=1)
    ax.set_yticks(range(len(servers)))
    ax.set_yticklabels(servers, fontsize=5.6)
    ax.set_xticks(range(len(rule_ids)))
    ax.set_xticklabels(rule_ids, fontsize=7, rotation=90, va="bottom")
    ax.xaxis.set_ticks_position("top")
    ax.xaxis.set_label_position("top")
    ax.tick_params(axis="x", pad=2, length=0)
    ax.set_xticks(np.arange(-0.5, len(rule_ids), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(servers), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.2)
    ax.tick_params(which="both", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    cax = fig.add_axes([0.42, 0.028, 0.40, 0.018])
    cbar = fig.colorbar(im, cax=cax, orientation="horizontal")
    cbar.set_ticks([0, 1])
    cbar.set_ticklabels(["no", "yes"])
    cbar.ax.tick_params(labelsize=5.8, length=0)
    cbar.outline.set_visible(False)
    save(fig, "fig7_e7_heatmap")


# ── fig8_partial: REMOVED in review round 3 (R6, length) --- the confounder null result is fully reported in Section 5.7 text.
#    The numbers it plotted stay in the text of Section 5.6 / 5.7.



if __name__ == "__main__":
    fig2_f4_prevalence(); print("ok fig2")
    fig3_f4_heatmap(); print("ok fig3")
    fig5_drift(); print("ok fig5")
    fig7_e7_heatmap(); print("ok fig7")
