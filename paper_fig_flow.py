"""Flow figure for the prevalence chain (R8 review: roadmap, no middle steps).

885 tools -> 208 any-category -> 69 de-C2 -> 11.1-28.1 partially re-estimated
-> 6.2-21.0 directive-only (primary estimand). Side branch: 172 C2
sibling-mentions. No in-figure title (captions carry the information).
PDF+PNG to paper/figures/.
"""
import pathlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

PAPER_FIG = pathlib.Path(__file__).resolve().parent.parent / "paper" / "figures"
PAPER_FIG.mkdir(exist_ok=True)
plt.rcParams.update({"font.size": 8.5, "font.family": "DejaVu Sans"})

BOXES = ["885 tools\n32 servers", "208 flagged\n23.5%", "69 de-C2\n7.8%",
         "11.1\u201328.1\n1.3\u20133.2%", "6.2\u201321.0\n0.7\u20132.4%"]
X = [0.02, 0.225, 0.43, 0.635, 0.84]
W = 0.145
ARROW_LABELS = ["rule coverage", "drop C2", "disjoint\nre-estimation", "directive only\n(C1, C3, C6)"]

fig, ax = plt.subplots(figsize=(6.9, 1.9))
ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")

for i, (label, x) in enumerate(zip(BOXES, X)):
    primary = (i == 4)
    ax.add_patch(FancyBboxPatch((x, 0.50), W, 0.36, boxstyle="round,pad=0.012",
                                linewidth=1.3 if primary else 0.9,
                                edgecolor="#1a5c1a" if primary else "#333333",
                                facecolor="#e9f3e9" if primary else "#f2f2f2"))
    ax.text(x + W / 2, 0.68, label, ha="center", va="center", fontsize=8.6)
    if primary:
        ax.text(x + W / 2, 0.435, "primary estimand", ha="center", va="center",
                fontsize=7.0, style="italic", color="#1a4a1a")

for i in range(4):
    ax.add_patch(FancyArrowPatch((X[i] + W + 0.013, 0.68), (X[i + 1] - 0.013, 0.68),
                                 arrowstyle="-|>", mutation_scale=11, linewidth=1.0,
                                 color="#333333"))
    ax.text((X[i] + W + X[i + 1]) / 2, 0.94, ARROW_LABELS[i], ha="center", va="center",
            fontsize=7.2, style="italic", color="#444444")

ax.add_patch(FancyArrowPatch((X[1] + W / 2, 0.50), (X[1] + W / 2, 0.30),
                             arrowstyle="-|>", mutation_scale=11, linewidth=1.0,
                             color="#777777"))
ax.add_patch(FancyBboxPatch((X[1] - 0.058, 0.03), W + 0.116, 0.25,
                            boxstyle="round,pad=0.012", linewidth=0.9, linestyle="--",
                            edgecolor="#777777", facecolor="#ffffff"))
ax.text(X[1] + W / 2, 0.155, "172 C2 sibling mentions (19.4%)\nexcluded as descriptive",
        ha="center", va="center", fontsize=8.0, color="#333333")

fig.savefig(PAPER_FIG / "fig8_chain_flow.pdf", bbox_inches="tight")
fig.savefig(PAPER_FIG / "fig8_chain_flow.png", bbox_inches="tight", dpi=300)
print("saved", PAPER_FIG / "fig8_chain_flow.pdf")
