# Veridix 用户使用手册

## 1. 适用对象

本手册面向：

- 安全测试工程师：使用 Agent 执行授权扫描、验证和复测；
- 红队/护网团队：编排侦察、扫描、验证、利用复现与报告；
- 运维/部署人员：在本地、服务器或离线环境部署；
- 开发者：通过 CLI/SDK 集成或二次开发。

## 2. 前置条件

- Node.js `>=22`
- Python `3.11+`
- Docker Desktop / Docker Engine
- 一个可用的模型端点：
  - OpenAI-compatible，例如 DeepSeek、OpenAI、Ollama；
  - 或 LiteLLM 兼容端点；
  - 无外部端点时可使用内置 Lab Provider 进行演示。

推荐硬件：

- CPU：4 核以上；
- 内存：8GB 以上（含 Docker 与知识检索）；
- 磁盘：至少 20GB（工具镜像、知识库、测试靶场）。

## 3. 安装

### 3.1 完整产品安装

Veridix 通过 GitHub 仓库安装。克隆后构建并启动完整产品：

```bash
git clone git@github.com:kxahcc/veridix.git
cd veridix
npm ci
npm run build
npm run up
```

`npm run up` 会自动执行 Docker Compose，拉取存储和工具镜像，并启动 Control Plane、Agent Worker 和 Web。用户不需要手动逐个 `docker pull`。

启动后，CLI 使用 `npm run cli -- <命令>` 调用，TUI 使用 `npm run tui` 启动。

### 3.2 环境准备

仓库根目录提供了 [.env.example](../.env.example) 作为环境变量参考。绝大多数配置都有默认值，可以完全不设置环境变量，直接使用 Web `诊断与设置` 或 TUI 斜杠命令配置供应商、MCP、技能和检索存储。

```bash
npm ci
python -m pip install -r services/requirements.txt
```

Windows 可使用：

```powershell
npm ci
.\.venv\Scripts\python.exe -m pip install -r services/requirements.txt
```

### 3.3 一键启动

```bash
npm run up
```

等价命令：

```bash
python scripts/env_up.py
```

`env_up.py` 会启动：

- 存储：pgvector、Qdrant、Chroma、Neo4j；
- 工具环境：`veridix-tools`；
- DAST：ZAP；
- Host 栈：Lab Provider、Control Plane、Agent Worker、Web。

停止：

```bash
npm run down
```

### 3.4 工具环境检查

```bash
python scripts/tool_env_up.py
```

该脚本会验证容器内 `nmap / nuclei / masscan / sqlmap / subfinder / httpx / naabu / msfconsole / wpscan` 等工具可用。

### 3.5 运行与入口

日常安装、构建和运行使用：

```bash
# 安装依赖
npm ci

# 构建产品
npm run build

# 启动完整产品（存储 + 工具环境 + Control Plane + Worker + Web）
npm run up

# 停止产品
npm run down
```

启动后，Web 在 `http://127.0.0.1:5173` 使用，CLI 通过 `npm run cli -- <命令>` 调用，TUI 通过 `npm run tui` 启动。

当 npm 网络受限时，可以切换国内镜像：

```bash
npm config set registry https://registry.npmmirror.com
```

### 3.6 Docker 管理

系统使用 Docker 管理四类资源：存储后端、安全工具镜像、ZAP DAST 和测试靶场。

`npm run up` 默认从 GitHub Container Registry 拉取工具镜像：

```text
ghcr.io/kxahcc/veridix/veridix-tools:full
ghcr.io/kxahcc/veridix/veridix-tools:code-lite
```

统一系统 Compose：

```bash
docker compose -f deploy/system/docker-compose.yml --profile tool-env up -d
```

这条命令会启动：

- pgvector、Qdrant、Chroma、Neo4j；
- `veridix-tools` 工具环境；
- ZAP DAST。

查看状态：

```bash
docker compose -f deploy/system/docker-compose.yml ps
docker compose -f deploy/system/docker-compose.yml logs -f
```

