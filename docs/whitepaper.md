# Veridix 技术白皮书

## 0. 这份文档怎么读

本文档不是模块清单，而是按“一个任务从创建到交付，系统内部到底发生了什么”来写的工程说明。每一章都回答三个问题：这个模块解决什么问题，它的输入和输出是什么，它内部按什么顺序处理数据。

如果你只想快速理解 RAG，可以直接跳到第 12 章；如果你想理解 Agent 是怎么被约束的，先读第 8 到第 10 章；如果你关心漏洞是怎么被判定为真实的，直接读第 16 章。

文档中的代码片段都是仓库里真实存在的契约或伪代码，不是概念示意。

## 1. 系统是什么

Veridix 是一个面向授权安全测试的 AI Agent 系统。它把“让大模型决定下一步做什么”和“让系统保证每一步可控、可验证、可恢复”两件事分开。

大模型负责理解任务、提出假设、选择工具、解释结果。系统负责范围检查、工具授权、上下文投影、证据判定、状态持久化、人工门禁和报告生成。

一个典型任务从创建到交付会经过下面这些阶段：

1. 用户在 Web/TUI/CLI 创建一个 Project、Target 和 Mission。
2. 系统创建 Run，状态为 `queued`。
3. Worker 轮询到该 Run，认领并执行。
4. Kernel 或 Graph 决定先跑哪些节点。
5. 每个节点通过 Harness 生成受限上下文，再让模型提出工具调用。
6. 工具调用经过 ToolBroker 授权后执行，观察结果进入下一轮。
7. Loop Oracle 判断假设是否成立。
8. 成立的假设生成 Finding 和 Evidence。
9. 证据门禁通过后 Finding 进入 `supported` 或 `verified`。
10. 用户审查、复测、修正，最后导出报告。

## 2. 进程和端口

默认本地开发栈包含四个进程：

| 进程 | 默认端口 | 作用 |
| --- | --- | --- |
| Control Plane | 8787 | 保存所有状态，提供 API |
| Agent Worker | 8788 | 执行 Run |
| Lab Provider | 8766 | 本地模拟模型端点 |
| Web | 5173 | React 管理界面 |

Control Plane 是唯一权威状态源。Worker 不直接改数据库，它通过 API 认领 Run、提交事件、提交 Finding。这样做的好处是：Web/TUI/CLI 看到的永远来自同一套状态，Worker 崩溃后可以由另一个 Worker 接管。

## 3. 仓库结构

```text
apps/
  cli/       TypeScript CLI
  tui/       Ink + React 终端界面
  web/       React + Vite 网页
packages/
  contracts/         JSON Schema 生成 TS/Python 契约
  sdk-typescript/    前端 SDK
services/
  control_plane/     FastAPI 控制面
  agent_runtime/     Kernel / Loop / Worker
  knowledge_service/ 知识、RAG、记忆、技能、MCP
  mission_orchestrator/ Graph 调度与持久化
  evidence_service/  Finding / Evidence / 报告
  lab_provider/      本地模拟模型
  research_service/  基准与指标
  release_service/   SBOM / 签名 / 打包
runners/
  container/  Docker 沙箱
  web/        Browser / Proxy / ZAP / Burp / Caido
  remote/     远端节点 / SSH / OAST
deploy/
  system/   存储、工具镜像、ZAP 的 Compose
  toolpacks/工具包清单
skills/builtin/  内置技能
knowledge/builtin/  内置知识
```

## 4. 核心数据模型

### 4.1 Run

Run 是用户发起的一次任务执行。它的状态包括：

```text
queued -> claimed -> running -> succeeded
                              -> failed
                              -> cancelled
                              -> paused -> running
                              -> attention_required
```

`paused` 有两种来源：用户主动暂停，或者预算耗尽。`attention_required` 表示工具执行后无法确定副作用是否已经发生，需要人工决定继续还是中止。

### 4.2 AgentRunSpec

每个 Run 都有一个不可变的执行规格，包括：

- 允许的目标列表；
- 允许的工具列表；
- 最大轮次；
- 最大工具风险等级；
- 模型端点与模型名；
- 墙钟预算；
- 预算策略。

模型只能调用这个规格里允许的工具。ToolBroker 会再次检查，防止上下文注入或模型越权。

### 4.3 LoopSpec

Loop 是“一个角色内部反复观察和行动”的闭环。LoopSpec 是一个声明式契约，包含：

