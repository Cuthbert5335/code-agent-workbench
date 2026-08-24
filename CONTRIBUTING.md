# Contributing to CodeXXX

感谢你对 CodeXXX 的关注。CodeXXX 是一个本地优先、可自部署、面向 Agent 学习和代码排查的开源项目。

## 开始之前

请先阅读：

- [README.md](README.md)：安装、配置和运行方式。
- [docs/requirements.md](docs/requirements.md)：产品范围和安全边界。
- [docs/tech-stack.md](docs/tech-stack.md)：技术实现和阶段边界。

内部开发交接文件不属于公开发布材料，也不会作为公共 API 或产品契约。

## 本地验证

后端：

```bash
cd backend
python -m venv .venv
python -m pip install -r requirements.txt
python -m pytest -q
python -m compileall -q app tests
python -m ruff check app tests ../scripts
python -m pip check
```

前端：

```bash
cd frontend
npm ci
npm run lint
npm run build
```

## 提交规范

- 保持修改范围聚焦，避免把在线 SaaS、任意 Shell、宿主机代码执行或未规划的基础设施带入本地版本。
- 新增 API 时同步更新 Pydantic schema、权限检查、审计记录和测试。
- 涉及 SQLite 表结构时新增版本化迁移，并测试从旧版本迁移到当前版本。
- 补丁只能更新后端 SQLite 任务快照，不直接覆盖用户本地文件。
- 沙箱只能使用固定命令，必须显式确认；运行时不可用时必须失败封闭。
- 不提交 `.env`、模型密钥、数据库、任务轨迹、评测报告或其他本地数据。

提交 Pull Request 前，请在描述中说明变更、测试命令和已知限制。
