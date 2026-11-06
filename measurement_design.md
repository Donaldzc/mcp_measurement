# MCP/Agent 工具生态测量研究设计（v2，吸收审稿意见修订版）

**修订声明**：本版逐条吸收以下审稿意见：抽样框架与纳入排除标准（1）、实体图与 gold set 验证（2）、离线 sandbox 与分层报告（3）、AST/SBOM 主分析（4）、codebook+kappa 标注（5）、release-level panel 与漂移拆解（6）、policy-checkable 用词与双人编码（7）、分析单元定义与可复现性（8）、注入面指标重定义与 cluster bootstrap（9）、固定 commit SHA 采集流程（10）。

## 一、分析单元与实体图（定义先于采集）

**主分析单元**：可独立部署的 MCP server（简称 server）——一个可被 Agent 客户端以 stdio/HTTP/SSE/WebSocket 任一协议启动并完成 MCP 握手的最小逻辑单元。tool 作为 server 的嵌套层级，repo/package/deployment 仅作为来源属性或部署形态。

**四级实体图**：

```
artifact (repo / npm包 / PyPI包 / registry 条目 / docker 镜像)
  └─ server (一个 artifact 可含多个 server：monorepo 子模块、多入口包)
       └─ tool (工具名+描述+schema+handler，一个 server 可暴露多个)
            └─ deployment (运行时配置：传输协议、鉴权、宿主环境)
```

去重与实体解析验证：构造 200–300 server 的人工 gold set（手动枚举实体边界与归属），计算跨源去重的 precision/recall，报告到正文；cluster bootstrap 按 publisher/repo 做，不把同源工具当独立样本。

## 二、抽样框架与纳入排除标准

**抽样框**：以四个聚合 registry 为 frame（mcp.so、Smithery、PulseMCP、Glama），经 ToS 核查后做结构化抓取（分页上限待按各 API 上限补齐 [待补充：各 API 上限与认证方式]）。GitHub/npm/PyPI 搜索命中数仅用于估计 registry 的召回缺口，**不直接作为生态规模**，也不构成抽样框。

**纳入标准**：（1）注册或登记于任一聚合 registry；（2）明确宣称支持 MCP 协议 v0.5+ 及以上；（3）可独立部署（排除仅作为 SDK 库的 package）。

**排除标准**：（1）仅作为 SDK/框架提供（例如 FastMCP、官方 TypeScript SDK）；（2）仓库内仅含 README，无可执行入口；（3）聚合 registry 条目已删除且无任何镜像；（4）重复条目（以实体图去重后仅保留一个代表）。

**抽样策略**：主样本按 source（registry）× transport（stdio/HTTP/SSE/WebSocket）× language（TS/Python/Go/其他）× publisher type（个人/公司/组织/AI 服务机器人）四层分层概率抽样，目标 200–300 server（可行性试点先 50）。top-star 样本不并入主样本，单独作为 popular stratum 报告，分层权重需显式声明。

## 三、数据需求清单（四类）

D1 实体登记数据
来源：四个 registry API 全量枚举 + GitHub/npm/PyPI 结构化补全（API 上限与 ToS 待 [待补充]）。
字段：实体 ID、来源、名称规范化、部署方式、registry 元数据、commit SHA、包版本、license。
用途：实体解析、分层抽样权重计算、生态规模估计。

D2 工具接口数据（注入面语料）
路径分层报告，**静态文档（离线只读）与动态 tools/list（sandbox）分开统计**，不混合。
静态来源：README、manifest 字段、注册 registry 的工具描述。
动态来源：离线 sandbox 内执行 initialize → notifications/initialized → tools/list。
字段：工具名、描述全文、inputSchema、协议能力声明（capabilities）。

D3 能力面数据
主方法：AST/dataflow 静态分析（跨语言：TypeScript via tsc，Python via ast）+ SBOM（npm audit / PyPI 依赖清单）。
辅助：规则集识别网络外联域名、文件系统路径、子进程调用、环境变量/凭证读取、动态执行（eval/exec/Function）。
抽样子集（≥50 server）做 syscall/network 动态验证与静态结果比对，报告静态能力一致率。