- 允许的工具和技能；
- 知识查询词；
- Oracle 类型；
- 成功标准；
- 失败策略；
- 重试策略；
- 预算；
- 风险等级；
- 证据要求；
- 沙箱 profile。

后面第 9 章会用一个实际例子说明这些字段怎么影响行为。

### 4.4 Finding

Finding 是最终交付给用户看的漏洞条目。它的状态机比 Run 更严格：

```text
candidate -> supported -> verified -> open/fixed/retest_passed
                       -> rejected
                       -> inconclusive
                       -> duplicate
```

模型不能直接创建 `verified` Finding。只有 EvidenceService 根据证据校验后才能推进状态。

## 5. Control Plane 是怎么保存状态的

### 5.1 事件流

Control Plane 把所有关键动作保存为 append-only 事件。每个事件有：

- stream_id；
- sequence；
- event_id；
- event_type；
- actor；
- occurred_at；
- payload。

Web 页面通过 `after=N` 游标拉取事件增量，而不是每次重算整个 Run。

### 5.2 为什么用事件而不是直接改表

事件可以让系统从任何时间点重放执行过程。比如用户问“这个 Finding 是怎么来的”，系统可以把相关事件按顺序拼出来，而不是只给一个最终状态。

Worker 把事件先缓冲在本地，再批量提交给 Control Plane。这样即使控制面暂时不可达，Worker 也不会因为每一条事件都失败而中断。

### 5.3 Command Outbox

用户操作 Worker 时，例如暂停或恢复，命令先写入 Outbox，再由控制面事件驱动执行。这样可以避免 Worker 和 UI 同时修改状态造成竞争。

## 6. Worker 是怎么执行任务的

### 6.1 轮询

Worker 启动后不断调用 `GET /api/v1/runs`，然后按状态处理：

```text
queued   -> claim + start
paused   -> resume from checkpoint
running  -> adopt if local worker has no active thread
```

### 6.2 认领

Worker 对 `queued` Run 调用 `POST /runs/{run_id}/claim`。认领幂等键是 `run_id:claim`，避免两个 Worker 同时执行同一个 Run。

### 6.3 构建执行环境

认领成功后，Worker 读取 Mission 和 Target，然后构建：

- AgentRunSpec；
- Provider 后端；
- ToolRunner；
- ToolBroker；
- CheckpointStore；
- ContextProjector；
- 事件缓冲器。

这些对象组合成一个可恢复的执行环境。

### 6.4 三种执行模式

Worker 根据 Mission 配置选择执行模式：

1. **默认 Kernel 模式**：一个模型实例连续多轮调用工具，直到 `run.finish` 或预算耗尽。
2. **multi_role 模式**：按角色模板执行多个 Loop 节点，节点之间通过 Blackboard 传递结构化事实。
3. **graph 模式**：用于基准测试，同场景分别跑单 Loop 和 Graph，比较 verified findings、成本和重复动作。

### 6.5 崩溃恢复

如果 Worker 崩溃，控制面状态仍然是 `running`。新的 Worker 会尝试：

1. 加载该 Run 的 Checkpoint；
2. 恢复 ToolBroker 已执行结果；
3. 恢复 GraphStore 中的节点状态；
4. 从断点继续。

Checkpoint 里保存了完整 transcript，而不是只保存光标。因此恢复后模型还能看到之前的工具调用和结果。

## 7. AgentKernel 的一轮是怎么跑的

### 7.1 输入

Kernel 每个 Turn 的输入是 `ContextView`，包含：

- Mission 文本；
- Target；
- 已裁剪的观察列表；
- 剩余预算；
- ContextBlocks。

### 7.2 处理流程

```text
1. 调用 provider.stream(context)
2. 模型返回 delta 文本或工具调用
3. 如果是工具调用：
   a. ToolBroker.authorize()
   b. 如果拒绝，记录 tool.denied，继续
   c. 如果允许，构造 ExecutionRequest
   d. runner.execute()
   e. 判断 side_effect_state
   f. 对 stdout 做 ContentTrust 检查
   g. 加入 observations
4. 如果模型调用 run.finish，Run 成功
5. 每轮结束保存 Checkpoint
```

### 7.3 一个具体例子

假设 Mission 是“扫描目标并验证是否存在暴露服务”。

第一轮模型可能调用 `nmap.scan`。ToolBroker 检查目标是否在 allowed_targets 内、工具是否在 allowed_tools 内、风险是否超过上限。通过后，Docker runner 执行 nmap，返回 stdout。