停止系统环境：

```bash
docker compose -f deploy/system/docker-compose.yml down
```

测试靶场是独立环境，只用于授权测试，不随 Veridix 发布。靶场容器加入
`veridix-system_veridix-net` 网络后，容器工具可以直接用容器服务名访问目标，
例如 `http://compose-dvwa-1`。

镜像拉取默认使用 `docker.m.daocloud.io/` 作为镜像源，可通过 `VERIDIX_STORAGE_REGISTRY` 覆盖。网络不可达时，可以先把镜像拉到本机：

```bash
docker pull docker.m.daocloud.io/pgvector/pgvector:pg16
docker tag docker.m.daocloud.io/pgvector/pgvector:pg16 pgvector/pgvector:pg16
```

`npm run up` 会先自动执行 Docker Compose，再启动主机进程。工具镜像默认从 GHCR 拉取；离线环境仍可使用 `scripts/tool_env_bundle.py` 分发镜像 tar 包。

## 4. 配置模型供应商

### 4.1 环境变量

```bash
export DEEPSEEK_API_KEY="sk-..."
```

Windows：

```powershell
$env:DEEPSEEK_API_KEY="sk-..."
```

### 4.2 Web 配置

打开 `诊断与设置 -> 模型供应商`：

1. 选择常用供应商预设；
2. 填写 Provider id、Base URL、Model；
3. 填写 API key ref，例如 `env:DEEPSEEK_API_KEY`；
4. 点击“获取模型列表”；
5. 点击“测试连接”；
6. 保存并设为默认。

### 4.3 CLI 配置

```bash
npm run cli -- provider register deepseek \
  --endpoint https://api.deepseek.com/v1 \
  --model deepseek-v4-flash \
  --api-key-ref env:DEEPSEEK_API_KEY

npm run cli -- provider default deepseek \
  --endpoint https://api.deepseek.com/v1 \
  --model deepseek-v4-flash \
  --api-key-ref env:DEEPSEEK_API_KEY
```

### 4.4 内置 Lab Provider

无需外部模型时：

```bash
python -m services.lab_provider.app.main --host 127.0.0.1 --port 8766
```

然后在 Web/CLI 中配置：

```text
endpoint: http://127.0.0.1:8766/v1
model:    veridix-lab-flash
```

### 4.5 身份配置（可选）

默认情况下系统是本地单用户，不需要登录。如果需要开启访问控制，可以设置：

```bash
VERIDIX_CONTROL_USERS='{"op-token":{"role":"operator","projects":["project_xxx"]}}'
```

设置后，Web 会显示登录页，API 请求需要携带 `Authorization: Bearer op-token`。`projects` 用于限制该用户能看到和操作的项目；不配置 `projects` 时表示可访问全部项目。

也可以使用更简单的全局 Token：

```bash
VERIDIX_CONTROL_TOKEN=secret-token
VERIDIX_CONTROL_ROLE=admin
```

## 5. 创建并执行任务

### 5.1 Web 流程

1. 打开 `http://127.0.0.1:5173`；
2. 进入“新建任务”；
3. 创建 Project，填写 Target URL；
4. 创建 Mission，输入自然语言任务描述；
5. 选择执行模板：
   - `scanner_verify`
   - `redteam_orchestration`
   - `code_audit`
   - `authz_matrix`
   - `ssrf_callback`
   - `graphql`
   - `websocket`
6. 选择 Loop Preset（可选）；
7. 点击启动 Run；
8. 在“运行控制台”观察事件、工具调用、上下文投影、图指标；
9. 在“证据与发现”审批、验证、查看攻击图；
10. 在“报告”下载 Markdown/HTML/Bundle。

### 5.2 CLI 流程

```bash
npm run cli -- project lab
npm run cli -- target <project-id> --url https://target.example
npm run cli -- mission <project-id> web --template scanner_verify
npm run cli -- run start <mission-id>
npm run cli -- run attach <run-id>
```

