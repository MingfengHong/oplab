# Oplab

Oplab 是一个以证据、决策和可恢复科研流程为中心的“一人课题组”工作台。它不是让几个角色提示词轮流聊天，而是把课题、来源、段落、论断、反证、组会和产物保存为可审计的领域对象。

当前版本实现可运行的动态 Harness 闭环：

1. 创建课题并生成依赖感知的动态研究计划；
2. 从 OpenAlex 自动检索学术来源，也可上传本地 PDF、Markdown 或文本；
3. 控制器根据实时证据缺口，从类型化工具注册表中选择检索、论断抽取、反证比较或审查；
4. 每次决策、工具结果和评估都写入 trajectory 与领域事件；
5. 预算、schema 重试、重复动作熔断和证据门禁约束模型行为；
6. 在结构化用户组会前持久化并暂停，用户决定后恢复同一运行；
7. 独立 Reviewer 通过引用和证据门禁后，才发布可回溯到 `Claim → Passage → Source` 的备忘录；
8. API 进程中断后从持久化 checkpoint 恢复未完成运行。

## 本地运行

无需模型密钥即可运行确定性策略路径；配置 DeepSeek 等 OpenAI 兼容端点后，会启用类型化规划、动态决策、论断抽取、反证判断、综合和审稿。

```powershell
Copy-Item .env.example .env
uv sync --dev --cache-dir .uv-cache
pnpm.cmd install
docker compose up --build
```

打开 `http://localhost:3000`，API 文档位于 `http://localhost:8000/docs`。浏览器访问 API 使用 Next.js 同源代理，因此不会依赖浏览器端跨端口 CORS。

仅使用 SQLite 开发时：

```powershell
./scripts/dev.ps1
```

## 状态边界

| 状态 | 权威来源 |
| --- | --- |
| Project、Source、Claim、Meeting、Decision、Artifact | PostgreSQL（本地可用 SQLite）领域模型与事件日志 |
| 单次运行的 Harness 状态与节点 checkpoint | 独立 LangGraph checkpoint store；领域库保留关键轨迹事件 |
| PDF、报告和上传文件 | artifact/upload store |
| 跨天调度、等待外部算力 | 阶段 B 的 Temporal adapter；阶段 A 不伪装实现 |

领域写入必须经过类型化命令与领域服务。模型只能选择已注册工具，不持有任意数据库、文件或网络写权限。

## 开发验证

```powershell
uv --cache-dir .uv-cache run ruff check .
uv --cache-dir .uv-cache run pytest
pnpm.cmd typecheck
pnpm.cmd build
```

使用当前 `.env` 中的模型和 OpenAlex，在临时数据库中执行完整闭环烟雾测试：

```powershell
python -m dotenv -f .env run --override -- .venv\Scripts\python.exe scripts\smoke_harness.py
```

数据库迁移使用独立的 Alembic revision：

```powershell
uv --cache-dir .uv-cache run alembic upgrade head
```

架构决策和阶段边界见 [docs/architecture.md](docs/architecture.md)。

## License

Copyright (C) 2026 Oplab contributors. Licensed under `AGPL-3.0-or-later`.