D4 演化数据
来源：libraries.io + Software Heritage 归档 + git tag/commit 对齐。
漂移拆解为五类：描述漂移（语义相似度阈值 0.85 扫描）、能力漂移（AST/SBOM 差异）、依赖漂移（SBOM diff）、安装脚本漂移（build/postinstall 字段）、维护者漂移（owner/committers diff）。

## 四、伦理与安全边界（硬门禁）

未信任 server 的动态执行必须在一次性隔离容器内进行：（1）禁网络；（2）只读根文件系统，仅 /tmp 可写且有容量限额；（3）无环境变量凭证、无 SSH key、无 Git 凭据；（4）资源限额（CPU 核心、内存、时间）；（5）固定 commit SHA 或包版本，每次采集记录哈希；（6）发现潜在漏洞按负责任披露流程处理，公开信息不包含 exploit 细节。

## 五、可行性试点（50 server）先于全量

试点四指标（全过才放量）：
F1 抓取成功率：静态/动态各自的可达率，分 source 报告，低于 85% 需排查网络或 sandbox 配置。
F2 缺失率：实体登记字段缺失比例 + tool 描述缺失比例。
F3 静态/动态一致率：抽 20 server，比对静态 AST 抽取的能力与动态 tools/list 返回的声明，报告 Cohen's kappa。
F4 标注一致率：codebook 先行（见下），双人标注 100 条工具描述，kappa ≥ 0.6 才进入全量。

## 六、codebook v3 与标注流程

### 6.1 双人标注方法论（codebook 迭代 → 冻结 → 确认）

采用"试点迭代 → 规则冻结 → 确认样本独立盲标"三段式，避免向确认样本过拟合：

| 阶段 | 样本 | 种子 | 用途 | kappa |
|---|---|---|---|---|
| 试点-1 | n=105 | 42 | 初始 codebook + 双人标注 | **0.183** |
| 试点-2 | 同 n=105 | 42 | 33 条分歧逐条归因 → codebook v2 | **0.417** |
| 试点-3 | 同 n=105 | 42 | 剩余 4 条分歧 → codebook v3 冻结 | **1.000**（0 分歧） |
| **确认** | **n=107** | **43** | 排除试点 105 条，codebook v3 冻结后独立盲标 | **macro=0.961** |

**论文报告值 = 确认样本 macro kappa = 0.961**（超出 ≥0.6 门槛）。试点 1.0 为过拟合产物，不可报告。

### 6.2 codebook v3 操作化定义（6 类，每类 0/1）

| 类别 | 判定规则 | 排除项 | 正则锚 | 严重度 |
|---|---|---|---|---|
| **C1 直接命令 Agent** | 调用时机/顺序强制性指令 + 对 agent 回答行为指令 | 描述性 "use this to \<verb\>"、API 参数约束 | `you must call`、`first/then call`、`start here`、`check again`、`use this before (calling|making)`、`be concise`、`prioritize` | 2 |
| **C2 跨工具引用** | 编排性引用（调用/排序/替代/组合另一工具）——非字母名前方 80 字符需编排动词；纯字母名（whoami/login）仅动词直接紧邻 | 纯描述性提及 "Complements X"、"reserved with X"；端点路径 "via /upload/image" | 精确名双层判定 + 泛指模式 `use the matching X tool`、`before 'X' tool`、`found with X` | 2 |
| **C3 嵌入 URL/代码** | http(s) URL（含文档引用）、真实 HTML 标签白名单、三反引号块、真实 base64 blob/data URI | 对 "base64 format" 的提及（非真实编码）、配置键列举（61 字符含斜杠，阈值 80 以上才算） | `https?://`、HTML 标签白名单、```、`data:;base64,` + 80+ 字符 [A-Za-z0-9+/]= | 3 |
| **C4 凭证/密钥** | api key、secret、password、cookie、PII 直接命中；token 需凭证语境 | token 排除认证机制说明 "the token is accepted"；credential 排除 auth/credential failure 错误语义 | `api-key`、`secret`、`password`、`cookie` + token 需 ±40 字符内 `your|provide|header|bearer...` 语境 | 1 |
| **C5 敏感路径** | /etc/、~/.ssh、.ssh/、.env、/root/、C:\、.aws、.gnupg、id_rsa | 容器挂载点 /home/sandbox、/opt/output | 直接正则 | 3 |
| **C6 隐藏/弱化** | 显式隐藏词 + quietly/silently（需否定守卫） | "never call this silently"（反隐藏指令）、系统行为告警 "gate silently rejects" | `without telling the user`、`internal knowledge`、`quietly` + 需否定守卫（排除 `never|not|don't|avoid` 前 20 字符） | 3 |

