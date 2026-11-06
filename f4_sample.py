# -*- coding: utf-8 -*-
"""F4 标注样本抽样 + 规则标注器（Annotator A）。

样本: 32 个动态成功 server 的 885 个 tool，按 server 比例分层随机抽 100 条（seed=42，保底每 server ≥1）。
输出: sandbox/output/f4_sample.csv (id, server, tool, description, A1..A6, Asev)

Annotator A v3（按第二轮 33 条分歧逐条归因修订 codebook 操作化定义）:
  (1) 直接命令 Agent：调用时机/顺序/强制性指示（"you must call"、"first/then call X"、
      "start here"、"check again"）+ 对 agent 回答/输出行为的指令
      （"be concise"、"always mention/install"、"prioritize"、"provide code examples"）；
      排除：描述性用途 "use this to <verb>"（T002/T035/T104）、API 参数约束。
  (2) 跨工具引用（编排性）：指示调用/排序/替代/组合另一工具——精确名需 60 字符内
      有编排动词（use/call/then/first/instead/pair/requires/load/fetch/pass/via）；
      泛指模式 "use the matching X tool"、"the separate X tool"、"found with X"、
      "before 'X' tool"；排除纯描述性提及（"Complements X"、"reserved with X"）。
  (3) 嵌入 URL/代码/编码：http(s) URL（含文档引用 URL，裁定 T066 计 1）、真实 HTML
      标签白名单（排除 <filename>/<what...>/<w:t> 等占位符与 XML 参数示例）、
      三反引号块、真实 base64 blob/data URI；排除对 "base64 format" 的提及（T025/T068）。
  (4) 凭证/密钥：api key、secret、password、cookie、PII 直接命中；token 需凭证语境
      （your/provide/include/header/param/env/bearer...），排除 "the token is accepted"
      类认证机制说明（T035）；credential 排除 auth/credential failure 错误语义（T071）。
  (5) 敏感路径：/etc、~/.ssh、.ssh/、.env、/root、C:\、.aws、.gnupg、id_rsa；
      排除容器挂载点 /home/sandbox、/opt/output（T007/T041）。
  (6) 隐藏/弱化：显式隐藏词（without telling/asking the user、do not mention/tell、
      internal knowledge、user can't read/see/know、hide/conceal）；quietly/silently
      需否定守卫（排除 "never call this silently" 类反隐藏指令，T074）；
      排除裸条件路由 "if the user provides/asks"（T043/T056）。
"""
import csv, json, pathlib, random, re

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "sandbox" / "output"
SAMPLE_N = 100
SEED = 42


def load_tools():
    summ = json.loads((OUT / "summary_v7.json").read_text(encoding="utf-8"))
    ok = {r["server_id"] for r in summ["results"] if r.get("ok")}
    items = []
    for sid in sorted(ok):
        p = OUT / f"{sid}.jsonl"
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            if o.get("tool_name"):
                items.append({
                    "server": sid,
                    "tool": o["tool_name"],
                    "description": (o.get("description") or "").replace("\n", " ").strip(),
                })
    return items


def stratified_sample(items, n, seed):
    random.seed(seed)
    by_server = {}
    for it in items:
        by_server.setdefault(it["server"], []).append(it)
    for v in by_server.values():
        random.shuffle(v)
    total = len(items)
    keys = sorted(by_server)
    alloc = {s: max(1, round(len(v) * n / total)) for s, v in by_server.items()}
    # 收敛到恰好 n
    while sum(alloc[k] for k in keys) > n:
        k = max(keys, key=lambda s: alloc[s] / len(by_server[s]))
        if alloc[k] > 1:
            alloc[k] -= 1
        else:
            break
    while sum(alloc[k] for k in keys) < n:
        k = max(keys, key=lambda s: len(by_server[s]) / alloc[s])
        if alloc[k] < len(by_server[k]):
            alloc[k] += 1
        else:
            break
    out = []
    for s in keys:
        out.extend(by_server[s][:alloc[s]])
    random.shuffle(out)
    for i, it in enumerate(out):
        it["id"] = f"T{i+1:03d}"
    return out