Kernel 把 stdout 加进 observations，下一轮模型会看到“nmap 已经跑过，输出里有这些端口”。如果模型继续重复调用同一个工具且没有新观察，Kernel 会通过去重计数提醒它换动作。

## 8. Harness 是怎么限制模型的

### 8.1 要解决的问题

如果直接把 75 个技能、整个知识库、所有 MCP 工具全部塞进 prompt，模型会混乱，也会被无关信息诱导。Harness 解决的是“一个节点到底应该看到什么”的问题。

### 8.2 构建流程

对每个节点，HarnessBuilder 按固定顺序处理：

1. 检查允许工具，排除不在注册表或超出 Provider 能力的工具；
2. 检查技能，排除不在 Loop 白名单、不匹配 profile、或工具/runner 不满足的技能；
3. 检查知识，排除不在 Mission subjects、或 trust 等级不足的知识；
4. 生成 ProjectionSnapshot；
5. 生成 HarnessSnapshot，里面包含所有投影的 digest。

### 8.3 为什么要有 digest

Digest 用于判断“这次投影和上次是否一样”。如果上下文没有变化，系统不会重复把同样的内容塞给模型，也会在事件里留下投影指纹，方便回放。

### 8.4 原生工具

有些工具是系统级的，不是来自工具 Pack：

- `run.finish`：结束 Run；
- `skill.read`：读取已投影技能包内的文本资源；
- `memory.recall/record/status`：操作项目记忆。

这些工具始终可以被模型调用，但同样受 ToolBroker 控制。

## 9. Loop 是怎么反复观察和验证的

### 9.1 目标

Loop 解决的是“一个角色怎么在一个领域内反复执行，直到假设得到验证或确认无法验证”。

### 9.2 主循环

```text
while iteration < max_iterations:
    1. 检查墙钟预算
    2. 检查是否有新进展
    3. 模型根据 LoopState 提出 ActionProposal
    4. 如果是 wait，进入 waiting
    5. 如果是 finish，调用 Oracle
    6. 否则执行工具
    7. 每次迭代后调用 Oracle
    8. 如果 Oracle 验证通过或到达终态，退出
```

### 9.3 无进展检测

系统不会只依赖 max_iterations。它会记录：

- 是否产生了新的 observation；
- 是否新增了 fact；
- 是否新增了 evidence；
- 是否重复调用了相同工具。

如果连续多轮没有任何进展，Loop 会发出 `loop.replan.suggested`，并进入 inconclusive，而不是继续空转。

### 9.4 预算策略

默认是“高上限 + 软预算”。`relaxed` 策略下，超预算只发事件；`strict` 策略下，超预算停止。真正的失控防护是“无进展检测”和“墙钟硬截止”。

### 9.5 Oracle 例子

假设 Scanner 节点把 `http://target/admin` 作为假设。Verifier 节点必须提供 `replay_proof` 事实。如果模型只输出一段文字说“已确认漏洞”，Oracle 不会通过。只有观察里真的出现 replay proof，且证据引用存在时，Oracle 才返回 verified。

## 10. Graph 是怎么编排多角色的

### 10.1 要解决的问题

一次完整安全测试通常需要 Recon、Scanner、Verifier、Reporter 多个角色。角色之间不能只靠“把上一个人的总结发给下一个人”，因为那样会丢失证据。

### 10.2 GraphScheduler

GraphScheduler 管理：

- 节点前置条件；
- fan-out/fan-in；
- handoff；
- dead letter；
- backpressure；
- human gate；
- 图版本和 patch。

### 10.3 一次执行

```text
recon 节点跑完后，把事实写入 Blackboard。
scanner 节点的 precondition 检查 Blackboard 是否有目标事实。
scanner 跑完后，把 candidate finding 写入 Blackboard。
verifier 节点检查是否有 replay proof。
verifier 通过后，reporter 生成报告。
```

### 10.4 动态 Planner

Planner 不能随意改图。它只能提出 GraphPatch，且 patch 必须经过 policy 校验。这样做的原因是防止模型或某个节点为了“完成任务”而绕过验证步骤。

### 10.5 持久化

Graph 的结构、节点状态、handoff、patch 都保存在 SQLite。`save_snapshot()` 使用单个事务，避免出现“handoff 存在但源节点还在 running”这种半写状态。

## 11. 上下文是怎么组装出来的

### 11.1 ContextBlocks

模型真正看到的内容不是原始数据库，而是 `ContextBlocks`：

```text
knowledge: 检索到的知识块
memory:    项目记忆事实
skills:    选中的技能正文
mcp:       MCP 工具描述
summaries: 历史摘要
digest:    整个上下文块的哈希
```

