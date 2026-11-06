# -*- coding: utf-8 -*-
"""Checker v2 — implements the codebook's *exclusion* clauses that v1 omitted
(reviewer point M1). Runs on the frozen 885-tool corpus (f4_all.csv) and on the
frozen 60-tool audit sample against the frozen human labels.

Differences from v1 (f4_sample.py annotate_rule):
  C1: drop descriptive/navigation phrasings the codebook excludes
      ("pick the", "use this (tool) first/before/as the primary",
       "then call" two-step navigation); add missed imperative forms
      ("MUST be called/used/followed", "ALWAYS use/start/read", "DO NOT assume",
       "use/follow this exact format").
  C3: URLs fire only with an agent-directed access verb nearby, not under a
      negation guard, and not when the host is a documentation citation host;
      bare inline/format-element HTML tags (i, b, select, input, ...) no longer
      fire alone — a tag must carry an attribute (style/src/href/on*) or belong
      to the rich/structural set; placeholder guards for $<i>$ path segments.
  C4: stance guards — negated solicitations ("needs no credential",
      "without a credential", "do not ask/pass"), auth-failure/missing-credential
      semantics, mechanism descriptions ("token is accepted", "your token's"),
      and server-side configuration mentions (env/config/default/server-side)
      are excluded per the codebook.
  C2/C5/C6: unchanged from v1.
"""
import csv, json, pathlib, re

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
ANALYSIS = OUT / "analysis"

# ── C1 ──────────────────────────────────────────────────────────────────────
# Table 1 defines C1 as a call-order / call-timing imperative; output-format
# mandates ("be concise", "use this exact format", "provide code examples")
# are out of scope in v2, as are descriptive phrasings ("use this to <verb>",
# "pick the", "use this as the primary") and deictic two-step navigation
# ("always call this before the first call", "then call X"), which the frozen
# coders map to C2.
CMD = re.compile(
    r"\byou must call\b|\byou should always\b|\byou must always\b|"
    r"\bnever (use|call|skip|forget)\b|"
    r"\balways (run|mention|install|provide|include|use|start|begin|read|follow|consult|check)\b|"
    r"\balways call\b(?!\s+this\s+before)|"
    r"\bmust (be\s+)?(called|used|run|invoked|executed|followed|read|queried)\b|"
    r"\bdo not assume\b|"
    r"\bcheck again\b|\bcheck for\b.{0,60}\bwhenever\b|"
    r"\bstart here\b|"
    r"\bcall this\b.{0,30}\bfirst\b|\bfirst call\b|\bcall this only after\b", re.I)

# ── C2 (unchanged v1) ───────────────────────────────────────────────────────
XTOOL_GENERIC = re.compile(
    r"\buse the matching \w+ tool\b|\bthe (separate|matching|other) [`'\"]?[\w-]+[`'\"]? tool\b|"
    r"\bcombine with\b|\bpair with\b|\bafter calling\b|"
    r"\bfound (with|via|using)\b|\bobtained (via|with|from)\b|\breturned by\b|"
    r"\bbefore (the )?['\"`][\w\s-]{2,40}['\"`] tool\b", re.I)
XTOOL_CUE = re.compile(r"\b(?:use|calls?|invoke|run|then|first|before|after|instead|pair|"
                       r"combine|requires?|loads?|fetch|pass)\b", re.I)
XTOOL_DIRECT = re.compile(r"\b(?:use|call|invoke|run|load|fetch)\s+[`'\"]?", re.I)

def c2_exact(text, siblings):
    low = text.lower()
    for t in siblings:
        tl = t.lower()
        if len(tl) < 4:
            continue
        for m in re.finditer(re.escape(tl), low):
            pre = low[max(0, m.start() - 80):m.start()]
            if tl.isalpha():
                if XTOOL_DIRECT.search(low[max(0, m.start() - 12):m.start()] + " "):
                    return True
            elif XTOOL_CUE.search(pre):
                return True
    return False

# ── C3 ──────────────────────────────────────────────────────────────────────
# a real URL needs a host character after the scheme: bare "http:// or https://"
# protocol-name mentions are format mentions and excluded by the codebook.
URL = re.compile(r"https?://[A-Za-z0-9]|www\.", re.I)
URL_NEG = re.compile(r"(?:do not|don'?t|never|instead of|rather than|without|"
                     r"no need|not required|avoid|must not)\b[\w\s]{0,40}$", re.I)
DOC_HOST = re.compile(
    r"https?://(?:docs\.|learn\.microsoft|developers\.|developer\.|github\.com|"
    r"en\.wikipedia|www\.npmjs|readme\.|support\.|kb\.|help\.|"
    r"[\w.-]*(?:microsoft|google|apple|apache|w3|ietf)[\w.-]*\.(?:com|org|net|dev|io)/)", re.I)
# v1 whitelist minus inline/format-element tags, which in this corpus appear
# only as element names in prose or as $<i>$ path placeholders (both excluded
# as format mentions). A tag carrying an attribute (style/src/href/on*) is
# payload-bearing and fires regardless of the tag name.
RICH_TAG = {"script", "style", "iframe", "object", "embed", "img", "svg",
            "pre", "code", "video", "audio", "source", "link", "meta", "base",
            "html", "head", "body", "frame", "frameset", "canvas", "table",
            "div", "span", "form", "a"}
TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>", re.I)
ATTR = re.compile(r"\b(?:style|src|href|class|id|on\w+)\s*=", re.I)
B64 = re.compile(r"\bdata:[\w/+.-]+;base64,|[A-Za-z0-9+/]{80,}={0,2}")
CODE = re.compile(r"```")

def c3_check(text):
    for m in URL.finditer(text):
        if DOC_HOST.search(text[m.start():m.start() + 80]):
            continue                      # documentation citation: format mention
        if URL_NEG.search(text[max(0, m.start() - 50):m.start()]):
            continue                      # negated / counter-example context
        return True
    for m in TAG.finditer(text):
        if m.start() > 0 and text[m.start() - 1] in ".$":
            continue                      # $steps.<i>.<path> placeholder
        tag, attrs = m.group(1).lower(), m.group(2)
        if tag in RICH_TAG or ATTR.search(attrs):
            return True
        # any other bare tag: format mention / prose element name, excluded
    if B64.search(text) or CODE.search(text):
        return True
    return False

# ── C4 ──────────────────────────────────────────────────────────────────────
CRED = re.compile(r"\bapi[- ]?key\b|\bsecret(s)?\b|\bpassword\b|\bcookie(s)?\b|\bset-cookie\b|"
                  r"\bPII\b|\bprivate information\b", re.I)
TOKEN = re.compile(r"\btoken(s)?\b", re.I)
TOKEN_CTX = re.compile(r"your|provide|pass|include|send|expose|leak|header|param|env|bearer|"
                       r"steal|exfiltrate", re.I)
CREDENTIAL = re.compile(r"\bcredential(s)?\b", re.I)
C4_GUARD = re.compile(
    r"\b(?:needs? no|no need for|without (?:a |any |the )?\w{0,12}\s*(?:credential|api[- ]?key|token|secret|password)|"
    r"do(?:es)? not (?:require|need|ask|pass|provide|include|send|expose)|"
    r"don'?t (?:require|need|ask|pass|provide|include|send)|"
    r"never (?:pass|ask|include|send|provide|expose|share)|"
    r"not (?:required|needed|necessary)|free of|no authentication|unauthenticated)\b", re.I)
C4_MECH = re.compile(r"\b(?:is accepted|auth(?:entication)?[- ]?fail|invalid|expired|missing|"
                     r"lacking|incorrect|denied)\b", re.I)
C4_SERVERSIDE = re.compile(r"\b(?:server[- ]side|already configured|preconfigured|configured "
                           r"(?:on|in|at) the server|environment variable|env var|"
                           r"config(?:uration)? file|\.env file)\b", re.I)
TOKEN_POSSESS = re.compile(r"\btoken'?s\b", re.I)

def c4_check(text):
    def guarded(lo, hi):
        ctx = text[max(0, lo - 60):min(len(text), hi + 60)]
        return bool(C4_GUARD.search(ctx) or C4_MECH.search(ctx))
    for m in CRED.finditer(text):
        if not guarded(m.start(), m.end()):
            return True
    for m in TOKEN.finditer(text):
        ctx = text[max(0, m.start() - 40):m.end() + 40]
        if TOKEN_POSSESS.search(m.group(0) + "'") and not TOKEN_CTX.search(ctx):
            continue
        if TOKEN_CTX.search(ctx) and not guarded(m.start(), m.end()):
            return True
    for m in CREDENTIAL.finditer(text):
        pre = text[max(0, m.start() - 6):m.start()].lower()
        post = text[m.end():m.end() + 12].lower().lstrip()
        if pre.endswith("auth/") or post.startswith(("failure", "error", "invalid", "expired")):
            continue
        if not guarded(m.start(), m.end()):
            return True
    return False

# ── C5 / C6 (unchanged v1) ──────────────────────────────────────────────────
PATH = re.compile(r"/etc/|~/\.ssh|\.ssh/|\b\.env\b|/root/|[A-Za-z]:\\|\.aws\b|\.gnupg\b|id_rsa", re.I)
HIDE = re.compile(
    r"\bwithout (telling|asking|notifying|informing)( the)? user\b|"
    r"\bdo not (mention|tell|inform|notify)\b|"
    r"\binternal knowledge\b|"
    r"\b(the )?user (can'?t|cannot|won'?t|doesn'?t|does not) (read|see|know)\b|"
    r"\bhide\b|\bconceal\b", re.I)
HIDE_CORE = re.compile(r"\bquietly\b|\bsilently\b", re.I)
NEG = re.compile(r"(?:never|not|don'?t|avoid)\b[\w\s]{0,15}$", re.I)
SILENT_SYS = re.compile(r"^\s*(?:rejects?|rejecting|fails?|failing|drops?|dropping|"
                        r"denies|denying|ignores?|ignoring)\b", re.I)