严重度计算：取阳性类最大值；0=无阳性。

### 6.3 adjudication 流程（试点第二轮 33 条分歧 → 第三轮剩余 4 条）

所有分歧逐条留痕并转化为规则修订，形成可复现 artifact（f4_sample.py 的正则 + 判定函数）：

- **C2 误捕/漏捕 8 条**：`requires?` 无词边界 → 加 `\b`；纯字母名 whoami 被 isalpha 拦截 → 双层判定；"via" 误命中端点路径 → 从 cue 表剔除
- **C1 漏报 4 条**："Use this to ... before making/calling ..." 句式 → CMD 新增 `use this.*before` 模式
- **C6 误报 2 条**："a stricter gate silently rejects"（系统行为告警）→ 加 SILENT_SYS 后随抑制；"never call this silently"（否定隐藏）→ 加 NEG 否定守卫
- **C3 误捕 1 条**：配置键列举 61 字符含斜杠 → B64 阈值 60→80

### 6.4 确认样本 7 条诚实分歧（frozen 规则代价，不追加）

以下分歧为冻结规则无法覆盖的边缘模式，**如实报告**（规则召回率下限 ≈ 0.78）：

| ID | 分歧 | 原因 |
|---|---|---|
| T045 | C1+C2 A=0 B=1 | SAP Fiori "CRITICAL - TARGET FOLDER MUST..." 指令模式未覆盖 |
| T056/T080/T081/T096/T105 | C2 A=0 B=1 | "via X"、"by calling X"、"Get X from Y"、"Requires If-Match..." 等非精确名引用模式 |

### 6.5 全量标注结果（n=885，frozen codebook v3，Annotator A）

| 类别 | 阳性数 | 阳性率 |
|---|---|---|
| C1 直接命令 Agent | 58 | 6.6% |
| C2 跨工具引用 | 172 | 19.4% |
| C3 嵌入 URL/代码 | 15 | 1.7% |
| C4 凭证/密钥 | 27 | 3.1% |
| C5 敏感路径 | 1 | 0.1% |
| C6 隐藏/弱化 | 7 | 0.8% |

严重度分布：sev=0（无阳性）657/885=74.2%，sev=1（凭证型）11，sev=2（指令型）196，sev=3（嵌入/路径/隐藏型）21。

**Annotator A 可复现 artifact**：`f4_sample.py`（完整冻结规则 + 正则 + 判定函数），全量输出 `f4_all.csv` + `f4_all_summary.json`。

### 6.6 标注流程（修订后）

codebook 试点迭代 → 规则冻结 → 确认样本独立盲标（报告 macro kappa）→ 冻结规则直接跑全量 → 全量结果中双方并集阳性条目做 LLM adjudication（无工具、结构化 JSON 输出）→ 最终全量标注报告 prevalence。

## 七、RQ 修订

**RQ1（规模与结构）**：以 server 为单位，生态规模、分布、活跃度与集中度；top-star popular stratum 的差异。
**RQ2（注入面）**：工具描述安全指令六类的 prevalence，跨严重度分层；静态（README）vs 动态（tools/list）的描述差异率。
**RQ3（能力面）**：声明能力 vs 静态抽取能力的偏差率；跨类 server 的能力集中度（网络+凭证双能力占比与基尼系数）。
**RQ4（供应链）**：发布主体类型与安装方式分布；CI 直发 vs 人工发布的安全姿态差异；依赖树风险。
**RQ5（演化）**：server 生命周期与五类漂移事件的时间模式；漂移与安全事件的关联。