URL = re.compile(r"https?://|www\.", re.I)
HTML_TAG = re.compile(
    r"</?(?:a|p|div|span|img|br|hr|pre|code|ul|ol|li|table|thead|tbody|tr|td|th|"
    r"h[1-6]|script|style|iframe|form|input|button|select|textarea|html|head|body|"
    r"title|meta|link|section|article|aside|nav|header|footer|em|strong|b|i|u|svg|path)\b[^>]*>",
    re.I)
B64 = re.compile(r"\bdata:[\w/+.-]+;base64,|[A-Za-z0-9+/]{80,}={0,2}")  # 60→80：排除配置键列举 FP
CODE = re.compile(r"```")
CMD = re.compile(
    r"\byou must call\b|\byou should always\b|\byou must always\b|"
    r"\bnever (use|call|skip|forget)\b|"
    r"\balways (run|call|mention|install|provide|include)\b|"
    r"\bcheck again\b|\bcheck for\b.{0,60}\bwhenever\b|"
    r"\bpick the\b|\buse this (tool )?first\b|\buse this before\b|\bstart here\b|"
    r"\buse this\b[^.]{0,100}?\bbefore (making|calling|invoking|using)\b|"
    r"\buse this as (the )?(primary|core|main)\b|"
    r"\bcall this\b.{0,30}\bfirst\b|\bfirst call\b|\bthen call\b|\bcall this only after\b|"
    r"\bbe concise\b|\bprovide code examples?\b|\bprioritize\b", re.I)
XTOOL_GENERIC = re.compile(
    r"\buse the matching \w+ tool\b|\bthe (separate|matching|other) [`'\"]?[\w-]+[`'\"]? tool\b|"
    r"\bcombine with\b|\bpair with\b|\bafter calling\b|"
    r"\bfound (with|via|using)\b|\bobtained (via|with|from)\b|\breturned by\b|"
    r"\bbefore (the )?['\"`][\w\s-]{2,40}['\"`] tool\b", re.I)
XTOOL_CUE = re.compile(r"\b(?:use|calls?|invoke|run|then|first|before|after|instead|pair|"
                       r"combine|requires?|loads?|fetch|pass)\b", re.I)  # via 误命中端点路径，剔除
XTOOL_DIRECT = re.compile(r"\b(?:use|call|invoke|run|load|fetch)\s+[`'\"]?",
                          re.I)  # 纯字母工具名需动词直接紧邻
CRED = re.compile(r"\bapi[- ]?key\b|\bsecret(s)?\b|\bpassword\b|\bcookie(s)?\b|\bset-cookie\b|"
                  r"\bPII\b|\bprivate information\b", re.I)
TOKEN = re.compile(r"\btoken(s)?\b", re.I)
TOKEN_CTX = re.compile(r"your|provide|pass|include|send|expose|leak|header|param|env|bearer|"
                       r"steal|exfiltrate", re.I)
CREDENTIAL = re.compile(r"\bcredential(s)?\b", re.I)
PATH = re.compile(r"/etc/|~/\.ssh|\.ssh/|\b\.env\b|/root/|[A-Za-z]:\\|\.aws\b|\.gnupg\b|id_rsa", re.I)
HIDE = re.compile(
    r"\bwithout (telling|asking|notifying|informing)( the)? user\b|"
    r"\bdo not (mention|tell|inform|notify)\b|"
    r"\binternal knowledge\b|"
    r"\b(the )?user (can'?t|cannot|won'?t|doesn'?t|does not) (read|see|know)\b|"
    r"\bhide\b|\bconceal\b", re.I)
HIDE_CORE = re.compile(r"\bquietly\b|\bsilently\b", re.I)
NEG = re.compile(r"(?:never|not|don'?t|avoid)\b[\w\s]{0,15}$", re.I)
# 系统行为告警（"a stricter gate silently rejects the download"）非隐藏指令，后随抑制
SILENT_SYS = re.compile(r"^\s*(?:rejects?|rejecting|fails?|failing|drops?|dropping|"
                        r"denies|denying|ignores?|ignoring)\b", re.I)


