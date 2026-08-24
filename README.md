# CodeXXX

CodeXXX 是一个开源、本地优先、可自部署的代码分析与修改 Agent。它通过网页提供代码理解、问题定位、上下文检索、计划执行、补丁审阅和验证能力，适合个人开发者、初学者、团队开发者以及希望学习 Agent 工程的开发者。

项目的首要目标不是建设一个必须在线使用的 SaaS，而是让任何人都能把代码拉到自己的电脑上运行、阅读和排查。代码、SQLite 数据库和模型密钥默认保留在本机；只有用户明确选择的上下文才会发送给配置的模型服务商。

当前状态：阶段 0 至阶段 6 已完成，阶段 7 的组织后端与前端组织视图已完成。当前后端包含账号认证、组织与组织成员、项目权限、SQLite 工作流持久化、用量和并发限制、可靠任务队列、容器沙箱、数据保留与账号删除；前端已接入组织切换、组织成员管理、组织项目视图、账号、项目和项目成员。仓库同时提供本地 Agent 学习评测脚本，在线部署与管理后台属于后续扩展。

## 项目定位和使用方式

CodeXXX 面向两类主要场景：

- 个人本地使用：在自己的电脑上分析代码、生成补丁、查看任务轨迹并下载修改后的快照。
- 学习和协作：通过源码、测试、文档和可选组织能力，理解上下文构建、Tool Calling、状态机、权限、队列、补丁和沙箱设计。

推荐先使用本地模式。服务模式和组织能力已经具备后端基础，但不代表当前项目已经是经过生产加固的在线 SaaS。

## 公共项目文档

- [产品需求与范围](docs/requirements.md)
- [技术栈与架构边界](docs/tech-stack.md)
- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)

本地开发交接记录不会随公开仓库发布。

## 快速开始

### 环境要求

- Python 3.12 或更高版本。
- Node.js 20 或更高版本，以及 npm。
- 一个 OpenAI 兼容模型服务（可选）；未配置模型时可以使用演示模式。
- Docker/Podman 不是运行基础工作台的必需依赖；只有执行固定沙箱验证时才需要容器运行时。

### Windows PowerShell

```powershell
git clone <你的 GitHub 仓库地址>
cd CodeXXX

cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

另开一个终端启动前端：

```powershell
cd CodeXXX\frontend
npm.cmd install
Copy-Item .env.example .env
npm.cmd run dev
```

然后访问 <http://127.0.0.1:5173>。首次使用可以不配置模型，先验证界面、项目、任务和补丁流程；需要真实模型时，只在 `backend/.env` 中填写服务端配置。

### macOS / Linux

```bash
git clone <你的 GitHub 仓库地址>
cd CodeXXX/backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

另开一个终端：

```bash
cd CodeXXX/frontend
npm install
cp .env.example .env
npm run dev
```

### 配置和数据

- `backend/.env.example` 是安全模板，不包含任何真实密钥。
- `backend/.env` 只由后端读取，严禁提交到 GitHub。
- SQLite 数据默认写入 `backend/data/codexxx.db`，该目录已被 Git 忽略。
- 数据库启动时会自动执行版本化迁移；当前 schema version 为 5。
- 如果本机没有 Docker/Podman，固定沙箱验证会失败封闭并返回不可用状态，不会降级到宿主机执行。

### 运行测试

后端和前端的检查命令见“运行测试”章节。提交 Pull Request 前，建议至少运行后端 pytest、Ruff、前端 lint 和生产构建。

## Agent 学习评测

仓库提供一个不联网、不调用模型、不执行用户代码的本地评测脚本。它读取固定问题集和导出的任务轨迹，计算回答关键词、引用完整度/精确率、工具成功率、补丁应用、验证通过、平均耗时、取消率和失败率。可以先从 SQLite 导出不含文件内容、补丁正文、会话和模型配置的轨迹：

```powershell
python scripts/export_trajectories.py `
  --database backend/data/codexxx.db `
  --output agent-trajectories.json
```

```powershell
python scripts/evaluate_agent.py `
  --dataset examples/evaluation_set.json `
  --runs path/to/exported-runs.json `
  --output evaluation-report.json
