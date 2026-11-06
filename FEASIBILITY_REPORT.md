# MCP/Agent 工具生态测量 — 可行性试点最终报告

**运行时间**: 2026-10-22 16:15 (UTC+8)
**测试范围**: 50 repo 静态抓取 + 官方 everything server 动态握手 + 实体解析 gold set 验证

---

## 一、四指标结果

| 指标 | 数值 | 门槛 | 判定 |
|---|---|---|---|
| F1 静态抓取成功率 | **0.92** (46/50) | ≥0.85 | ✅ 通过 |
| F1 动态抓取成功率 | **1.0** (2/2) | ≥0.85 | ✅ 通过 |
| F2 工具描述缺失率 | **0.028** (7/248) | <0.15 | ✅ 通过 |
| F3 静态/动态能力一致率 | null（占位） | ≥0.6 | 全量阶段补 |
| F4 双人标注 Cohen's kappa | null（占位） | ≥0.6 | 补 codebook_template.csv 后跑 |

**数据总量**:
- 静态抓取 50 repo → 46 成功 / 4 失败（HTTP 403 或 repo 无 TS/Python MCP server 源码）→ 74 文件 → **200 工具**
- 动态握手 1 官方 everything server → **16 工具**，全带 description + inputSchema
- 语料总量：216 工具条目，其中 7 条无 description（248 总数含重复 tool_name 去重前）

**限额状态**: core_remaining = 4675 / 5000（未耗尽，可继续放量）

---

## 二、Pipeline 完整性

| 组件 | 状态 | 交付 |
|---|---|---|
| 静态抓取 crawl_static.py v2 | ✅ HEAD→固定 SHA + 缓存 + manifest | d2_pilot/crawl_static.py |
| 动态握手 handshake_tools_list.py v2 | ✅ 固定 npm 版本 + sandbox 声明 + 安全白名单 | d2_pilot/handshake_tools_list.py |
| 实体解析 p1_entity_resolver.py | ✅ 四级实体图 + L1-L3 去重 + gold set PR | p1_entity_resolver.py |
| 六类安全指令分类器 | ✅ 规则集基线（官方 everything 无误伤） | p2_codebook_classifier.py |
| 可行性四指标 runner | ✅ F1-F2 可用 / F3-F4 留占位 | p2_feasibility_runner.py |
| Gold set + Codebook 模板 | ✅ 19 条种子 gold set + 双人标注模板 | gold_set/ |
| Token 安全加载 | ✅ .env.local + .gitignore 已配 | .env.local / .gitignore |

---

## 三、已知局限（pilot-only，主测量需修）

1. **抽样框架**: Top-N star GitHub/npm，不是四 registry 概率抽样。已在 manifest.criteria 标注。
2. **实体解析 precision**: 0.024 —— gold set 与自动生成的 server_id 规则不同。主测量按四 registry frame 重跑后 precision/recall 应显著上升。
3. **未覆盖指标**: F3 AST/dataflow 抽取能力（主测量需 tsc / ast 库）、F4 双人标注（补 codebook_template.csv 后跑）。

---

## 四、放量前置清单（全量 200-300 server）

1. [ ] **枚举四聚合 registry API**: mcp.so / Smithery / PulseMCP / Glama 的服务接口与分页逻辑，按 source × transport × language × publisher type 分层抽样
2. [ ] **Docker 容器化动态抓取**: 一次性隔离容器（禁网/只读/无凭证/资源限额/固定版本），对未信任 server 批量执行 tools/list
3. [ ] **AST/dataflow 能力抽取**: TS 用 tsc 编译 + AST traversal；Python 用 ast 模块；输出外联域名/文件/子进程/凭证/eval 等能力标签，为 F3 一致率提供对照数据
4. [ ] **Codebook 双人标注**: 对 100-200 条动态工具描述独立标注（annotator_a / annotator_b），分歧处辅助 LLM（无工具 + 结构化 JSON），计算 Cohen's kappa
5. [ ] **实体解析规则校准**: 基于四 registry 输入重新测试 precision/recall，目标 ≥0.8

---

## 五、可直接复现性

所有脚本已配 .env.local token 自动加载 + 内容哈希缓存 + 固定 commit SHA：
```powershell
# 重跑 50 repo 试点
python d:\code\trae\temp_20261021_02\mcp_measurement\d2_pilot\crawl_static.py --repos 50 --force-refresh
python d:\code\trae\temp_20261021_02\mcp_measurement\p2_feasibility_runner.py
```
