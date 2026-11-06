"""Frozen analysis script for the blind audit (preregistration protocol S6).

Input : blind_audit/sheets/coderA_blind_sheet.csv and coderB_blind_sheet.csv
        with C1..C6 filled as 1 (confirmed) / 0 (overturned) / NA
        (inapplicable), plus an adjudicated merge produced under the main
        study's adjudication rules: blind_audit/adjudicated.csv
        (sheet_id, C1..C6 final labels).
Output: per-category disjoint precision with Beta(1,1) 95% CrI, optimism gap
        vs the tuned-sample posterior, recomputed de-C2 headline (Eq. 4),
        per-category Cohen kappa + PABAK, and the decision-rule outcomes.
Frozen at preregistration; run once after adjudication.
"""
import csv, json, random, pathlib, sys

BASE = pathlib.Path(__file__).resolve().parent
SHEETS = BASE / "blind_audit" / "sheets"
KEY = BASE / "blind_audit" / "SEALED_mapping_key.csv"
ADJ = BASE / "blind_audit" / "adjudicated.csv"

SEED_MC = 20261103
CATS = ["C1", "C3", "C6"]          # actionable categories (H1-H3)
RELIAB = ["C5", "C6"]              # enriched reliability module (H4, M4)
TUNED = {"C1": (3, 5), "C3": (3, 6), "C4": (5, 20), "C5": (0, 1), "C6": (3, 7)}
RAW = {"C1": 42, "C3": 6, "C4": 20, "C5": 1, "C6": 7}   # v2 corpus counts
N_TOOLS = 885

def load(p):
    with open(p, newline="", encoding="utf-8") as f:
        return {r["sheet_id"]: r for r in csv.DictReader(f)}

def beta_ci(k, n, seed):
    rng = random.Random(seed)
    a, b = 1 + k, 1 + (n - k)
    xs = sorted(rng.betavariate(a, b) for _ in range(200_000))
    return xs[0].__round__(4), xs[5_000], xs[195_000], xs[-1]

def main():
    key = load(KEY)
    A, B = load(SHEETS / "coderA_blind_sheet.csv"), load(SHEETS / "coderB_blind_sheet.csv")
    adj = load(ADJ)

    # --- validation texts (protocol S5.4) ----------------------------------
    val = {s: key[s] for s in key if key[s]["stratum"].startswith("validation")}
    v_ok = all((c[s].get("C6") == "1") if key[s]["id"] == "V001"
               else all(c[s].get(c_) in ("0", "NA") for c_ in ("C1","C2","C3","C4","C5","C6"))
               for s in val for c in (A, B))
    print(f"validation texts passed: {v_ok}")
    if not v_ok:
        sys.exit("attention check failed; re-brief coders before analysis")

    # --- per-category kappa + PABAK on shared items -------------------------
    def kappa(cat):
        pairs = [(A[s].get(cat), B[s].get(cat)) for s in adj
                 if A[s].get(cat) in ("0", "1") and B[s].get(cat) in ("0", "1")]
        n = len(pairs)
        if n == 0: return None
        po = sum(x == y for x, y in pairs) / n
        p1a = sum(x == "1" for x, _ in pairs) / n
        p1b = sum(y == "1" for _, y in pairs) / n
        pe = p1a * p1b + (1 - p1a) * (1 - p1b)
        k = None if pe == 1 else (po - pe) / (1 - pe)
        return {"n": n, "po": round(po, 4), "kappa": None if k is None else round(k, 4),
                "pabak": round(2 * po - 1, 4), "prev_pos": round((p1a + p1b) / 2, 4)}
    reliab = {c: kappa(c) for c in RELIAB}

    # --- disjoint precision per actionable category -------------------------
    prec, gap_draws = {}, {}
    for c in CATS:
        # stratum labels: v2 key names extension strata "<C>_ext" (mechanical
        # mapping fixed post-freeze; analysis logic unchanged)
        pos = [s for s in adj if key[s]["stratum"].replace("_ext", "") == c
               and key[s]["checker_cats"].find(c) >= 0]
        conf = [s for s in pos if adj[s].get(c) == "1"]
        prec[c] = {"n": len(pos), "confirmed": len(conf)}
        _, prec[c]["ci_lo"], prec[c]["ci_hi"], _ = beta_ci(len(conf), len(pos), SEED_MC)
        kt, nt = TUNED[c]
        rng = random.Random(SEED_MC + 1)
        d = [rng.betavariate(1+kt, 1+nt-kt) - rng.betavariate(1+len(conf), 1+len(pos)-len(conf))
             for _ in range(200_000)]
        d.sort()
        gap_draws[c] = d
        prec[c]["delta_ci"] = [d[5_000].__round__(4), d[195_000].__round__(4)]
        prec[c]["p_delta_gt_0.25"] = round(sum(x > 0.25 for x in d) / len(d), 4)

    # --- recomputed de-C2 headline (Eq. 4), C1/C3/C6 from disjoint ----------
    rng = random.Random(SEED_MC + 2)
    head = []
    for _ in range(200_000):
        e = 0.0
        for c in ("C1", "C3", "C6"):
            k, n = prec[c]["confirmed"], prec[c]["n"]
            keep = rng.betavariate(1 + k, 1 + n - k)
            e += RAW[c] * keep
        for c in ("C4", "C5"):
            k, n = TUNED[c]
            e += RAW[c] * rng.betavariate(1 + k, 1 + n - k)
        head.append(100.0 * e / N_TOOLS)
    head.sort()
    h_lo, h_hi = round(head[5_000], 2), round(head[195_000], 2)
    h3_holds = h_lo <= 4.5 and h_hi >= 4.0

    out = {"precision": prec, "reliability": reliab,
           "headline_recomputed": {"ci": [h_lo, h_hi],
                                   "median": round(head[100_000], 2),
                                   "h3_overlap_4.0_4.5": h3_holds},
           "decision_rules": {
               "rule1_headline_unchanged": h3_holds,
               "rule2_revise_headline": not h3_holds,
               "rule3_downgrade": [c for c in CATS if prec[c]["p_delta_gt_0.25"] > 0.5],
               "rule4_kappa_below_0.4": [c for c in RELIAB
                   if reliab[c] and reliab[c]["kappa"] is not None and reliab[c]["kappa"] < 0.4]}}
    (BASE / "blind_audit" / "analysis_result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
