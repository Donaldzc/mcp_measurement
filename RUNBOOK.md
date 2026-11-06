# 可行性试点手动执行清单（P0-P2 全部就绪后）

## 前置条件

1. GitHub Token（推荐 fine-grained，勾 public_repo 只读；经典 token 需 public_repo scope）
   生成位置: https://github.com/settings/tokens
   
2. Docker Desktop 已启动（全量动态抓取未信任 server 必须容器化）

3. gold_set.csv 已审校
   文件: mcp_measurement/gold_set/gold_set.csv
   已预填 19 条（15 included / 4 excluded），你只需逐条确认 included 字段
   标记为 0 的条目有明确 exclusion_reason：headroom/deployment、awesome 清单、private-gpt 完整应用

---

## Step 1: 设置 GitHub Token（PowerShell）

```powershell
# 单次会话生效
$env:GITHUB_TOKEN = "ghp_你的token"

# 验证限额（预期 core_remaining: 4990+）
try {
    $r = Invoke-RestMethod -Uri "https://api.github.com/rate_limit" `
        -Headers @{"Authorization"="Bearer $env:GITHUB_TOKEN"} -TimeoutSec 15
    "core_remaining: $($r.resources.core.remaining)"
} catch { "token 无效或未生效" }
```

---

## Step 2: 跑 50 repo 可行性试点（静态抓取）

```powershell
cd mcp_measurement/d2_pilot
python crawl_static.py --repos 50
```

预期输出（token 生效后）：
```
{
  "repos_ok": 50,
  "repos_err": 0,
  "files_fetched": 80+,
  "tools": 5+,
  "manifest": "corpus/manifest.json"
}
```
若 repos_err > 0 或 rate_limited: true，检查 GITHUB_TOKEN 是否正确。

---

## Step 3: 跑可行性四指标

```powershell
cd mcp_measurement
python p2_feasibility_runner.py
```

通过门槛：
- F1 静态抓取成功率 ≥ 0.85（四会测量论文最低要求）
- F2 工具描述缺失率 < 0.15
- F3 / F4 待 AST 抽取 / 双人标注（留占位即可）

若 F1 < 0.85，检查 crawl_static.py 输出的 manifest.json，看哪些 source 失败率高。

---

## Step 3.5: 构建 npm 抽样框（四 registry 枚举的替代链路）

四聚合 registry 的公开枚举在当前时点实测不可用，脚本已改为以 npm registry 搜索 API
作为可复现分布层抽样框（见 `registry_npm_frame.py` 文件头注释）：

| 来源 | 端点 | 实测结果（2026-10-22） |
|---|---|---|
| PulseMCP | api.pulsemcp.com/v0beta/servers | HTTP 410 Gone |
| Glama | glama.ai/api/mcp/v1/servers | HTTP 401（需 API key） |
| Smithery | CLI `mcp search --json` | 可用，但详情页 GitHub 链接 100% 404 |
| mcp.so | /servers HTML | 可拉，SPA 无结构化字段 |
| npm registry | /-/v1/search + /{pkg}/{ver} | 可用，含版本/发布时间/下载量/bin 入口 |

```powershell
cd mcp_measurement
python registry_npm_frame.py --per-query 250 --top 60
```

输出 `registry/npm_frame.jsonl`（全量候选 + 排除理由）与 `registry/known_servers.json`
（真实包名 + pin 版本 + bin 入口）。可部署性硬判据 = 包内存在 bin 可执行入口，
不依赖描述关键词。

---

## Step 4: 容器化动态 tools/list（禁网沙箱）

```powershell
cd mcp_measurement
python sandbox_handshake_runner.py --known 60
```

两阶段：主机有网 `npm install --prefix` 固定版本 → 容器 `--network=none` 直接 node
启动 bin 入口做 initialize + tools/list。结果写 `sandbox/output/{server_id}.jsonl`，
汇总 `sandbox/output/summary.json`（含每例 ok/n_tools/reason 与 install_failures）。

沙箱七项: `--network=none` `--read-only` `--tmpfs /tmp` `--cap-drop=ALL`
`--security-opt=no-new-privileges` `--cpus=1 --memory=256m --pids-limit=50` 超时 120s。

---

## Step 5: gold set 审校后 → 跑实体解析验证

```powershell
cd mcp_measurement
python p1_entity_resolver.py --top-n 50 --level 2 --gold gold_set/gold_set.csv
```

预期输出含 pr_vs_gold（precision/recall/F1），若 recall < 0.8 需检查 gold_set.csv 中 included 字段是否正确。

---

## 常见失败排查

| 症状 | 原因 | 处理 |
|---|---|---|
| crawl_static.py repos_err 全 http403 | Token 未生效/额度耗尽 | 重新设置 GITHUB_TOKEN；或等 reset（manifest.json 里有 reset_epoch） |
| repos_ok 但 tools_found = 0 | 该 repo 不是 MCP server 或正则覆盖不到 | 换 top-star 官方 server 测试 |
| handshake_tools_list.py 报 safety refusing | 在主机跑未信任包 | 加 --sandbox 并确保满足七项要求，或换到已知可信包 |
| p2_feasibility_runner.py F1 0.0 | manifest.json 被限额耗尽时 stat 为 0 | 先补 token 重跑 crawl_static.py |

---

## 下一步（四指标全过）

可行性试点通过后 → 扩到 200-300 server 全量静态抓取 → Docker 启动 → 容器化动态 tools/list → AST/dataflow 抽取能力（F3） → codebook 双人标注（F4） → 跑完整测量论文管线。
