"""R5 review follow-up computations.

M2(ii) : length-stratified reweighting of the disjoint C3/C6 precision onto
         the original frame's length strata (cut points 52/174/399 chars,
         same as Table tab:length). Two weightings are reported:
         flag-weighted (distribution of the original frame's C3/C6-flagged
         tools across strata) and equal-quarter (the frame's marginal length
         distribution). Per-stratum shares use a Beta(1,1) smoothed estimate;
         a stratum with no audit items falls back to the pooled audit share.
M2(iii): C4 sensitivity -- headline de-C2 point with C4 keep share 0.30
         (as used, overturn 14/20) vs 0.25 (raw census 5/20), plus the C4
         keep share's own Beta(7,15) 95% CrI propagated as a range.
M4     : pooled C1 estimate (3/5 tuned + 2/15 disjoint = 5/20), Beta(6,16)
         90/95% CrI and P(p > 0.60) against the tuned value 0.60.

Reads : blind_audit/SEALED_mapping_key.csv, blind_audit/adjudicated.csv,
        sandbox/output/f4_all_v2.csv, sandbox/output/f4_ext_v2.csv
Writes: sandbox/output/analysis/r5_followup.json
"""
import csv, json, pathlib, random, statistics

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
AUD = BASE / "blind_audit"
SEED = 20261104
DRAWS = 200_000
CUTS = (52, 174, 399)          # Q1 <=52, Q2 53-174, Q3 175-399, Q4 >=400


def load(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def stratum(n):
    if n <= CUTS[0]:
        return 0
    if n <= CUTS[1]:
        return 1
    if n <= CUTS[2]:
        return 2
    return 3


corpus = {}
for name in ("f4_all_v2.csv", "f4_ext_v2.csv"):
    for r in load(OUT / name):
        corpus[r["id"]] = r
orig = load(OUT / "f4_all_v2.csv")

key = {r["sheet_id"]: r for r in load(AUD / "SEALED_mapping_key.csv")}
adj = {r["sheet_id"]: r for r in load(AUD / "adjudicated.csv")}

# --- original-frame flag distribution across strata (weights) -------------
flag_strata = {"C3": [0, 0, 0, 0], "C6": [0, 0, 0, 0]}
flag_col = {"C3": "A3", "C6": "A6"}
for r in orig:
    s = stratum(len(r["description"]))
    for c, col in flag_col.items():
        if r[col] == "1":
            flag_strata[c][s] += 1

# --- disjoint audit items: stratum x outcome --------------------------------
audit = {"C3": {}, "C6": {}}     # cat -> stratum -> [n, confirmed]
for sid, k in key.items():
    st = k["stratum"]
    cat = {"C3_ext": "C3", "C6_ext": "C6"}.get(st)
    if cat is None:
        continue
    row = corpus[k["id"]]
    s = stratum(len(row["description"]))
    n, c = audit[cat].get(s, (0, 0))
    audit[cat][s] = (n + 1, c + int(adj[sid][cat] == "1"))


def reweight(cat):
    pooled_n = sum(n for n, _ in audit[cat].values())
    pooled_c = sum(c for _, c in audit[cat].values())
    pooled_sm = (1 + pooled_c) / (2 + pooled_n)
    per = []
    for s in range(4):
        n, c = audit[cat].get(s, (0, 0))
        per.append({"stratum": s + 1, "n": n, "confirmed": c,
                    "p_raw": round(c / n, 4) if n else None,
                    "p_smooth": round((1 + c) / (2 + n), 4) if n else None})
    w_flag = [x / sum(flag_strata[cat]) for x in flag_strata[cat]]
    pooled_raw = pooled_c / pooled_n
    rw_flag = sum(w * (p["p_smooth"] if p["p_smooth"] is not None else pooled_sm)
                  for w, p in zip(w_flag, per))
    rw_flag_raw = sum(w * (p["p_raw"] if p["p_raw"] is not None else pooled_raw)
                      for w, p in zip(w_flag, per))
    present = [p for p in per if p["p_smooth"] is not None]
    rw_equal = sum(p["p_smooth"] for p in present) / 4.0
    # renormalize: strata with no audit items contribute the pooled share
    rw_equal += (4 - len(present)) * pooled_sm / 4.0
    return {"flags_per_stratum": flag_strata[cat], "w_flag": [round(w, 4) for w in w_flag],
            "per_stratum": per, "pooled_raw": round(pooled_raw, 4),
            "reweighted_flag": round(rw_flag, 4), "reweighted_flag_raw": round(rw_flag_raw, 4),
            "reweighted_equal": round(rw_equal, 4)}


rw = {c: reweight(c) for c in ("C3", "C6")}

# --- M2(iii): C4 sensitivity on the de-C2 headline point --------------------
CATS = ["A1", "A2", "A3", "A4", "A5", "A6"]
F_DISJ = {"A1": 13 / 15, "A3": 6 / 15, "A4": 0.70, "A5": 1.00, "A6": 5 / 6}


def prod(xs):
    out = 1.0
    for x in xs:
        out *= x
    return out


def dec2_point(f_over):
    tot = 0.0
    for r in orig:
        fl = [c for c in CATS if c != "A2" and r[c] == "1"]
        if fl:
            tot += 1.0 - prod(f_over[c] for c in fl)
    return tot, 100.0 * tot / len(orig)


t30, p30 = dec2_point(F_DISJ)
t25, p25 = dec2_point(dict(F_DISJ, A4=0.75))          # keep 5/20 -> overturn 15/20
rng = random.Random(SEED)
c4_keep = sorted(rng.betavariate(7, 15) for _ in range(DRAWS))
c4_lo, c4_hi = c4_keep[int(0.025 * DRAWS)], c4_keep[int(0.975 * DRAWS)]
t_lo, p_lo = dec2_point(dict(F_DISJ, A4=1 - c4_hi))   # low keep -> high overturn
t_hi, p_hi = dec2_point(dict(F_DISJ, A4=1 - c4_lo))

# --- M4: pooled C1 -----------------------------------------------------------
pool = sorted(rng.betavariate(1 + 5, 1 + 15) for _ in range(DRAWS))
pooled = {"k": 5, "n": 20, "mean": round(6 / 22, 4),
          "cri90": [round(pool[int(0.05 * DRAWS)], 4), round(pool[int(0.95 * DRAWS)], 4)],
          "cri95": [round(pool[int(0.025 * DRAWS)], 4), round(pool[int(0.975 * DRAWS)], 4)],
          "p_gt_0.60": round(sum(x > 0.60 for x in pool) / DRAWS, 5),
          "covers_0.60_90": pool[int(0.05 * DRAWS)] <= 0.60 <= pool[int(0.95 * DRAWS)]}

result = {
    "seed": SEED, "draws": DRAWS,
    "length_reweight": rw,
    "c4_sensitivity": {
        "baseline_keep_0.30": {"tools": round(t30, 2), "pct": round(p30, 3)},
        "census_keep_0.25": {"tools": round(t25, 2), "pct": round(p25, 3)},
        "c4_keep_cri95": [round(c4_lo, 4), round(c4_hi, 4)],
        "headline_pct_range": [round(p_lo, 3), round(p_hi, 3)],
    },
    "pooled_c1": pooled,
}
(OUT / "analysis" / "r5_followup.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
