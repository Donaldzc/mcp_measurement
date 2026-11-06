"""R6 review: estimand sensitivity variants of the de-C2 headline.

Reports the de-C2 tool-level headline (disjoint overturn shares, Beta
posterior MC as in the frozen analysis) under four estimands:

  baseline       : C1, C3, C4, C5, C6   (the reported headline)
  C1-excluded    : C3, C4, C5, C6       (drop the non-reproduced category)
  C4-excluded    : C1, C3, C5, C6       (drop the tuning-sample category)
  directive-only : C1, C3, C6           (the "directive text" estimand)

Per-tool probability p_t = 1 - prod_c f_c over the tool's flags within the
variant's category set; rate = sum_t p_t / 885. Overturn shares f_c are
drawn per iteration as 1 - Beta(k_conf, k_over) with the frozen posterior
parameters; the baseline must reproduce the frozen [1.25, 3.18] interval.

Writes: sandbox/output/analysis/r6_estimands.json
"""
import csv, json, pathlib, random

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
SEED = 20261104
DRAWS = 200_000
KEEP_POST = {"A1": (3, 14), "A3": (10, 7), "A6": (2, 6), "A4": (7, 15), "A5": (1, 2)}
VARIANTS = {
    "baseline": ["A1", "A3", "A4", "A5", "A6"],
    "c1_excluded": ["A3", "A4", "A5", "A6"],
    "c4_excluded": ["A1", "A3", "A5", "A6"],
    "directive_only": ["A1", "A3", "A6"],
}


def load(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


rows = [r for r in load(OUT / "f4_all_v2.csv") if r["description"]]
tools = {v: [r for r in rows if any(r[c] == "1" for c in cs)] for v, cs in VARIANTS.items()}
print({v: len(t) for v, t in tools.items()})


def prod(xs):
    out = 1.0
    for x in xs:
        out *= x
    return out


def pct(f):
    return {v: 100.0 * sum(1.0 - prod(f[c] for c in cs if r[c] == "1")
                           for r in tools[v]) / len(rows)
            for v, cs in VARIANTS.items()}


pt = {c: 1.0 - (KEEP_POST[c][0] / sum(KEEP_POST[c])) for c in KEEP_POST}
# point plug-in uses raw disjoint/census shares, not posterior means
pt = {"A1": 13 / 15, "A3": 6 / 15, "A4": 0.70, "A5": 1.00, "A6": 5 / 6}
point = pct(pt)

rng = random.Random(SEED + 7)
draws = {v: [] for v in VARIANTS}
for _ in range(DRAWS):
    f = {c: 1.0 - rng.betavariate(*KEEP_POST[c]) for c in KEEP_POST}
    for v, val in pct(f).items():
        draws[v].append(val)

out = {"seed": SEED + 7, "draws": DRAWS, "tools_per_variant": {v: len(t) for v, t in tools.items()},
       "point_plugin": {v: round(x, 3) for v, x in point.items()}}
for v in VARIANTS:
    s = sorted(draws[v])
    out[v] = {"median": round(s[DRAWS // 2], 3),
              "cri95": [round(s[int(0.025 * DRAWS)], 3), round(s[int(0.975 * DRAWS)], 3)]}
(OUT / "analysis" / "r6_estimands.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(json.dumps(out, indent=2))
