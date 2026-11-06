"""R7 review: sensitive-content outcome + frame-stratified/combined estimates.

New outcomes on top of r6_estimands.py (same frozen posteriors, seed +7 stream
not reused; this script uses seed +11):

  sensitive_only : C4, C5            (sensitive-content outcome, original frame)
  directive_only : C1, C3, C6        (primary estimand, re-reported here)

Frame-stratified directive-only estimates:
  original frame : 885 tools, 32 servers (f4_all_v2.csv); C1 share in-frame
                   disjoint, C3/C6 extension-frame disjoint.
  extension frame: 1706 tools, 53 servers (f4_ext_v2.csv); C3/C6 shares are
                   measured on this frame; C1 share is the in-frame disjoint
                   share applied under a frame-homogeneity assumption (no
                   extension C1 audit exists).
  combined       : 2591 tools, 85 servers; per-frame flagged sets pooled.

Writes: sandbox/output/analysis/r7_estimands.json
"""
import csv, json, pathlib, random

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
SEED = 20261104
DRAWS = 200_000
KEEP_POST = {"A1": (3, 14), "A3": (10, 7), "A6": (2, 6), "A4": (7, 15), "A5": (1, 2)}
PLUGIN = {"A1": 13 / 15, "A3": 6 / 15, "A4": 0.70, "A5": 1.00, "A6": 5 / 6}
VARIANTS = {
    "baseline": ["A1", "A3", "A4", "A5", "A6"],
    "directive_only": ["A1", "A3", "A6"],
    "sensitive_only": ["A4", "A5"],
}


def load(p):
    with open(p, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["description"]]


def prod(xs):
    out = 1.0
    for x in xs:
        out *= x
    return out


def rate(tools, n, f, cs):
    return 100.0 * sum(1.0 - prod(f[c] for c in cs if r[c] == "1") for r in tools) / n


frames = {
    "original": load(OUT / "f4_all_v2.csv"),
    "extension": load(OUT / "f4_ext_v2.csv"),
}
frames["combined"] = frames["original"] + frames["extension"]
servers = {k: len({r["server"] for r in v}) for k, v in frames.items()}
tools_flagged = {k: {v: [r for r in rows if any(r[c] == "1" for c in cs)]
                     for v, cs in VARIANTS.items()}
                 for k, rows in frames.items()}

rng = random.Random(SEED + 11)
res = {"seed": SEED + 11, "draws": DRAWS, "servers": servers,
       "n_tools": {k: len(v) for k, v in frames.items()},
       "flagged": {k: {v: len(t) for v, t in tv.items()}
                   for k, tv in tools_flagged.items()},
       "point_plugin": {}, "posterior": {}}

for k in frames:
    n = len(frames[k])
    res["point_plugin"][k] = {v: round(rate(t, n, PLUGIN, cs), 3)
                              for v, cs in VARIANTS.items()
                              for t in [tools_flagged[k][v]]}
    draws = {v: [] for v in VARIANTS}
    for _ in range(DRAWS):
        f = {c: 1.0 - rng.betavariate(*KEEP_POST[c]) for c in KEEP_POST}
        for v, cs in VARIANTS.items():
            draws[v].append(rate(tools_flagged[k][v], n, f, cs))
    res["posterior"][k] = {}
    for v in VARIANTS:
        s = sorted(draws[v])
        res["posterior"][k][v] = {"median": round(s[DRAWS // 2], 3),
                                  "cri95": [round(s[int(0.025 * DRAWS)], 3),
                                            round(s[int(0.975 * DRAWS)], 3)]}

(OUT / "analysis" / "r7_estimands.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
print(json.dumps(res, indent=2))
