# CodeXXX 技术栈

## 前端

- React
- TypeScript
- Vite
- 原生 CSS

## 后端

- Python 3.12+
- FastAPI
- Uvicorn
- Pydantic
- python-dotenv
- SQLite（Python `sqlite3`，schema v5）：账号、会话、组织、组织成员、项目、任务、队列、补丁、验证、用量和审计持久化
- PBKDF2-HMAC-SHA256：带随机盐的密码哈希；数据库不保存明文密码
- 256 位随机不透明会话令牌：客户端仅收到原始令牌，数据库只保存 SHA-256 摘要
- Python 标准库 `difflib`、`hashlib`、`json`、`ast`：阶段 5 Diff、版本标识和无执行语法检查
- 版本化 SQLite 工作流存储：AgentTask、TaskFile、ToolCall、Patch、ValidationRun、TaskQueue 和 TaskIdempotency
- SQLite 可靠任务队列：持久化领取、租约、心跳、指数退避重试、取消、幂等和服务启动恢复
- 有界 `ThreadPoolExecutor`：执行已取得有效队列租约的 Agent 任务和有超时的验证器
- Docker 隔离沙箱：断网、只读根文件系统、临时工作区、非 root 和资源限制；不可用时失败封闭
- 后台数据保留服务：按配置周期清理会话、登录尝试、终态任务、审计和用量记录

## 模型调用

后端通过 OpenAI 兼容格式调用模型服务。服务地址、模型名称、超时和上下文限制都从环境变量读取；API Key 不会暴露给浏览器。

## 质量保障

- pytest：后端自动化测试
- Ruff：Python 静态检查
- ESLint：前端静态检查
- Vitest：前端基础功能稳定后再加入

## Agent 工作流

- Pydantic：任务、计划、工具注册、轨迹和状态响应契约
- 固定只读工具注册表：显式参数校验、权限、超时与输出上限
- SQLite 任务快照：计划、转换、工具轨迹和安全文件内容可在服务重启后恢复
- 重启恢复规则：队列根据持久化任务状态和过期租约决定重试、取消、失败或完成，失效 Worker 不能提交结果
- `Idempotency-Key`：任务创建和确认支持请求去重，同一 Key 不得用于不同请求
- 有界 Worker 并发：后台领取已确认任务，前端可以轮询、取消和恢复

## 补丁与验证

- Pydantic 严格 JSON：模型补丁输出和用户补丁操作拒绝额外字段
- 统一 Diff：逐文件展示完整内容替换产生的可审阅差异
- SHA-256：标识补丁基线与目标版本，应用和撤销前检查冲突
- 后端持久化工作区：只应用到 SQLite 任务快照，浏览器下载当前版本，不直接覆盖本地原文件
- 固定内置验证器：补丁完整性、版本冲突、行尾空白、JSON 语法和 Python AST 语法
- 固定沙箱命令：pytest、Ruff、Mypy、npm test 和 npm build；必须显式确认执行
- 沙箱通过容器运行时 CLI 边界运行，限制 CPU、内存、PID、磁盘、时间和输出，默认关闭网络
- 无容器运行时或基础镜像时返回 503，不降级到宿主机执行

## 认证与权限

- FastAPI Bearer 认证依赖：后端统一验证令牌，不依赖前端隐藏按钮
- `viewer`、`editor`、`admin`、`owner` 项目角色和显式权限矩阵
- 会话过期、主动退出和修改密码后的全会话撤销
- SQLite 持久化登录失败窗口，实现邮箱与客户端地址组合限流
- 版本化数据库迁移和外键约束；结构保持可迁移到 PostgreSQL
- 项目任务按 `viewer`、`editor`、`admin` 和 `owner` 后端权限隔离
- React 前端已接入注册、登录、项目切换、成员管理、账号删除和权限状态；会话令牌仅在当前标签页 `sessionStorage` 保存
- 注册表单在前端校验八位密码、二次确认和显示/隐藏状态；补丁历史使用稳定创建顺序，并通过序号、时间和色彩边界区分卡片；主要字号采用可读的紧凑缩放层
- 本地匿名兼容工作流由 `ALLOW_LEGACY_LOCAL_WORKFLOWS` 显式控制，服务化部署必须关闭
- SQLite `usage_records`：持久化用户、项目和匿名本地作用域的滚动窗口用量
- `BEGIN IMMEDIATE`：原子检查活动任务、文件额度并保存新任务，避免并发穿透
- 标准 `HTTP 429`、`Retry-After` 和 `X-RateLimit-*`：返回明确的限额及恢复时间
- 数据保留策略通过 `GET /api/retention` 可见，并由后台线程按周期执行清理

## 下一阶段边界

- 阶段 0 至阶段 6 已完成，阶段 7 组织后端和前端团队协作功能块已完成
- 阶段 7 已完成：`Organization`、组织成员/角色、组织内项目边界、组织切换、组织成员管理和组织项目视图
- README 已提供 GitHub 自部署、跨平台启动、配置和迁移说明；`scripts/` 提供本地 Agent 轨迹导出与基础评测
- 当前可选增强：补充更多固定评测样本，以及按本地自部署需要提供 Dockerfile/Compose
- 在线部署、PostgreSQL、对象存储、管理后台和完整在线评测平台属于后续扩展
- Dockerfile/Compose 仅作为可选本地部署增强，不作为在线生产系统承诺
- 不提前引入多 Agent、Tree-sitter 或向量 RAG