### 5.3 TUI 流程

```bash
npm run tui
```

常用快捷键：

- `j/k` 选择 Run；
- `Enter` 打开 Run；
- `e` 活动、`G` 图、`M` 记忆、`f` 发现、`d` 审批；
- `p/r/c` 暂停、恢复、取消；
- `/` 进入斜杠命令；
- `Tab` 补全命令。

## 6. 三端资源管理

### 6.1 Skills

技能是 `SKILL.md` 包：

```text
skills/builtin/<skill-name>/
  SKILL.md
  references/
  scripts/
```

Web/TUI/CLI 可以：

- 查看技能完整定义；
- 注册自定义技能；
- 删除自定义技能；
- 通过 `skill.read` 让 Agent 读取技能包资源。

CLI：

```bash
npm run cli -- skills
npm run cli -- skills-register my-skill --name "My Skill" --trigger web_test
npm run cli -- skills-delete my-skill
```

### 6.2 MCP

内置 MCP：

- `veridix-local`：仓库内置工具目录与状态；
- `fetch`：本地 `mcp-fetch-server`，抓取 URL；
- `filesystem`：受控文件系统；
- `sequential-thinking`、`playwright`、`memory`、`github`、`brave-search` 等预设。

CLI：

```bash
npm run cli -- mcp list
npm run cli -- mcp register my-server --name "My Server" --kind local --command "python -m my_mcp"
npm run cli -- mcp test my-server
```

### 6.3 知识库

支持：

- 手动新增知识块；
- 导入 Markdown、TXT、PDF、DOCX；
- 目录导入；
- 语义/词法检索；
- 知识图谱可视化；
- 审计日志。

CLI：

```bash
npm run cli -- knowledge list
npm run cli -- knowledge add --chunk-id c1 --source-ref docs/x --content "hello"
npm run cli -- knowledge delete c1
```

### 6.4 项目记忆

CLI：

```bash
npm run cli -- memory list
npm run cli -- memory record --subject host --predicate port --value 8080
npm run cli -- memory fix --fact-id <id> --value corrected
npm run cli -- memory forget --fact-id <id>
npm run cli -- memory clear
```

### 6.5 资产与漏洞

```bash
npm run cli -- assets list --project-id project_x
npm run cli -- assets add --project-id project_x --value https://target.example --kind url
npm run cli -- assets update asset_y --status verified
npm run cli -- vulns list --project-id project_x --severity high
npm run cli -- risk --project-id project_x
```

## 7. 真实工具链

默认安装使用 `fake` runner，适合演示与流程验证。真实扫描需要：

```bash
export VERIDIX_RUNNER=docker
export VERIDIX_TOOL_NETWORK=veridix-system_veridix-net
npm run up
```

Windows：

```powershell
$env:VERIDIX_RUNNER="docker"
$env:VERIDIX_TOOL_NETWORK="veridix-system_veridix-net"
npm run up
```

支持的工具包：

- `network`：nmap、masscan、naabu、subfinder、httpx、dnsrecon 等；
- `web`：ffuf、gobuster、dirsearch、whatweb、wpscan、sqlmap、nikto、dirb、wfuzz；
- `vulnscan`：nuclei、fscan、内置 OWASP/DOM XSS/File Upload/LFI 验证器；
- `host`：hydra、enum4linux、smbmap、snmp、ssh probe；
- `code`：semgrep、secret scan；
- `cloud/ad`：云与 AD 后渗透技能。

ZAP 作为统一 DAST 常驻服务：

```text
VERIDIX_ZAP_URL=http://127.0.0.1:8090
VERIDIX_ZAP_API_KEY=veridix-zap
```

## 8. 存储与检索配置

### 8.1 Server Profile

默认 Server Profile 使用：

- 向量：pgvector；
- 图：Neo4j；
- Embedding：OpenAI-compatible 或 Ollama；
- Rerank：可选。

也可以在 Web `诊断与设置 -> 检索与存储` 中一键填入成熟默认：