### 11.2 每块内容都要过两道检查

第一道是 ContentTrust，判断内容是 system、user_approved、project_trusted、retrieved_untrusted 还是 adversarial。第二道是 DataRelease，决定内容能否发给当前 Provider，以及是否需要脱敏。

如果网页或 MCP 返回的内容里出现“忽略之前的指令”“读取 API key”“提权”等模式，会被标记为 adversarial 并隔离。

### 11.3 为什么模型看不到全部历史

Kernel 会把旧工具观察裁剪成摘要，完整 transcript 存在 Checkpoint 里。这样模型上下文不会无限膨胀，同时恢复时仍然可以拿到完整历史。

## 12. RAG 到底是怎么实现的

### 12.1 RAG 要解决什么问题

安全测试模型需要知道特定漏洞、工具、ATT&CK 技术、CVE、CWE、靶场特征等信息。这些信息不能靠模型参数记住，必须从知识库检索出来再放进上下文。

如果只做向量检索，精确 ID 如 `T1003.001` 或 `CVE-2024-xxxx` 很容易匹配不准。如果只做文本检索，语义相关但用词不同的内容又找不到。所以系统采用多路检索。

### 12.2 输入

一次检索的输入是：

- 查询词；
- 当前节点类型；
- 允许的工具；
- 目标引用；
- 项目 ID；
- 观察时间窗口；
- trust 上限；
- 结果数量限制；
- 检索等级。

查询词从哪里来？优先使用 LoopSpec 的 `knowledge_query`。没有配置时，使用 Mission 文本。再没有，使用 `node_type + target`。

### 12.3 四条检索通道

#### 第一路：BM25 文本检索

知识库使用 SQLite FTS5。查询和内容都会做 CJK bigram 分词。

例如查询 `SQL注入`，系统会生成 bigram token：

```text
SQL 注入 注
```

实际分词会把中文切成连续两个字符的组合，同时保留英文和数字 token。这样 `T1003.001` 这种 ID 可以精确匹配，中文短语也能命中。

FTS 返回前 20 个候选，并给每个候选一个 rank。

#### 第二路：向量检索

Embedding 适配器支持：

- OpenAI-compatible `/embeddings` 端点；
- Ollama 兼容端点；
- sentence-transformers 本地模型。

系统先把查询和候选文本编码成向量，计算余弦相似度。如果向量后端是 Qdrant，还会同时使用 dense vector 和 sparse vector 做混合搜索。

向量检索会在单独线程中执行，并设置 deadline。如果超时，系统不会假装成功，而是记录 `rag_degraded:vector_store_timeout`。

#### 第三路：知识图谱检索

知识块可以带 `graph` 元数据。系统把 chunk 和节点/边关联起来，检索时除了匹配查询，还会扩展邻居节点，找出相关 chunk。

图后端可以是 SQLite，也可以是 Neo4j。图检索同样有超时保护。

#### 第四路：语义重排

候选合并后，如果配置了 rerank，系统会把前 10 个候选和查询一起交给 rerank 模型，得到更准确的排序。

支持：

- OpenAI-compatible `/rerank`；
- fastembed 本地 cross-encoder；
- 本地 sentence-transformers。

### 12.4 合并算法

系统使用 RRF（Reciprocal Rank Fusion）合并多路结果。

公式：

```text
score(chunk) = sum(1 / (60 + rank))
```

每一路返回的排名越靠前，贡献越大。它不依赖不同模型的分数可比性，只依赖排名，因此可以把 BM25、向量、图谱的结果直接合并。

也支持 `weighted` 和 `vector_first` 两种融合策略，默认是 RRF。

### 12.5 范围过滤

合并之后，最终候选必须满足：

- `target_refs` 为空，或包含当前目标；
- `observed_at/expires_at` 在当前时间窗口内；
- project 匹配。

这一步很重要，避免把另一个目标的扫描知识带到当前任务。

### 12.6 进入上下文

最终 top-N 会进入 `KnowledgeView`。系统再按 token budget 决定保留多少内容。每块内容都会带 chunk_id、source_ref、trust 和 version，方便模型引用，也方便用户追溯来源。

### 12.7 一次实际检索的例子

假设当前节点是 Verifier，查询词是 `ssrf_callback`。

BM25 会找到包含 `SSRF`、`callback`、`OAST` 的知识块。向量检索会找到语义接近的“服务端请求伪造回调解法”。图检索会扩展 `SSRF -> OAST -> callback` 相关节点。三者 RRF 合并后，rerank 把最匹配的三块排到前面。