```

轨迹可以是 JSON 数组或 JSONL；每条轨迹使用评测集的 `id` 作为 `evaluation_id`。脚本只用于学习和回归比较，不是在线评测平台，也不会上传代码或密钥。

当前文件选择支持：

- 同时选择一个或多个本地代码文件。
- 选择本地项目目录并显示可展开的目录树。
- 保留目录内相对路径，用于上下文和回答引用。
- 在已选代码文件中按字面量搜索，显示文件、行号、列号和少量上下文。
- 按名称筛选或列出基础函数、类、接口、类型等符号，显示相对路径、语言、声明行号和简短声明。
- 手动建立或重新建立项目索引，并展示未开始、处理中、已完成、部分完成和失败状态。
- 查看索引的文件、字符、切片和符号统计，以及逐文件明细和处理提示。
- 展示文件名、识别到的语言类型和文件大小。
- 跳过重复文件，并支持单个移除或全部清空。
- 拒绝常见密钥文件和不支持的文件类型。
- 限制最多 50 个文件、单文件 1 MB、文件总量 20 MB。

这些前端校验用于尽早向用户反馈；后续分析接口仍会在后端执行同样或更严格的校验，不能只依赖浏览器。

## 项目理解接口

以下接口均使用 `multipart/form-data`，文件字段名为重复的 `files`。它们只处理用户当前明确选择且通过安全校验的 UTF-8 文件，不执行代码、不修改或持久化文件：

- `POST /api/search`：通过必填的 `query` 字段进行忽略大小写的文本字面量搜索。
- `POST /api/chunks`：返回确定性、有行号边界和输出上限的代码切片。
- `POST /api/symbols`：通过可选的 `query` 字段筛选基础代码符号；留空时列出全部已识别符号。
- `POST /api/index`：建立请求内索引快照，返回每个文件及总体的字符、行数、切片和符号统计。

基础符号识别当前采用可解释的正则规则，适合常见声明检索，但不等同于完整语法树或引用关系分析；Tree-sitter 和向量检索不在阶段 3 范围内。

## Agent 工作流

消息输入默认使用“Agent 计划”模式，也可以切换回已有的“直接分析”模式。Agent 流程为：

```text
创建任务 -> 生成计划 -> 等待用户确认 -> 执行只读工具
        -> 审阅结果 -> 安全校验 -> 完成
```

后端接口：

- `GET /api/tools`：列出固定的只读工具注册表。
- `GET /api/tasks`：列出当前账号有权读取的项目任务；本地兼容模式只列出匿名本地任务。
- `POST /api/tasks`：提交 `goal`、重复的 `files` 和项目模式必填的 `project_id`，创建计划模式任务。
- `GET /api/tasks/{task_id}`：读取计划、状态、轨迹和结果。
- `POST /api/tasks/{task_id}/confirm`：确认并启动后台只读执行。
- `POST /api/tasks/{task_id}/cancel`：取消等待或正在执行的任务。
- `POST /api/tasks/{task_id}/resume`：把取消、失败、超时或阻塞任务恢复到待确认状态。

阶段 4 注册的工具只有：列出项目文件、生成项目摘要、搜索文本、搜索基础符号和检查代码切片。所有工具均有参数校验、超时和输出上限，不执行 Shell、不运行用户代码、不安装依赖、不修改文件。

任务、计划、状态转换、工具调用和安全文件快照已保存到 SQLite。用户确认后，任务通过 SQLite 可靠队列执行；队列提供持久化领取、租约、心跳、指数退避重试、取消、幂等键和自动调度。Worker 只有持有有效租约时才能提交结果，服务重启后会恢复过期租约并按任务状态安全重试、取消或终结，避免失效 Worker 重复写入。

## 结构化补丁与验证

已完成的阶段 5 流程为：

```text
已完成 Agent 任务 -> 生成严格 JSON 补丁草稿 -> 逐文件查看 Diff
                  -> 接受或拒绝文件 -> 二次确认应用
                  -> 内置验证 -> 可撤销或下载当前快照文件