def c2_exact(text, siblings):
    """同 server 精确工具名引用，双层判定：
    - 含 -/_ 的非字母名：前方 60 字符内需编排动词（排除 "Complements X"/"reserved with X"）；
    - 纯字母名（whoami/login/orient 等）：仅动词直接紧邻（use `whoami`），
      防 "then `orient task=...`"、"via --login" 类子命令/参数示例误报。"""
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


def c4_check(text):
    if CRED.search(text):
        return True
    for m in TOKEN.finditer(text):  # token 需凭证语境，排除认证机制说明
        if TOKEN_CTX.search(text[max(0, m.start() - 40):m.end() + 40]):
            return True
    for m in CREDENTIAL.finditer(text):  # 排除 auth/credential failure 错误语义
        pre = text[max(0, m.start() - 6):m.start()].lower()
        post = text[m.end():m.end() + 12].lower().lstrip()
        if pre.endswith("auth/") or post.startswith(("failure", "error", "invalid", "expired")):
            continue
        return True
    return False


def c6_check(text):
    if HIDE.search(text):
        return True
    for m in HIDE_CORE.finditer(text):  # quietly/silently 否定守卫 + 系统行为告警抑制
        if NEG.search(text[max(0, m.start() - 20):m.start()]):
            continue
        if SILENT_SYS.match(text[m.end():m.end() + 15]):
            continue
        return True
    return False


def annotate_rule(text, siblings):
    c1 = bool(CMD.search(text))
    c2 = bool(XTOOL_GENERIC.search(text)) or c2_exact(text, siblings)
    c3 = bool(URL.search(text) or HTML_TAG.search(text) or B64.search(text) or CODE.search(text))
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


def main():
    import sys
    args = sys.argv[1:]
    confirm = "confirm" in args
    all_mode = "all" in args
    items = load_tools()
    print(f"总 tools: {len(items)}")
    # 每个 server 的工具名集合（跨工具引用检测用）
    names_by_server = {}
    for it in items:
        names_by_server.setdefault(it["server"], set()).add(it["tool"])
    if all_mode:
        # 全量标注（冻结 codebook v3，无抽样）
        sample = items
        out = OUT / "f4_all.csv"
    elif confirm:
        # 确认样本：排除试点 (server, tool)，seed=43，独立评估冻结 codebook v3 的 kappa
        pilot = set()
        p = OUT / "f4_sample.csv"
        if p.exists():
            for r in csv.DictReader(p.open(encoding="utf-8")):
                pilot.add((r["server"], r["tool"]))
        pool = [it for it in items if (it["server"], it["tool"]) not in pilot]
        print(f"确认样本池（排除试点 {len(pilot)} 条）: {len(pool)}")
        sample = stratified_sample(pool, SAMPLE_N, 43)
        out = OUT / "f4_confirm.csv"
    else:
        sample = stratified_sample(items, SAMPLE_N, SEED)
        out = OUT / "f4_sample.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "server", "tool", "description",
                    "A1", "A2", "A3", "A4", "A5", "A6", "A_sev",
                    "B1", "B2", "B3", "B4", "B5", "B6", "B_sev"])
        for it in sample:
            sib = names_by_server[it["server"]] - {it["tool"]}
            cats, sev = annotate_rule(it["description"], sib)
            iid = it.get("id", f"{it['server']}|{it['tool']}")
            w.writerow([iid, it["server"], it["tool"], it["description"],
                        *cats, sev, "", "", "", "", "", "", ""])
    print(f"样本写入 {out}")
    from collections import Counter
    cc = Counter()
    for it in sample:
        sib = names_by_server[it["server"]] - {it["tool"]}
        cats, sev = annotate_rule(it["description"], sib)
        for i, c in enumerate(cats):
            if c:
                cc[f"C{i+1}"] += 1
    print("Annotator A 阳性率:", dict(cc))
    return 0


if __name__ == "__main__":
    main()