## 八、指标定义（全部可复现）

（见第三节 D2–D5 对应字段定义，以及第五节 F1–F4。关键：所有指标锚定 server 分析单元，分层权重由抽样概率计算，cluster bootstrap 按 publisher 聚类报告置信区间。）

## 九、形式化映射（policy-checkable，非 provable）

**用词校正**：本研究不声称"可证明"安全性质，也不证明工具链满足 non-interference 或 leakage bound。本研究输出的是 policy-checkable 规则集——每条规则可由调度层在运行时对每次工具调用做判定，判定结果与当前工具链状态直接关联。规则集本身的完备性（是否覆盖所有测量到的风险模式）由双人编码独立验证，报告 inter-coder agreement（Cohen's kappa ≥ 0.7）。映射示例：

| 测量到的风险模式 | policy-checkable 规则（示例） |
|---|---|
| 跨工具引用出现在安全敏感 server 的工具描述中 | 规则 R：server 若声明能力集合 C_sensitive ⊇ {网络外联, 凭证}，则其工具描述中不得包含显式跨工具调用指令 |
| 动态 tools/list 返回的参数 schema 包含 /etc/ 路径 | 规则 P：schema 中任何 string 类型字段不得与敏感路径正则匹配 |
| 安装脚本在 postinstall 段触发 curl \| bash | 规则 I：server 的 npm postinstall 段不得包含网络下载 + 即时执行模式 |

## 十、实验步骤（修订版）

E0 可行性试点：50 server，F1–F4 四指标过门槛后进入 E1。
E1 登记枚举：四 registry + GitHub/npm/PyPI 补全，按纳入排除标准过滤，生成实体登记。
E2 实体解析与去重：人工 gold set 验证 precision/recall。
E3 分层抽样：按四层概率抽样 200–300 server，固定 commit SHA + 包版本，缓存原始文件（内容哈希寻址）。
E4 静态采集：离线只读 AST + SBOM，分层报告静态注入面与能力面。
E5 动态采集：sandbox 内 tools/list，分层报告动态注入面。
E6 漂移分析：libraries.io + Software Heritage 归档对齐，五类漂移事件统计。
E7 形式化映射：双人编码 policy-checkable 规则集，报告 kappa。

## 十一、消融实验（修订版）

A1 能力抽取特征消融：仅 manifest → +工具描述 → +AST/SBOM，测量一致率提升；辅助规则 vs 主方法 AST 的增量贡献。
A2 注入面分类器消融：纯 codebook 规则 → LLM 辅助（无工具） → 融合，在 held-out 上比较精度/召回/F1；换两个底座模型检验稳健性。
A3 抽样策略消融：概率抽样 vs top-star popular vs 混合，比较各指标的置信区间宽度；分层权重敏感性。
A4 去重规则消融：不同实体解析强度下生态规模估计与命名冲突率的变化，报告敏感性。
A5 漂移判定阈值消融：语义相似度阈值 0.80–0.95 扫描，报告漂移事件数量的敏感性与人工复核一致率。
A6 静态 vs 动态能力差异：分层报告差异率，分析差异成因（README 未更新 vs server 实现变更）。

## 十二、可复现性基础设施（crawl_static.py 待修复）

（1）固定 commit SHA + 包版本：采集时记录每个来源的精确版本，重新运行脚本使用同一版本；（2）内容哈希缓存：按内容 SHA1 存储原始响应，避免重复抓取；（3）分层输出：静态文档与动态握手分别写入独立文件；（4）每轮采集生成 manifest.json（时间戳、来源列表、版本、限额状态），用于事后追溯。

## 待补充清单

（1）四个聚合 registry 的 API 上限与认证方式、ToS 条款；（2）gold set 规模与标注人力；（3）Sandbox 配置细节（容器镜像、资源限额值、禁网策略实现）；（4）AST/dataflow 跨语言工具选型；（5）libraries.io API 额度；（6）审稿意见中 "headroom（库/代理）" 等 top-star 样本污染的量化：重新跑实体解析看被排除的比例。
