# -*- coding: utf-8 -*-
"""漂移 × E7 违反数的偏相关去混杂分析（控制版本数）。

混杂逻辑：漂移事件数 = f(版本对数)——版本越多暴露机会越多，与 E7 违反的原始相关
可能被"版本总数"驱动。方法：
  (a) 事件数口径：Pearson/Spearman 原始 + 控制 log(1+n_releases) 的 Pearson 偏相关
  (b) 事件率口径：事件/版本对（天然按暴露归一化）vs E7 的简单相关
  (c) 稳健性：版本数三分位分层，看层内方向一致性
推断：n=32，t 检验 + Fisher z 95% CI（偏相关的 df = n-2-k）

输出：回写 e6_rq4_report.json 的 partial_correlation 字段
"""
import json, pathlib, math, statistics
from collections import Counter

BASE = pathlib.Path(__file__).resolve()
OUT = BASE.parent / "sandbox" / "output"


def pearson(x, y):
    mx, my = statistics.mean(x), statistics.mean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    return num / den if den else 0.0


def spearman(x, y):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v); i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    return pearson(rank(x), rank(y))


def partial_pearson(x, y, z):
    """控制单变量 z 的 Pearson 偏相关。"""
    rxy, rxz, ryz = pearson(x, y), pearson(x, z), pearson(y, z)
    den = math.sqrt((1 - rxz ** 2) * (1 - ryz ** 2))
    return (rxy - rxz * ryz) / den if den else 0.0


def fisher_ci(r, n, k=0):
    """Fisher z 95% CI 与双侧 p（df = n - 2 - k）。"""
    df = n - 2 - k
    if df <= 0 or abs(r) >= 1:
        return {"ci95": None, "p": None}
    z = 0.5 * math.log((1 + r) / (1 - r))
    se = 1 / math.sqrt(n - 3)
    lo, hi = z - 1.96 * se, z + 1.96 * se
    t = r * math.sqrt(df / (1 - r * r))
    # t 分布 p 值（正态近似即可，n>=30）
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return {"ci95": [round((math.tanh(lo)), 3), round(math.tanh(hi), 3)], "p": round(p, 4), "t": round(t, 2)}


def main():
    e6 = json.loads((OUT / "e6_rq4_report.json").read_text(encoding="utf-8"))
    e7 = json.loads((OUT / "e7_report.json").read_text(encoding="utf-8"))
    n_rules = len(e7["rules"])

    viol, drift_total, drift_rate, n_rel = {}, {}, {}, {}
    for s, sv in e6["E6_drift"]["per_server"].items():
        drift_total[s] = sum(sv["events"].values())
        drift_rate[s] = drift_total[s] / sv["n_pairs"] if sv["n_pairs"] else 0.0
        n_rel[s] = sv["n_releases"]
    for row in (OUT / "e7_server_rule_matrix.csv").read_text(encoding="utf-8").splitlines()[1:]:
        parts = row.split(",")
        viol[parts[0]] = sum(int(x) for x in parts[1:n_rules + 1])

    sids = sorted(set(drift_total) & set(viol))
    X = [drift_total[s] for s in sids]          # 漂移事件数
    Y = [viol[s] for s in sids]                 # E7 违反数
    Z = [math.log1p(n_rel[s]) for s in sids]    # 混杂：log(1+版本数)
    R = [drift_rate[s] for s in sids]           # 事件率口径
    N = len(sids)

    res = {"n_servers": N, "method": "Pearson 偏相关控制 log(1+releases)；df=N-2-1",
           "raw": {
               "pearson": round(pearson(X, Y), 3),
               "pearson_inference": fisher_ci(pearson(X, Y), N),
               "spearman": round(spearman(X, Y), 3),
           },
           "partial_control_releases": {
               "pearson_partial": round(partial_pearson(X, Y, Z), 3),
               "inference": fisher_ci(partial_pearson(X, Y, Z), N, k=1),
               "confounder_corr": {
                   "releases_vs_drift": round(pearson(Z, X), 3),
                   "releases_vs_e7": round(pearson(Z, Y), 3),
               },
           },
           "event_rate_口径": {
               "note": "事件/版本对（按暴露机会归一化）vs E7 的简单相关",
               "pearson": round(pearson(R, Y), 3),
               "inference": fisher_ci(pearson(R, Y), N),
           },
          }

    # 稳健性：版本数三分位分层方向一致性
    order = sorted(range(N), key=lambda i: n_rel[sids[i]])
    thirds = [order[:N // 3], order[N // 3: 2 * N // 3], order[2 * N // 3:]]
    layers = []
    for g in thirds:
        gx = [X[i] for i in g]; gy = [Y[i] for i in g]
        layers.append({"n": len(g), "spearman_in_layer": round(spearman(gx, gy), 3),
                       "direction": "+" if pearson(gx, gy) > 0 else "-"})
    res["stratified_robustness"] = {
        "note": "按版本数三分位分层，检查层内方向一致性（Simpson 悖论探查）",
        "layers": layers,
        "direction_consistent": len(set(l["direction"] for l in layers)) == 1,
    }

    # 类别级偏相关：D5 维护者漂移与 D4 安装脚本漂移单列
    cat_cols = {"D5_maintainer": [], "D4_scripts": []}
    for s in sids:
        ev = e6["E6_drift"]["per_server"][s]["events"]
        cat_cols["D5_maintainer"].append(ev.get("D5_maintainer", 0))
        cat_cols["D4_scripts"].append(ev.get("D4_scripts", 0))
    res["per_category_partial"] = {}
    for k, cx in cat_cols.items():
        pr = partial_pearson(cx, Y, Z)
        res["per_category_partial"][k] = {
            "raw_pearson": round(pearson(cx, Y), 3),
            "partial_control_releases": round(pr, 3),
            "inference": fisher_ci(pr, N, k=1),
        }

    # 回写
    e6["partial_correlation"] = res
    (OUT / "e6_rq4_report.json").write_text(json.dumps(e6, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 60)
    print("漂移 × E7 违反数：偏相关去混杂（控制版本数）")
    print("=" * 60)
    r = res["raw"]
    p = res["partial_control_releases"]
    e = res["event_rate_口径"]
    print(f"\n原始关联 (n={N}):")
    print(f"  Pearson={r['pearson']}  95%CI {r['pearson_inference']['ci95']}  p={r['pearson_inference']['p']}")
    print(f"  Spearman={r['spearman']}")
    print(f"\n控制 log(1+版本数) 后:")
    print(f"  偏相关={p['pearson_partial']}  95%CI {p['inference']['ci95']}  p={p['inference']['p']}")
    print(f"  混杂确认: 版本数×漂移 r={p['confounder_corr']['releases_vs_drift']}, 版本数×E7 r={p['confounder_corr']['releases_vs_e7']}")
    print(f"\n事件率口径 (事件/版本对):")
    print(f"  Pearson={e['pearson']}  95%CI {e['inference']['ci95']}  p={e['inference']['p']}")
    sr = res["stratified_robustness"]
    print(f"\n三分位分层: {[l['spearman_in_layer'] for l in sr['layers']]} 方向一致={sr['direction_consistent']}")
    print(f"类别级偏相关: " + ", ".join(
        f"{k} raw={v['raw_pearson']} partial={v['partial_control_releases']}"
        for k, v in res["per_category_partial"].items()))
    print(f"\n→ 已回写 e6_rq4_report.json: partial_correlation")
    return 0


if __name__ == "__main__":
    main()
