"""R7 review: positive control on MCPTox attack-authored tool descriptions.

Runs the frozen v2 checker (annotate_v2 from f4_sample_v2.py, imported
without its CLI main) over every attack-authored description in the MCPTox
benchmark (def_tool/*.py docstrings, zhiqiangwang4/MCPTox-Benchmark,
shallow clone under sandbox/mcptox). Sibling names are unknown per case, so
C2 uses the generic patterns only (conservative).

Reports recall: any-category union, directive union (C1/C3/C6), and
per-category hit counts.

Writes: sandbox/output/analysis/r7_mcptox.json
"""
import ast, importlib.util, json, pathlib

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"

spec = importlib.util.spec_from_file_location("f4_sample_v2", BASE / "f4_sample_v2.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

cases = []
for p in sorted((BASE / "sandbox" / "mcptox" / "def_tool").glob("*.py"),
                key=lambda x: int(x.stem) if x.stem.isdigit() else x.stem):
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if doc:
                cases.append({"file": p.name, "tool": node.name, "desc": doc})
            break

res = {"n_cases": len(cases), "per_category": {f"A{i}": 0 for i in range(1, 7)},
       "any": 0, "directive_c136": 0, "unflagged": []}
for c in cases:
    cats, sev = mod.annotate_v2(c["desc"], set())
    if any(cats):
        res["any"] += 1
    else:
        res["unflagged"].append(c["file"])
    if cats[0] or cats[2] or cats[5]:
        res["directive_c136"] += 1
    for i, v in enumerate(cats, 1):
        res["per_category"][f"A{i}"] += v

res["recall_any"] = round(res["any"] / res["n_cases"], 4)
res["recall_directive"] = round(res["directive_c136"] / res["n_cases"], 4)
(OUT / "analysis" / "r7_mcptox.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in res.items() if k != "unflagged"}, indent=2))
print("unflagged n =", len(res["unflagged"]))
