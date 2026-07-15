# Oplab

Oplab 是一个以证据、决策和可恢复科研流程为中心的“一人课题组”工作台。它不是让几个角色提示词轮流聊天，而是把课题、来源、段落、论断、反证、组会和产物保存为可审计的领域对象。

当前版本实现阶段 A 的完整垂直切片：

1. 创建课题和 Research Charter；
2. 从 OpenAlex 自动检索学术来源，也可上传本地 PDF、Markdown 或文本；
3. `PI → Librarian → Skeptic → Writer` LangGraph 工作流建立证据台账与反证；
4. 在结构化用户组会前持久化并暂停；
5. 用户作出继续、修订或停止决策后恢复；
6. 生成每条引用均可回溯到 `Claim → Passage → Source` 的研究备忘录；
7. API 进程中断后从持久化 checkpoint 恢复未完成运行。

## 本地运行

无需模型密钥即可运行确定性的演示路径；配置 OpenAI 兼容端点后会启用结构化模型综合。

```powershell
Copy-Item .env.example .env
uv sync --dev --cache-dir .uv-cache
pnpm.cmd install
docker compose up --build
```

打开 `http://localhost:3000`，API 文档位于 `http://localhost:8000/docs`。

仅使用 SQLite 开发时：

```powershell
./scripts/dev.ps1
```

## 状态边界

| 状态 | 权威来源 |
| --- | --- |
| Project、Source、Claim、Meeting、Decision、Artifact | PostgreSQL（本地可用 SQLite）领域模型与事件日志 |
| 单次研究运行的节点 checkpoint | 独立 LangGraph checkpoint store |
| PDF、报告和上传文件 | artifact/upload store |
| 跨天调度、等待外部算力 | 阶段 B 的 Temporal adapter；阶段 A 不伪装实现 |

领域写入必须经过类型化命令与领域服务。Agent 节点不持有任意数据库写权限。

## 开发验证

```powershell
uv --cache-dir .uv-cache run ruff check .
uv --cache-dir .uv-cache run pytest
pnpm.cmd typecheck
pnpm.cmd build
```

数据库迁移使用独立的 Alembic revision：

```powershell
uv --cache-dir .uv-cache run alembic upgrade head
```

架构决策和阶段边界见 [docs/architecture.md](docs/architecture.md)。

## License

Copyright (C) 2026 Oplab contributors. Licensed under `AGPL-3.0-or-later`.
