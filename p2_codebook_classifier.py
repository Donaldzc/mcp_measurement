# -*- coding: utf-8 -*-
"""注入面安全指令分类器（v2 六类 codebook）。

实现 v2 设计 codebook v2 六类分类：
  C1 直接命令 Agent   direct agent imperative
  C2 跨工具引用       cross-tool reference
  C3 嵌入危险内容     embedded dangerous content
  C4 凭证/密钥索取    credential request
  C5 敏感路径读取     sensitive path access
  C6 隐藏/弱化指令    hidden or weakened instruction

输出: 对 tool description 返回 list of (class_label, severity 1-3, matched_pattern)
边界: 这是规则集基线，用于可行性试点 P2b 建立可复现起点。
      最终全量阶段需 + LLM 辅助（无工具、结构化 JSON、只在分歧处）。
"""
import re


# C1 直接命令 Agent
C1_PATTERNS = [
    (re.compile(r"\b(you|the\s+agent|agent)\s+(must|should|need\s+to|have\s+to|is\s+to)\b", re.I), 3, "agent imperative verb"),
    (re.compile(r"\btell\s+(the\s+)?agent\s+to\b", re.I), 3, "tell agent to"),
    (re.compile(r"\b(make|have)\s+(the\s+)?agent\s+\w+", re.I), 3, "make agent do"),
    (re.compile(r"\b(action|do)\s+this\b"), 2, "action this"),
]

# C2 跨工具引用
C2_PATTERNS = [
    (re.compile(r"(use|combine|together|alongside|along\s+with)\s+tool\s+[a-z_]+", re.I), 2, "verb + tool name"),
    (re.compile(r"(works?\s+with|integrates?\s+with|compatible\s+with)\s+[a-z_]+\s+tool", re.I), 2, "compatible with tool"),
    (re.compile(r"works?\s+(best|together)\s+(with|alongside)", re.I), 2, "works with"),
]

# C3 嵌入危险内容
C3_EMBED_MARKERS = ["```", "```", "<script", "<iframe", "<img ", "<svg ", "http://", "https://", "base64"]
C3_SEV = {
    "script iframe svg": 3,
    "http": 2,
    "base64": 3,
    "```": 2,
}

# C4 凭证索取
C4_PATTERNS = [
    (re.compile(r"(provide|enter|input|share|paste)\s+(your|the)\s+(password|api\s*key|token|secret|private\s*key|credential)", re.I), 3, "credential request verb"),
    (re.compile(r"(\bpassword\b|\bapi[_-]?key\b|\bsecret[_-]?key\b|\btoken\b)\s*(?:is|required|needed)", re.I), 2, "credential noun + required"),
]

# C5 敏感路径
C5_PATTERNS = [
    (re.compile(r"/etc/(passwd|shadow|sudoers|hosts|crontab)"), 3, "etc system paths"),
    (re.compile(r"(~|\$HOME)/\.ssh/"), 3, "ssh dir"),
    (re.compile(r"(~|\$HOME)/\.(aws|kube|docker|gcloud|npm)/"), 3, "cloud credential dirs"),
    (re.compile(r"C:\\Users\\[^\s]+\\(AppData|NTUSER|Desktop|Documents)"), 2, "Windows sensitive dirs"),
    (re.compile(r"([a-z_]+://)?s3://[a-z0-9\-_.]+/?"), 2, "S3 bucket reference"),
    (re.compile(r"(\bfile://|\.env\b|\.pem\b|\.key\b)"), 2, "file uri / key file ext"),
]

# C6 隐藏/弱化指令（启发式：指令出现在条件/注释/与工具名语义不符）
C6_INDICATORS = [
    (re.compile(r"\b(if|when|unless|only\s+if)\s+", re.I), 1, "conditional"),
    (re.compile(r"(may|might|could|perhaps|suggest|recommend|optionally)", re.I), 1, "hedging"),
]
C6_SEMANTIC_MISMATCH_HINTS = re.compile(
    r"(password|key|secret|/etc|\.ssh|curl|bash|eval|exec)\b", re.I)


def classify(tool_name, description):
    """返回 list of (class_label, severity, matched_pattern, indicator_text)。"""
    hits = []
    text = description or ""

    for pat, sev, label in C1_PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(("C1", sev, label, m.group()))

    for pat, sev, label in C2_PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(("C2", sev, label, m.group()))

    for marker in C3_EMBED_MARKERS:
        if marker in text.lower():
            sev = C3_SEV.get("script iframe svg" if marker.startswith("<script") or marker.startswith("<iframe") or marker.startswith("<svg")
                             else "base64" if marker == "base64"
                             else "http" if marker.startswith("http")
                             else "```", 2)
            hits.append(("C3", sev, "embedded content marker", marker))

    for pat, sev, label in C4_PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(("C4", sev, label, m.group()))

    for pat, sev, label in C5_PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(("C5", sev, label, m.group()))

    c6_sev = 0
    for pat, sev, label in C6_INDICATORS:
        if pat.search(text):
            c6_sev = max(c6_sev, sev)
    if C6_SEMANTIC_MISMATCH_HINTS.search(text):
        c6_sev = max(c6_sev, 2)
    if c6_sev > 0 and not any(h[0] in ("C1", "C4", "C5") for h in hits):
        hits.append(("C6", c6_sev, "hidden/weakened", ""))

    return hits


if __name__ == "__main__":
    import json, sys, pathlib
    p = pathlib.Path(sys.argv[1])
    out = []
    for line in open(p, encoding="utf-8"):
        t = json.loads(line)
        hits = classify(t["tool_name"], t.get("description", ""))
        if hits:
            out.append({**t, "security_hits": hits})
    print(json.dumps({"total": len(json.loads(open(p,encoding="utf-8").read())), "with_hits": len(out)}, ensure_ascii=False, indent=2))
