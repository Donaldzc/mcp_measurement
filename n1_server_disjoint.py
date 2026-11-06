"""N1 (review follow-up): server-level estimates under disjoint precision.

Recomputes the two published server-level quantities -- deC2_server_pct
(the paper's conservative de-C2 headline end) and server_independent_pct --
substituting disjoint double-coded overturn shares for C1/C3/C6 (C2/C4/C5
unchanged), with Monte Carlo propagation over the same Beta posteriors as
the frozen blind-audit analysis (seed declared). Also reports the
description-length comparison between the original frame (ranks 1-60) and
the extension frame (ranks 61-180) for the N3 homogeneity discussion.

Reads : sandbox/output/f4_all_v2.csv, sandbox/output/f4_ext_v2.csv
Writes: sandbox/output/analysis/n1_server_disjoint.json
"""
import csv, json, pathlib, random, statistics

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
AN = OUT / "analysis"
CATS = ["A1", "A2", "A3", "A4", "A5", "A6"]
SEED = 20261104          # review-follow-up run date, YYYYMMDD
DRAWS = 100_000

# overturn share f_c: tuned (as published) vs disjoint plug-in
F_TUNED = {"A1": 0.40, "A2": 0.6875, "A3": 0.50, "A4": 0.70, "A5": 1.00, "A6": 4.0 / 7.0}
F_DISJ = dict(F_TUNED, A1=13 / 15, A3=6 / 15, A6=5 / 6)          # 2/15, 9/15, 1/6 confirmed
# Beta(1+confirmed, 1+overturned) posteriors on the CONFIRMED probability
KEEP_POST = {"A1": (3, 14), "A3": (10, 7), "A6": (2, 6),          # disjoint
             "A2": (6, 12), "A4": (7, 15), "A5": (1, 2)}          # tuned (unchanged)

def load(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

rows = load(OUT / "f4_all_v2.csv")
tools = []
for r in rows:
    flags = [c for c in CATS if r[c] == "1"]
    non_c2 = [c for c in flags if c != "A2"]
    tools.append({"server": r["server"], "flags": flags, "deC2": bool(non_c2), "non_c2": non_c2})
n_srv = len({t["server"] for t in tools})

def estimates(f_c):
    for t in tools:
        t["p"] = 1.0 - _prod(f_c[c] for c in t["flags"]) if t["flags"] else 0.0
        t["p_deC2"] = 1.0 - _prod(f_c[c] for c in t["non_c2"]) if t["non_c2"] else 0.0
    by_server = {}
    for t in tools:
        by_server.setdefault(t["server"], []).append(t)
    dec2_srv = sum(1 - _prod(1 - t["p_deC2"] for t in ts if t["deC2"]) for ts in by_server.values())
    dec2_maxpos = sum(max((t["p_deC2"] for t in ts), default=0.0) for ts in by_server.values())
    dec2_disjoint = sum(min(1.0, sum(t["p_deC2"] for t in ts)) for ts in by_server.values())
    srv_indep = sum(1 - _prod(1 - t["p"] for t in ts) for ts in by_server.values())
    srv_maxpos = sum(max(t["p"] for t in ts) for ts in by_server.values())
    srv_disjoint = sum(min(1.0, sum(t["p"] for t in ts)) for ts in by_server.values())
    return {"deC2_server_pct": round(100 * dec2_srv / n_srv, 2),
            "deC2_maxpos_pct": round(100 * dec2_maxpos / n_srv, 2),
            "deC2_disjoint_pct": round(100 * dec2_disjoint / n_srv, 2),
            "server_independent_pct": round(100 * srv_indep / n_srv, 2),
            "server_maxpos_pct": round(100 * srv_maxpos / n_srv, 2),
            "server_disjoint_pct": round(100 * srv_disjoint / n_srv, 2)}

def _prod(xs):
    out = 1.0
    for x in xs:
        out *= x
    return out

rng = random.Random(SEED)
mc = {"deC2_server_pct": [], "deC2_maxpos_pct": [], "deC2_disjoint_pct": [],
      "server_independent_pct": [], "server_maxpos_pct": [], "server_disjoint_pct": []}
for _ in range(DRAWS):
    f = {c: 1.0 - rng.betavariate(*KEEP_POST[c]) for c in CATS}
    e = estimates(f)
    for k in mc:
        mc[k].append(e[k])

def band(xs):
    xs = sorted(xs)
    return {"median": round(statistics.median(xs), 2),
            "ci95": [round(xs[int(0.025 * DRAWS)], 2), round(xs[int(0.975 * DRAWS)], 2)]}

ext = load(OUT / "f4_ext_v2.csv")
len_in = [len(r["description"]) for r in rows]
len_ex = [len(r["description"]) for r in ext]

result = {
    "seed": SEED, "draws": DRAWS,
    "f_c_tuned": F_TUNED, "f_c_disjoint": {k: round(v, 4) for k, v in F_DISJ.items()},
    "tuned_point": estimates(F_TUNED),
    "disjoint_point": estimates(F_DISJ),
    "disjoint_mc": {k: band(v) for k, v in mc.items()},
    "n3_frame_lengths": {
        "frame_1_60": {"n": len(len_in), "mean": round(statistics.mean(len_in), 1),
                       "median": statistics.median(len_in)},
        "frame_61_180": {"n": len(len_ex), "mean": round(statistics.mean(len_ex), 1),
                         "median": statistics.median(len_ex)},
    },
}
(AN / "n1_server_disjoint.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