```

后端补丁接口包括：

- `GET /api/validators`
- `GET /api/tasks/{task_id}/patches`
- `POST /api/tasks/{task_id}/patches`
- `POST /api/tasks/{task_id}/patches/generate`
- `GET /api/patches/{patch_id}`
- `POST /api/patches/{patch_id}/review`
- `POST /api/patches/{patch_id}/reject`
- `POST /api/patches/{patch_id}/apply`
- `POST /api/patches/{patch_id}/revert`
- `POST /api/patches/{patch_id}/validate`
- `GET /api/patches/{patch_id}/files/{file_path}/download`

重要边界：浏览器不能安全地直接覆盖用户本地项目文件，因此“应用补丁”只更新后端 SQLite 中的任务快照，不覆盖用户磁盘上的原文件。应用或撤销后，用户可以下载对应文件并自行替换；任务、补丁、快照、验证和事件在服务重启后仍可恢复。补丁只能修改任务中已存在、通过安全校验并被完整放入有界模型上下文的文件，不支持截断文件后生成覆盖内容，也不支持新增、删除或重命名。

当前固定内置验证器为 `patch_integrity`、`conflict_check`、`whitespace`、`json_syntax` 和 `python_syntax`。它们只使用标准库检查 Diff、哈希、空白、JSON 和 Python AST，不运行用户代码或命令；pytest、静态检查和构建只能通过后文的固定沙箱验证器运行。

## 账号、项目与权限

阶段 6 第一部分提供以下后端接口：

- `POST /api/auth/register`、`POST /api/auth/login`、`POST /api/auth/logout`
- `GET /api/auth/me`、`POST /api/auth/change-password`
- `DELETE /api/auth/account`
- `GET/POST /api/projects`
- `GET/PATCH/DELETE /api/projects/{project_id}`
- `GET/POST /api/projects/{project_id}/members`
- `PATCH/DELETE /api/projects/{project_id}/members/{user_id}`
- `GET /api/audit`、`GET /api/projects/{project_id}/audit`

阶段 7 后端还提供组织接口：

- `GET/POST /api/organizations`
- `GET/PATCH /api/organizations/{organization_id}`
- `GET/POST /api/organizations/{organization_id}/members`
- `PATCH/DELETE /api/organizations/{organization_id}/members/{user_id}`

组织角色包括 `owner`、`admin` 和 `member`。组织成员默认可以看到组织内项目，但显式项目成员角色仍然决定具体的项目读写权限；任务历史、补丁、验证和审计沿用同一项目隔离边界。

密码使用带随机盐的 PBKDF2-HMAC-SHA256 保存，数据库不会出现明文密码。登录返回的随机 Bearer Token 只向客户端展示一次，SQLite 只保存其 SHA-256 摘要；令牌有过期时间，退出或修改密码后立即失效。登录失败采用持久化时间窗口限流。

项目角色为 `viewer`、`editor`、`admin` 和 `owner`。每个项目请求均由后端计算读取、写入、应用补丁、成员管理和删除权限。删除项目必须提交 `confirm: true` 和完全匹配的项目名称。

本地数据库默认位于 `backend/data/codexxx.db`，目录已加入 `.gitignore`。账号、会话、项目、成员、审计、任务、工具调用、补丁、验证、文件快照和队列状态都可以在后端重启后恢复。项目删除会通过外键级联清理所属工作流记录；账号删除需要邮箱和当前密码二次确认，并删除该账号、会话、拥有的项目及关联数据。

项目任务必须使用 Bearer Token，并由后端按 `read`、`write`、`apply_patch`、`manage` 和 `delete` 权限检查。前端已经接入注册、登录、退出、项目切换、项目设置、成员角色和账号删除，并通过当前浏览器标签页的 `sessionStorage` 恢复会话令牌及活动项目。后端仍保留显式可控的匿名本地兼容工作流；它只适合监听 `127.0.0.1` 的本地开发，服务化或局域网部署必须设置 `ALLOW_LEGACY_LOCAL_WORKFLOWS=false`。

注册时会提示密码长度不低于八位，并要求再次输入密码确认；密码输入和账号删除密码支持显示/隐藏。两次注册密码不一致时，页面提示“**两次输入的密码不同，请重新输入！**”并不会提交请求。

补丁生成成功后会显示“补丁已生成”。补丁历史按创建顺序展示，新补丁追加到旧补丁之后；卡片带有序号、生成时间和不同颜色边界，便于区分多次生成的结果。工作区主要文字和辅助信息使用较大的可读字号，同时保持紧凑排版。

## 用量和并发限制

SQLite schema v3 新增持久化 `usage_records`。系统按滚动时间窗同时检查用户和项目的模型调用、已接收文件、补丁创建和验证运行额度；匿名本地模式使用独立作用域。任务创建和恢复还会检查每用户、每项目活动任务上限，任务结束或取消后立即释放活动名额。Schema v4 新增 `task_queue` 和 `task_idempotency`，schema v5 新增组织、组织成员、项目组织归属和审计组织字段。

超限统一返回 `HTTP 429`。响应体包含 `resource`、`scope`、`limit`、`used`、`requested`、`retry_after_seconds` 和 `retry_at`，响应头包含 `Retry-After` 及 `X-RateLimit-*`。认证用户可通过 `GET /api/usage` 查看账号用量，并用 `GET /api/usage?project_id=<id>` 同时查看有权读取的项目用量。

## 隔离沙箱与验证

`GET /api/sandbox/status` 返回容器运行时、基础镜像和命令允许列表的可用状态。沙箱验证只允许 `sandbox_pytest`、`sandbox_ruff`、`sandbox_mypy`、`sandbox_npm_test` 和 `sandbox_npm_build`，并要求请求显式提交 `confirm_execution: true`。

命令在临时 Docker 容器中以非 root 用户运行，网络关闭、根文件系统只读，并限制 CPU、内存、PID、临时磁盘、执行时间和输出长度。沙箱不可用时接口返回 `HTTP 503`，不会降级到宿主机执行。当前开发机未安装 Docker、Podman 或 WSL，因此失败封闭和 API 行为已验证，但真实容器执行尚未在本机验证。

## 数据保留

后台清理任务默认每小时运行。过期或已撤销会话和登录尝试默认保留 7 天；终态任务及其级联补丁、验证和队列数据保留 90 天；审计日志保留 365 天；用量记录保留 30 天，且不会短于配置的用量统计窗口。认证用户可通过 `GET /api/retention` 查看当前策略。

## 目录结构

```text
CodeXXX/
├─ backend/              # FastAPI 后端
│  ├─ app/               # 应用代码
│  ├─ tests/             # 后端测试
│  ├─ requirements.txt
│  └─ .env.example
├─ frontend/             # React + TypeScript + Vite 前端
├─ docs/                 # 公开需求和技术栈说明
└─ examples/             # 固定评测问题集和示例资源
```

## 启动后端

建议使用 Python 3.12 或更高版本。在 PowerShell 中执行：

```powershell
cd CodeXXX/backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