最终模型上下文里会出现三块知识，每块都带来源。模型随后执行 `oast.create -> web.ssrf.test -> oast.check`，形成真实回调证据。

## 13. Skills 是怎么被选择和加载的

### 13.1 SKILL.md 是什么

技能不是一段随意写的 prompt，而是一个目录：

```text
skills/builtin/<name>/
  SKILL.md
  references/
  scripts/
```

`SKILL.md` 开头是 YAML frontmatter：

```yaml
---
name: strix-ssrf
version: 1.0
trigger: web_discovery
description: SSRF detection and verification workflow
required_tools:
  - oast.create
  - web.ssrf.test
  - oast.check
runner: native
risk_level: L2
---
```

正文是实际给模型看的方法论。

### 13.2 选择流程

Worker 启动时把所有技能包解析进注册表。真正给模型之前，系统先做语义召回：

1. 用当前查询对技能做 BM25 和向量检索；
2. RRF 合并；
3. 可选 rerank；
4. 过滤不在 allowed_skills 白名单的技能；
5. 过滤 required_tools 不在节点允许列表的技能；
6. 过滤 runner 不匹配的技能；
7. 按 token budget 保留前 N 个。

### 13.3 为什么 description 重要

技能检索的文本包括：

```text
name
description
category
trigger
tags
CWE
required_tools
content 前 1400 字
```

所以 `description` 不是给人看的装饰，它直接参与 BM25、embedding 和 rerank。

### 13.4 外部技能怎么接入

用户可以从 Web 上传 `.zip` 技能包，或直接粘贴 `SKILL.md`。系统会把包写入 `runtime/skills/<name>/`，校验路径穿越和文件大小，运行 conformance，然后让 Worker 在下一轮上下文构建时自动重新加载。

技能不是放进去就能用。如果 `required_tools` 不在工具注册表，或者当前节点的 allowed_tools 不包含这些工具，技能会被排除，并在投影事件里记录原因。

## 14. 项目记忆是怎么积累的

### 14.1 记忆是什么

项目记忆保存的是结构化事实：

```text
subject -> predicate -> value
```

例如：

```text
http://target -> observed:nmap.scan -> "80/tcp open http"
```

### 14.2 写入规则

记忆是 append-only。新事实不会删除旧事实，而是追加一条。系统通过 projection 计算当前 active、conflict、stale。

模型可以调用：

- `memory.recall`：检索事实；
- `memory.record`：写入观察；
- `memory.status`：查看快照。

`memory.record` 不能提升 trust。用户可以通过 Web/TUI/CLI 修正、遗忘、清空记忆。

### 14.3 记忆如何进入上下文

每次构建上下文时，系统会：

1. 检索 active/conflict 事实；
2. 按 token budget 保留最相关事实；
3. 把最近 5 条项目摘要放进上下文；
4. 生成 memory digest。

如果模型写入了新事实，系统会让当前 Run 的 ContextBlocks 失效，下一轮重新组装。

## 15. 工具是怎么执行的

### 15.1 ToolBroker

工具调用必须经过 ToolBroker。它检查三件事：

1. 工具是否在 allowed_tools；
2. 目标是否在 allowed_targets；
3. 工具风险是否超过 max_tool_risk。

### 15.2 Runner 选择

系统根据工具定义选择执行器：

- `native`：直接在本进程执行，例如内置验证器；
- `container`：在 Docker 沙箱执行；
- `browser`：用 Playwright 执行；
- `remote`：下发给远端节点。

### 15.3 输出处理

工具输出不是简单丢回给模型。系统会：

- 限制 stdout/stderr 大小；
- 检查 side_effect_state；
- 对 stdout 做 ContentTrust；
- 解析 structured observation；
- 把 artifact refs 记录到 Evidence。

### 15.4 工具 Pack

工具按 Pack 管理：

```text
network: nmap, masscan, naabu, subfinder, httpx, dnsrecon...
web: ffuf, gobuster, sqlmap, nikto, dirsearch, wpscan, zap...
vulnscan: nuclei, fscan, metasploit.console
host: hydra, enum4linux, smbmap...
binary: objdump, readelf, gdb, binwalk...
code: semgrep, secret scan
ad: LDAP, Kerberos, impacket...
```

每个工具定义包含 schema、risk level、runner、sandbox profile、healthcheck 和示例参数。

## 16. Evidence 是怎么判定漏洞为真的

### 16.1 Finding 的生命周期