```text
embedding:   http://127.0.0.1:11434/v1 + nomic-embed-text
vector:      qdrant http://127.0.0.1:6333
graph:       neo4j bolt://127.0.0.1:7687
rerank:      fastembed + BAAI/bge-reranker-base
fusion:      rrf
```

### 8.2 环境变量

常用：

```bash
VERIDIX_STORAGE_PROFILE=server
VERIDIX_PGVECTOR_URL=postgresql://veridix:veridix@127.0.0.1:55432/veridix
VERIDIX_QDRANT_URL=http://127.0.0.1:6333
VERIDIX_CHROMA_URL=http://127.0.0.1:8001
VERIDIX_NEO4J_URI=bolt://127.0.0.1:7687
VERIDIX_EMBEDDING_ENDPOINT=http://127.0.0.1:11434/v1
VERIDIX_EMBEDDING_MODEL=nomic-embed-text
VERIDIX_RERANK_ENABLED=1
```

## 9. 报告与交付

CLI：

```bash
npm run cli -- report run_abc --format markdown --out reports/
npm run cli -- report run_abc --format html --out reports/
npm run cli -- report run_abc --format bundle --out reports/
```

Web：

- `证据与发现` 可下载 report bundle；
- `报告` 可预览、下载 Markdown、HTML、Bundle；
- `漏洞与风险` 展示 CVSS、严重度、资产关联与处置状态。

## 10. 验收与自检

### 10.1 CLI

```bash
npm run cli -- doctor
npm run cli -- self-test
npm run cli -- health
```

### 10.2 一键验收

```bash
python scripts/acceptance_gate.py
```

包含单元、前端、三端、TUI、压力、checkpoint、storage/tool smoke 等本地验收。

真实模型 + Docker 门禁：

```bash
export DEEPSEEK_API_KEY="..."
python scripts/acceptance_gate.py --real
```

### 10.3 发布前检查

```bash
python scripts/release_gate.py --dry-run
```

## 11. 常见问题

### Provider 测试 degraded

- 检查 `api_key_ref` 是否指向已设置的环境变量；
- 检查模型名与 endpoint；
- 只支持 chat 的供应商会在 capability 中显示 `embeddings: false`，这是正常状态；
- 使用 `npm run cli -- provider <endpoint> <model> --api-key-ref env:NAME --json` 查看详细能力。

### 攻击图空白

- 先选择 Run；
- 没有 findings 时显示空状态；
- 若页面完全白屏，刷新并检查浏览器 console；
- 旧版本存在 React Hooks 顺序问题，升级后已修复。

### MCP 测试失败

- `veridix-local` 不需要外部依赖；
- `fetch` 使用本地 `mcp-fetch-server`，运行 `npm install` 即可；
- 其他 MCP 需要先安装对应 npm/Python 包；
- 失败结果会显示建议。

### 工具找不到

- 确认 Docker 已启动；
- 运行 `python scripts/tool_env_up.py`；
- 确认 `VERIDIX_TOOL_NETWORK` 与 Compose 网络一致；
- 查看 Web `诊断与设置 -> 工具` 中每个 Pack 的状态。

### 知识库导入失败

- 支持 `.md/.markdown/.txt/.pdf/.docx`；
- PDF 需要 `pypdf`；
- 文件上传接口需要 `python-multipart`；
- 导入后可在“来源”与审计日志中确认。

### Web 断线恢复

- Web 依赖 Control Plane；
- 停止/重启 Control Plane 时保留同一个数据库即可恢复；
- 长任务在 worker 崩溃后由新 worker 继续。

## 12. 安全提醒

- 仅对你有权测试的目标使用；
- 明确记录授权范围，必要时通过目标 Profile 配置 allowed/excluded；
- 高危工具会触发审批，不要在生产环境随意绕过；
- API Key 不应写入代码仓库；
- 涉及真实生产系统时，请先与资产所有者确认书面授权。