启动后可访问：

- 健康检查：[http://localhost:8000/api/health](http://localhost:8000/api/health)
- 接口文档：[http://localhost:8000/docs](http://localhost:8000/docs)

健康检查应返回类似下面的 JSON：

```json
{
  "status": "ok",
  "service": "CodeXXX API",
  "version": "0.1.0"
}
```

## 启动前端

另开一个 PowerShell 窗口执行：

```powershell
cd CodeXXX/frontend
npm.cmd install
Copy-Item .env.example .env
npm.cmd run dev
```

这里使用 `npm.cmd` 是为了避开部分 Windows PowerShell 环境对 `npm.ps1` 的脚本签名限制，不需要修改系统执行策略。

启动后访问 [http://127.0.0.1:5173](http://127.0.0.1:5173)。页面顶部会自动调用后端 `/api/health`，并显示“后端已连接”或清晰的离线提示。

如需检查前端代码质量：

```powershell
cd CodeXXX/frontend
npm.cmd run lint
npm.cmd run build
```

## 分析接口

后端正式分析接口为 `POST /api/analyze`，同时保留 `POST /api/analysis` 作为兼容别名。接口始终不会执行用户代码或修改文件：模型配置完整时调用 OpenAI 兼容的 Chat Completions 接口；配置不完整时继续返回诚实的演示回答。

请求使用 `multipart/form-data`：

- `question`：必填的自然语言问题。
- `files`：一个或多个代码文件。
- `conversation`：可选的 JSON 消息数组，例如 `[{"role":"user","content":"请关注异常处理"}]`。

可以在 PowerShell 中用下面的 Python 片段验证接口：

```powershell
cd CodeXXX/backend
@'
import httpx

with open('example.py', 'rb') as source_file:
    response = httpx.post(
        'http://127.0.0.1:8000/api/analyze',
        data={'question': '请解释这个函数'},
        files={'files': ('example.py', source_file, 'text/x-python')},
    )

print(response.status_code)
print(response.json())
'@ | python
```

未配置模型时，成功响应至少包含：

```json
{
  "answer": "当前请求已在演示模式下完成……",
  "references": [
    {
      "file": "example.py",
      "language": "Python",
      "start_line": 1,
      "end_line": 2,
      "truncated": false
    }
  ],
  "mode": "demo",
  "warnings": ["当前未调用真实模型……"],
  "stats": {
    "received_files": 1,
    "accepted_files": 1,
    "skipped_files": 0,
    "context_chars": 85,
    "conversation_messages": 0
  }
}
```

后端会再次校验文件路径、敏感文件、支持的扩展名、文件数量、单文件大小、总大小和 UTF-8 文本编码。前端校验不能替代后端校验。

如需启用真实模型，在 `backend/.env` 中配置：

```text
MODEL_API_KEY=你的服务端密钥
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_NAME=你的模型名称
MODEL_TIMEOUT_SECONDS=60
MAX_FILE_SIZE_BYTES=1048576
MAX_TOTAL_FILE_SIZE_BYTES=20971520
MAX_FILE_COUNT=50
MAX_CONTEXT_CHARS=60000
DATABASE_PATH=data/codexxx.db
SESSION_TTL_SECONDS=86400
LOGIN_MAX_FAILURES=5
LOGIN_FAILURE_WINDOW_SECONDS=300
ALLOW_LEGACY_LOCAL_WORKFLOWS=true
USAGE_WINDOW_SECONDS=86400
MAX_ACTIVE_TASKS_PER_USER=3
MAX_ACTIVE_TASKS_PER_PROJECT=5
MAX_MODEL_CALLS_PER_USER_WINDOW=100
MAX_MODEL_CALLS_PER_PROJECT_WINDOW=200
MAX_FILES_PER_USER_WINDOW=1000
MAX_FILES_PER_PROJECT_WINDOW=2000
MAX_PATCHES_PER_USER_WINDOW=50
MAX_PATCHES_PER_PROJECT_WINDOW=100
MAX_VALIDATIONS_PER_USER_WINDOW=100
MAX_VALIDATIONS_PER_PROJECT_WINDOW=200
TASK_QUEUE_WORKER_CONCURRENCY=2
TASK_QUEUE_LEASE_SECONDS=15
TASK_QUEUE_HEARTBEAT_SECONDS=2
TASK_QUEUE_POLL_SECONDS=0.1
TASK_QUEUE_MAX_ATTEMPTS=3
TASK_QUEUE_RETRY_BASE_SECONDS=0.25
SANDBOX_RUNTIME=docker
SANDBOX_PYTHON_IMAGE=codexxx-sandbox-python:3.13
SANDBOX_NODE_IMAGE=codexxx-sandbox-node:22
SANDBOX_CPU_LIMIT=1
SANDBOX_MEMORY_MB=512
SANDBOX_DISK_MB=128
SANDBOX_PIDS_LIMIT=128
SANDBOX_TIMEOUT_SECONDS=60
SANDBOX_MAX_OUTPUT_CHARS=12000
RETENTION_CLEANUP_INTERVAL_SECONDS=3600
EXPIRED_SESSION_RETENTION_DAYS=7
LOGIN_ATTEMPT_RETENTION_DAYS=7
TERMINAL_TASK_RETENTION_DAYS=90
AUDIT_LOG_RETENTION_DAYS=365
USAGE_RECORD_RETENTION_DAYS=30
```

`MODEL_BASE_URL` 应包含供应商的 API 版本路径（例如 `/v1`），也可以直接填写以 `/chat/completions` 结尾的完整接口地址。真实回答中的引用格式为 `[引用: 文件路径: 起始行-结束行]`；后端只会返回能够在本次上下文中验证的引用。

## 运行测试

```powershell
cd CodeXXX/backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app tests
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pip check
```

当前后端 117 项测试全部通过，除阶段 0–5 能力外，还覆盖认证与项目/组织权限、任务/补丁/验证持久化、项目隔离、可靠队列的租约/心跳/重试/取消/幂等和恢复、用户/项目用量限制、沙箱允许列表与失败封闭、数据保留清理、高风险项目/账号删除和级联清理，以及补丁重新生成后的历史顺序。compileall、Ruff、`pip check`、前端 ESLint 与 TypeScript + Vite 生产构建均通过；桌面和移动端真实 UI 流程也已验收。

阶段 7 的组织协作、自部署说明和 Agent 学习评测基线已完成。后续可选补充更多固定评测样本或本地 Dockerfile/Compose；在线部署、系统管理后台和完整评测平台暂不作为当前主线。继续保持固定沙箱命令、显式执行确认、补丁只更新 SQLite 任务快照，不提前引入多 Agent、Tree-sitter 或向量 RAG。

## 配置安全

模型密钥只由后端读取，不能放进前端代码。真实配置写入 `backend/.env`；该文件已被 `.gitignore` 忽略，不应提交到版本库。