当节点认为发现了一个漏洞时，它提交 `candidate` Finding。

系统会做指纹去重。如果同 target、类别、endpoint、param 已经存在，新 Finding 标为 `duplicate`。

### 16.2 Support 阶段

`support()` 会校验 Finding 引用的 Evidence 是否真实存在，并且 hash 是否匹配。通过后状态变为 `supported`。

### 16.3 Verify 阶段

`verify()` 需要 Oracle 判定。不同领域使用不同 Oracle：

- 普通 Web 扫描：需要 replay proof；
- Authz：需要双上下文矩阵比较；
- SSRF：需要一次性 callback 证据；
- GraphQL/WebSocket：需要 baseline/tampered diff；
- 结构化 Finding：需要 category、severity、evidence 字段都满足。

Oracle 返回 `verified` 时，Finding 才进入 verified。

### 16.4 CVSS

Finding 创建时会根据漏洞类别自动生成 CVSS v3.1 向量。系统实现完整的 v3.1 base score 计算，包括 Scope 条件和 roundup。

### 16.5 报告

报告不是模型生成的总结，而是由 Control Plane 根据 Run 事件、Findings、Evidence 生成的 Markdown/HTML/Bundle。

## 17. 三端是怎么观察同一个系统的

Web、TUI、CLI 不保存自己的状态。它们都调用 Control Plane API。

Web 使用 React Query 轮询事件，TUI 使用 Ink 渲染同一个事件流，CLI 提供命令式操作。用户在任何一端暂停 Run，其他端都会看到暂停状态。

## 18. 部署与配置

### 18.1 最小启动

```bash
npm ci
python -m pip install -r services/requirements.txt
python scripts/env_up.py
```

`env_up.py` 会启动存储、工具环境、ZAP、Control Plane、Worker 和 Web。

### 18.2 真实测试需要什么

默认 Worker 使用 `fake` runner，适合演示。真实扫描需要：

```bash
export VERIDIX_RUNNER=docker
export VERIDIX_TOOL_NETWORK=veridix-system_veridix-net
```

还需要配置一个真实模型端点，例如 DeepSeek：

```bash
export DEEPSEEK_API_KEY="..."
veridix provider register deepseek \
  --endpoint https://api.deepseek.com/v1 \
  --model deepseek-v4-flash \
  --api-key-ref env:DEEPSEEK_API_KEY
```

### 18.3 存储

默认桌面模式使用 SQLite FTS、SQLite vector 和 SQLite graph。Server 模式可以启用 pgvector、Qdrant、Chroma 和 Neo4j。

Embedding 和 rerank 不打包进工具镜像，而是使用用户配置的云端或本地模型端点。

## 19. 怎么扩展系统

### 19.1 新增一个工具

1. 在 `deploy/toolpacks/<pack>.json` 添加 tool definition；
2. 在工具镜像里加入可执行文件；
3. 在 runner factory 注册执行器；
4. 写 smoke 测试。

### 19.2 新增一个技能

1. 创建 `skills/builtin/<name>/SKILL.md`；
2. 填写 frontmatter；
3. 运行 skill conformance；
4. Worker 会自动发现。

### 19.3 新增一个 Loop Profile

1. 在 `loop_profiles.py` 定义 LoopProfile；
2. 注册到 REGISTRY；
3. 在 Web/TUI/CLI 里即可选择。

### 19.4 新增一个角色模板

1. 在 role templates 定义角色顺序；
2. 在 roles.py 定义各角色的 allowed_tools 和 Oracle；
3. 增加 fixture 测试。

## 20. 测试与发布

系统有完整的验证分层：

- 单元测试：Kernel、Loop、Graph、RAG、Evidence；
- 集成测试：Worker + Control Plane；
- E2E：Web/TUI/CLI；
- 真实门禁：DeepSeek + Docker；
- 恢复演练：Worker 崩溃、GraphStore 恢复。

一键验收：

```bash
python scripts/acceptance_gate.py
python scripts/acceptance_gate.py --real
```

发布检查：

```bash
python scripts/release_gate.py --dry-run
python scripts/build_release_local.py
```

## 21. 设计边界

- 模型不能绕过 ToolBroker；
- 模型不能直接创建 verified Finding；
- 未授权目标会被拒绝；
- 远端结果必须签名；
- 技能/知识/MCP 内容都按 trust 分级；
- 所有关键动作都可从事件流回放。

这套设计的核心不是“模型有多聪明”，而是“系统能否让聪明变得可控、可验证、可恢复”。