def c6_check(text):
    if HIDE.search(text):
        return True
    for m in HIDE_CORE.finditer(text):
        if NEG.search(text[max(0, m.start() - 20):m.start()]):
            continue
        if SILENT_SYS.match(text[m.end():m.end() + 15]):
            continue
        return True
    return False


def annotate_v2(text, siblings):
    text = text.replace("*", "")   # markdown emphasis (**ALWAYS**) is not syntax
    c1 = bool(CMD.search(text))
    c2 = bool(XTOOL_GENERIC.search(text)) or c2_exact(text, siblings)
    c3 = c3_check(text)
    c4 = c4_check(text)
    c5 = bool(PATH.search(text))
    c6 = c6_check(text)
    cats = [c1, c2, c3, c4, c5, c6]
    sev = 0
    if any(cats):
        sev = 1
        if c3 or c5 or c6:
            sev = 3
        elif c1 or c2:
            sev = 2
    return [int(x) for x in cats], sev


def kappa(a, b):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pa = sum(a) / n
    pb = sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return po, (po - pe) / (1 - pe) if pe < 1 else 1.0


def main():
    rows = list(csv.DictReader((OUT / "f4_all.csv").open(encoding="utf-8")))
    names = {}
    for r in rows:
        names.setdefault(r["server"], set()).add(r["tool"])

    # ── corpus relabel ────────────────────────────────────────────────
    with (OUT / "f4_all_v2.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(list(rows[0].keys()))
        stats = {f"C{i}": [0, 0] for i in range(1, 7)}   # [v1, v2]
        union = [0, 0]
        srv_any = {}
        for r in rows:
            sib = names[r["server"]] - {r["tool"]}
            cats, sev = annotate_v2(r["description"], sib)
            old = [int(r[f"A{i}"]) for i in range(1, 7)]
            for i in range(6):
                stats[f"C{i+1}"][0] += old[i]
                stats[f"C{i+1}"][1] += cats[i]
            union[0] += int(any(old))
            union[1] += int(any(cats))
            s = srv_any.setdefault(r["server"], [0, 0])
            s[0] |= int(any(old))
            s[1] |= int(any(cats))
            for i in range(1, 7):
                r[f"A{i}"] = cats[i - 1]
            r["A_sev"] = sev
            w.writerow(list(r.values()))
    out = {"corpus": {"tools": len(rows), "servers": len(srv_any),
                      "per_category": {k: {"v1": v[0], "v2": v[1]} for k, v in stats.items()},
                      "union": {"v1": union[0], "v2": union[1]},
                      "server_coverage": {"v1": sum(x[0] for x in srv_any.values()),
                                          "v2": sum(x[1] for x in srv_any.values())}}}

    # ── audit on the frozen 60-tool sample vs humanA / humanB ─────────
    sample = {r["id"]: r for r in csv.DictReader((ANALYSIS / "second_pass_sample.csv").open(encoding="utf-8"))}
    human = {}
    for fn, key in [("second_pass_labels_humanA.csv", "A"), ("second_pass_labels_humanB.csv", "B")]:
        for r in csv.DictReader((ANALYSIS / fn).open(encoding="utf-8")):
            human.setdefault(r["id"], {})[key] = r
    audit = {"n": 0}
    for c in range(1, 7):
        audit[f"C{c}"] = {}
    sev_pairs = {"engine": [], "A": [], "B": []}
    for sid, srow in sample.items():
        sib = names[srow["server"]] - {srow["tool"]}
        cats, sev = annotate_v2(srow["description"], sib)
        audit["n"] += 1
        ha, hb = human[sid]["A"], human[sid]["B"]
        sev_pairs["engine"].append(sev)
        sev_pairs["A"].append(int(ha["sev"]))
        sev_pairs["B"].append(int(hb["sev"]))
        for c in range(1, 7):
            e, va, vb = cats[c - 1], int(ha[f"C{c}"]), int(hb[f"C{c}"])
            d = audit[f"C{c}"]
            for lbl, v in [("humanA", va), ("humanB", vb)]:
                d.setdefault(lbl, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
                key = ("tp" if e and v else "fp" if e else "fn" if v else "tn")
                d[lbl][key] += 1
    for c in range(1, 7):
        for lbl in ("humanA", "humanB"):
            m = audit[f"C{c}"][lbl]
            tp, fp, fn, tn = m["tp"], m["fp"], m["fn"], m["tn"]
            m["precision"] = round(tp / (tp + fp), 3) if tp + fp else None
            m["recall"] = round(tp / (tp + fn), 3) if tp + fn else None
            po, k = kappa([1] * tp + [1] * fp + [0] * fn + [0] * tn,
                          [1] * tp + [0] * fp + [1] * fn + [0] * tn)
            m["kappa"] = round(k, 3)
    po, k = kappa(sev_pairs["engine"], sev_pairs["A"])
    audit["severity_engine_vs_humanA"] = {"po": round(po, 3), "kappa": round(k, 3)}
    out["audit60"] = audit
    (ANALYSIS / "f4_v2_eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["corpus"], ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in audit.items() if k != "n"}, ensure_ascii=False, indent=2)[:2400])


if __name__ == "__main__":
    main()
