# Deep Research 平台开发文档（Codex 执行版）

## 0. 文档目的

本文件用于指导 Codex 在 Linux 服务器环境下，按阶段、可回滚、可验收地实现一个接近 GPT / Gemini Deep Research 能力的开源情报研究平台。

### 0.1 当前路线说明

当前项目路线已收敛为：

- 主要服务单一操作者 / 项目作者本人
- 以 host-local / self-hosted Linux 运行路径为主
- Docker / compose 仅作为可选部署包装，不再是主交付目标或主验收标准
- 当前收尾标准优先是：自用、稳定、可维护、可恢复
- 不再继续主动扩展 OpenClaw、HTML/PDF、planner / gap analyzer、复杂 verifier、复杂检索优化

本文件中出现的更大范围基础设施或交付形态，应理解为长期参考边界，而不是当前阶段必须完成的主路径。

本项目不是普通聊天应用，也不是简单的 RAG 系统。系统主语义必须是：

- 异步 research task
- 可恢复的长流程状态机
- 基于证据链的 claim 生成与校验
- 可审计的 research ledger
- Web 工作台 + 可选消息入口

---

## 1. 项目边界

### 1.1 本期必须实现

- Web 工作台可提交研究任务
- 系统可自动执行搜索、抓取、抽取、索引、生成 claim、校验 claim、输出报告
- 任务具备状态流转、失败重试、暂停 / 恢复能力
- 每条 claim 都能追溯到 citation span
- 支持基础的来源质量打分
- 支持对象存储保存快照 / 附件 / 报告
- 支持基本的可观测性（日志、metrics、healthcheck、任务事件）

### 1.2 本期不做

- 多租户强隔离
- Kubernetes
- 多节点 OpenSearch
- 复杂权限系统
- 十几个外部 connector
- 大规模 plugin marketplace
- 高级多代理协作

### 1.3 当前部署路线

- 推荐路径：host-local / self-hosted Linux 直接运行
- 推荐验收：以 Python 环境、PostgreSQL、MinIO 或 filesystem backend、OpenSearch、orchestrator 启动与 smoke path 为主
- Docker / compose：保留为可选部署包装，不作为当前成功标准

---

## 2. 总体架构

系统分四层：

1. **入口层**
   - Open WebUI：浏览器工作台
   - OpenClaw：可选的消息入口（后置）

2. **编排层**
   - orchestrator：LangGraph 状态机 + API
   - worker：异步任务执行器

3. **检索证据层**
   - SearXNG：搜索发现
   - crawler：网页抓取、正文抽取、附件发现
   - Tika：文档解析
   - OpenSearch：全文索引、chunk 检索、混合检索
   - PostgreSQL：主库 / ledger / 任务状态
   - Redis：队列、锁、缓存
   - MinIO：对象存储

4. **交付层**
   - report service：Markdown / HTML / PDF 报告生成

当前执行约束：

- 以上是总体参考架构，不代表当前阶段必须把所有层或所有可选包装都做成完整交付
- 当前主交付路径仍以单机 / 自托管 Linux 的 host-local 运行方式为准
- Docker / compose 只作为可选包装，不应成为唯一依赖

核心原则：

- 先 research kernel，后交互壳
- 先 evidence chain，后多入口
- 先 async task，后 sync chat façade
- 先审计与可恢复，后功能堆叠

---

## 3. 目录结构

```text
/opt/deepresearch/
├── .env
├── docker-compose.yml          # optional deployment packaging
├── Makefile
├── README.md
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── schema.md
│   ├── runbook.md
│   └── phases/
│       ├── phase-0.md
│       ├── phase-1.md
│       ├── phase-2.md
│       └── ...
├── infra/
│   ├── caddy/
│   │   └── Caddyfile
│   ├── opensearch/
│   │   ├── opensearch.yml
│   │   ├── security/
│   │   └── certs/
│   ├── searxng/
│   │   ├── settings.yml
│   │   └── limiter.toml
│   ├── postgres/
│   │   └── init/
│   ├── minio/
│   │   └── bootstrap/
│   └── grafana/
├── services/
│   ├── orchestrator/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── settings.py
│   │   │   ├── api/
│   │   │   ├── graph/
│   │   │   ├── repos/
│   │   │   ├── models/
│   │   │   ├── tasks/
│   │   │   ├── search/
│   │   │   ├── verify/
│   │   │   ├── reporting/
│   │   │   └── utils/
│   │   └── tests/
│   ├── crawler/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── app/
│   │   └── tests/
│   ├── reporter/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── app/
│   │   └── tests/
│   └── openclaw/
│       ├── Dockerfile
│       └── config/
├── packages/
│   ├── common/
│   ├── db/
│   ├── events/
│   └── observability/
├── migrations/
├── scripts/
├── data/
│   ├── reports/
│   ├── snapshots/
│   ├── artifacts/
│   └── logs/
└── tests/
    ├── integration/
    ├── e2e/
    └── fixtures/
```

---

## 4. 技术栈约束

### 4.1 后端

- Python 3.11
- FastAPI
- LangGraph
- SQLAlchemy 2.x
- Alembic
- Pydantic v2
- httpx
- Redis client
- OpenSearch Python client
- MinIO SDK

### 4.2 基础设施

- PostgreSQL 16
- Redis 7
- OpenSearch 2.x
- OpenSearch Dashboards 2.x
- Apache Tika Server 3.x
- SearXNG 固定版本镜像
- MinIO 固定版本镜像
- Open WebUI 固定版本镜像
- Caddy 2

说明：

- 上述基础设施清单描述的是总体能力边界
- 当前收尾阶段的主路径是 host-local / self-hosted Linux 上直接联调已实现依赖
- Docker 镜像与 compose 仅作为可选包装，不再是必须完成的主目标

### 4.3 任务执行

优先方案：

- 使用 Redis 作为 broker
- worker 进程独立
- 任务语义为 **at-least-once delivery + application-level idempotency**

要求：

- 所有外部副作用操作必须具备幂等保护
- 每个 job 有 lease / heartbeat / timeout
- pause / cancel / resume 必须通过数据库状态和 worker 协议实现

---

## 5. API 设计

### 5.1 主路径 API（必须实现）

#### POST `/api/v1/research/tasks`
创建 research task。

请求体：

```json
{
  "query": "近30天 NVIDIA 在开源模型生态上的关键发布与影响",
  "constraints": {
    "domains_allow": ["nvidia.com", "github.com", "huggingface.co"],
    "domains_deny": [],
    "max_rounds": 2,
    "max_urls": 20,
    "language": "zh-CN"
  }
}
```

返回：

```json
{
  "task_id": "uuid",
  "status": "PLANNED"
}
```

#### GET `/api/v1/research/tasks/{task_id}`
返回任务元信息、当前状态、进度。

#### GET `/api/v1/research/tasks/{task_id}/events`
返回任务事件流。

#### POST `/api/v1/research/tasks/{task_id}/pause`
暂停任务。

#### POST `/api/v1/research/tasks/{task_id}/resume`
恢复任务。

#### POST `/api/v1/research/tasks/{task_id}/cancel`
取消任务。

#### POST `/api/v1/research/tasks/{task_id}/revise`
基于用户追加约束继续研究。

#### GET `/api/v1/research/tasks/{task_id}/report`
返回最新报告元数据与下载地址。

#### GET `/api/v1/research/tasks/{task_id}/claims`
返回 claim 列表与 citation spans。

### 5.2 兼容 façade（可后做）

#### POST `/v1/chat/completions`
仅作为 Open WebUI 接入层，不承载真正研究任务主语义。

---

## 6. 状态机设计

任务状态：

- `PLANNED`
- `SEARCHING`
- `ACQUIRING`
- `PARSING`
- `INDEXING`
- `DRAFTING_CLAIMS`
- `VERIFYING`
- `RESEARCHING_MORE`
- `REPORTING`
- `COMPLETED`
- `FAILED`
- `PAUSED`
- `CANCELLED`
- `NEEDS_REVISION`

### 6.1 状态转换原则

- 每次状态转换必须写入 `task_event`
- 每个节点必须可 checkpoint
- 失败必须可区分：暂时性失败 / 永久性失败
- pause 只能在安全边界点生效
- resume 必须从最近 checkpoint 继续
- cancel 后不得再产生新副作用

### 6.2 停止条件

当满足任一条件时结束研究轮次：

- 达到 `max_rounds`
- 达到 `max_urls`
- 新增高质量独立来源不足阈值
- unresolved claims 数量为 0
- 预算耗尽

---

## 7. Research Ledger（必须实现）

最小实体：

- `research_task`
- `research_run`
- `research_plan`
- `task_event`
- `search_query`
- `candidate_url`
- `fetch_job`
- `fetch_attempt`
- `content_snapshot`
- `attachment`
- `source_document`
- `source_chunk`
- `citation_span`
- `claim`
- `claim_evidence`
- `report_artifact`
- `domain_policy`

### 7.1 关键要求

- 所有 URL 必须先 canonicalize 再入库
- 每次抓取必须记录 attempt
- 每个 snapshot 必须记录 content hash
- claim 必须指向 citation span，而不是只指向 chunk
- 报告必须可回溯到 task / run / claims

---

## 8. 数据库 Schema 要求

Codex 必须先写 Alembic migration，再写 ORM。

### 8.1 `research_task`

字段：

- id
- query
- user_id
- status
- priority
- constraints_json
- created_at
- updated_at
- started_at
- ended_at

### 8.2 `research_run`

字段：

- id
- task_id
- round_no
- current_state
- checkpoint_json
- started_at
- ended_at

### 8.3 `task_event`

字段：

- id
- task_id
- run_id
- event_type
- payload_json
- created_at

### 8.4 `search_query`

字段：

- id
- task_id
- run_id
- query_text
- provider
- round_no
- issued_at
- raw_response_json

### 8.5 `candidate_url`

字段：

- id
- task_id
- search_query_id
- original_url
- canonical_url
- domain
- title
- rank
- selected
- metadata_json

### 8.6 `fetch_job`

字段：

- id
- task_id
- candidate_url_id
- mode
- status
- scheduled_at
- lease_until
- worker_id

### 8.7 `fetch_attempt`

字段：

- id
- fetch_job_id
- attempt_no
- http_status
- error_code
- started_at
- finished_at
- trace_json

### 8.8 `content_snapshot`

字段：

- id
- fetch_attempt_id
- storage_bucket
- storage_key
- content_hash
- mime_type
- bytes
- extracted_title
- fetched_at

### 8.9 `source_document`

字段：

- id
- task_id
- canonical_url
- domain
- title
- source_type
- published_at
- fetched_at
- authority_score
- freshness_score
- originality_score
- consistency_score
- safety_score
- final_source_score

### 8.10 `source_chunk`

字段：

- id
- source_document_id
- chunk_no
- text
- token_count
- metadata_json

### 8.11 `citation_span`

字段：

- id
- source_chunk_id
- start_offset
- end_offset
- excerpt
- normalized_excerpt_hash

### 8.12 `claim`

字段：

- id
- task_id
- statement
- claim_type
- confidence
- verification_status
- notes_json

### 8.13 `claim_evidence`

字段：

- id
- claim_id
- citation_span_id
- relation_type
- score

### 8.14 `report_artifact`

字段：

- id
- task_id
- version
- storage_bucket
- storage_key
- format
- created_at

---

## 9. 来源获取策略

### 9.1 URL 规范化

必须统一：

- 去 fragment
- 标准化 query 参数顺序
- 清洗常见追踪参数
- canonical tag 优先但不可盲信
- 同页面不同跳转 URL 去重

### 9.2 抓取策略

顺序：

1. 纯 HTTP 抓取
2. 失败或正文缺失时切 Playwright / 浏览器抓取
3. 附件 URL 交给 Tika / 文档解析

### 9.3 安全约束

必须实现：

- 禁止访问私网地址 / loopback / metadata endpoint
- 限制最大响应体大小
- 限制单域并发
- 限制抓取超时
- 限制重定向层数
- 限制 MIME 白名单
- 默认拒绝 `file://` 等危险 scheme

---

## 10. Source Calibration（来源校准）

每个 `source_document` 至少产出五个分项分数：

- `authority_score`
- `freshness_score`
- `originality_score`
- `consistency_score`
- `safety_score`

最终分：

```text
final_source_score = weighted_sum(...) - penalties
```

### 10.1 初版规则

- 官方站、原始论文、原始公告 > 新闻转载 > 聚合站 > 无法识别来源页面
- 原始发布时间越近，freshness 越高
- 转载链越长，originality 越低
- 与多数高质量来源冲突时 consistency 降低
- 含注入指令、异常页面、抓取失败页面 safety 降低

### 10.2 校验要求

- claim 至少需要 1 个高质量 support evidence
- 重要结论尽量需要 2 个独立来源支持
- 冲突结论必须显示“存在争议/待确认”

---

## 11. 报告结构

报告必须输出为 Markdown；HTML/PDF 可后续生成。

结构固定：

1. 标题
2. 研究问题
3. 执行摘要
4. 方法与来源范围
5. 关键结论
6. 分结论与证据
7. 冲突 / 不确定性
8. 未解决问题
9. 附录：来源列表
10. 附录：claim → citation spans 映射

### 11.1 报告红线

- 不允许无引用 claim
- 不允许把模型推断伪装成已证实事实
- 不允许隐藏冲突来源

---

## 12. 可观测性

至少实现：

- `/healthz`
- `/readyz`
- 结构化日志
- Prometheus metrics
- 任务事件流
- worker 心跳
- 失败重试统计
- 外部依赖可达性检查

建议指标：

- `research_tasks_total`
- `research_task_duration_seconds`
- `fetch_jobs_total`
- `fetch_failures_total`
- `claims_generated_total`
- `claims_verified_total`
- `opensearch_index_latency_seconds`
- `crawler_bytes_downloaded_total`

---

## 13. 基础设施与部署要求

### 13.1 镜像策略

- 不允许使用 `latest`
- 不允许使用 `main`
- 所有镜像固定版本
- `.env.example` 必须完整

### 13.2 Compose 原则

- dev / prod 分 profile 或分文件
- OpenSearch dev 可简化，但 prod 必须保留安全配置
- 只有 Caddy / Open WebUI / orchestrator API 对外暴露
- Postgres / Redis / Tika / MinIO / OpenSearch / SearXNG 默认内网

### 13.3 对象存储

必须自动初始化 bucket：

- `snapshots`
- `attachments`
- `reports`
- `artifacts`

必须设计 key 前缀：

```text
snapshots/{task_id}/{run_id}/{snapshot_id}.html
attachments/{task_id}/{attachment_id}.pdf
reports/{task_id}/v{n}/report.md
artifacts/{task_id}/{name}
```

---

## 14. 开发阶段（Codex 必须按阶段提交）

### Phase 0：仓库骨架与工程纪律

目标：

- 建立目录结构
- 建立 pyproject、lint、format、test、pre-commit
- 建立 `.env.example`
- 建立 `docker-compose.dev.yml`
- 建立 `Makefile`

交付：

- 仓库可 `make lint`、`make test`
- FastAPI 服务能启动
- health endpoint 可用

禁止：

- 不要提前实现复杂业务逻辑

### Phase 1：数据库与 migration

目标：

- 建立 Alembic
- 建立核心表
- 建立 ORM / repository 层

交付：

- `alembic upgrade head` 成功
- 单元测试覆盖主要 repo
- 关键索引和唯一约束齐全

### Phase 2：任务 API 与事件流

目标：

- 创建 / 查询 / 暂停 / 恢复 / 取消任务 API
- task_event 记录
- 基础状态机框架

交付：

- 能提交任务并看到状态变化
- pause / resume / cancel 行为可测

### Phase 3：搜索与候选 URL 入库

目标：

- 接入 SearXNG client
- query expansion
- 候选 URL 去重、规范化、入库

交付：

- 给定 query 能生成 search_query 和 candidate_url
- 域名 allow/deny 生效

### Phase 4：抓取与快照

目标：

- 实现 fetch_job / fetch_attempt
- HTTP 抓取 + 浏览器抓取 fallback
- 内容快照落 MinIO

交付：

- 至少成功抓取 5 个页面
- 失败页面有 attempt 记录
- 快照能回看

### Phase 5：解析、切块、索引

目标：

- 正文抽取
- 附件发现与 Tika 解析
- chunking
- OpenSearch 索引

交付：

- source_document / source_chunk 可查询
- OpenSearch 可按 task_id / text 检索

### Phase 6：claim drafting 与 evidence linking

目标：

- 基于检索结果生成 claim 草案
- 为 claim 绑定 citation spans

交付：

- 每条 claim 至少绑定 1 个 citation span
- 不允许出现无证据 claim

### Phase 7：verification 与 source calibration

目标：

- 实现来源打分
- support / contradict / weak support 关系识别
- claim verification status 决策

交付：

- claim 状态分为 supported / mixed / unsupported
- 冲突证据能展示

### Phase 8：报告生成

目标：

- 生成 Markdown 报告
- 报告存储到 MinIO
- 提供下载接口

交付：

- 报告包含执行摘要、结论、证据、冲突与附录

### Phase 9：Open WebUI 接入

目标：

- 提供基础 UI 工作流
- 任务列表 / 详情 / 报告页

交付：

- 用户在 WebUI 或自建页面可看到任务与报告

### Phase 10：OpenClaw（可选）

目标：

- 接消息入口
- 支持“发起任务 / 查询状态 / 返回报告链接”

---

## 15. Codex 执行规则

Codex 必须遵守：

1. 一次只做一个 phase。
2. 每个 phase 结束后先输出：
   - 改了哪些文件
   - 为什么这样改
   - 如何运行
   - 如何验收
   - 已知限制
3. 不允许跨 phase 偷跑复杂功能。
4. 先写 migration，再写 ORM，再写 service，再写 API。
5. 先写测试，再补实现，至少保证关键路径有测试。
6. 任何第三方版本必须固定。
7. 所有配置必须从环境变量读取。
8. 所有外部 I/O 必须带 timeout、retry、日志。
9. 所有副作用操作必须具备幂等保护。
10. 遇到不确定设计时，优先保持最小可运行方案，不擅自扩 scope。

---

## 16. Codex 输出格式要求

每次提交必须使用以下格式：

```text
1. 本轮目标
2. 修改文件清单
3. 关键实现说明
4. 运行命令
5. 验收步骤
6. 风险与待办
```

如果是数据库改动，必须额外输出：

```text
7. migration 说明
8. 回滚方式
```

---

## 17. 首轮给 Codex 的启动指令

将下面这段直接发给 Codex：

```text
你现在是该项目的实现代理。请严格按照《Deep Research 平台开发文档（Codex 执行版）》分阶段实施，不要跳 phase，不要擅自扩大范围。

当前只执行 Phase 0：仓库骨架与工程纪律。

目标：
- 建立目录结构
- 初始化 Python 工程（pyproject.toml）
- 配置 ruff / black / pytest / mypy / pre-commit
- 建立 FastAPI 服务最小骨架
- 提供 /healthz 和 /readyz
- 提供 docker-compose.dev.yml 与 .env.example
- 提供 Makefile（至少包含 lint / format / test / run）

要求：
- 不要实现数据库业务逻辑
- 不要实现搜索/抓取功能
- 所有版本固定
- 输出完整文件修改清单
- 给出运行和验收命令
```

---

## 18. 第二轮给 Codex 的指令模板

Phase 0 完成后，后续统一使用：

```text
继续执行下一阶段：Phase N。

要求：
- 严格限制在该阶段范围内
- 保持与现有代码风格一致
- 优先最小可运行实现
- 必须补充对应测试
- 输出：目标、修改文件、实现说明、运行命令、验收步骤、风险与待办
```

---

## 19. 验收标准（总）

系统达到 v1 合格线，至少满足以下条件：

1. 用户能创建 research task。
2. 系统能自动完成至少一轮搜索、抓取、解析、索引、claim drafting、verification、reporting。
3. 每条 claim 都能追溯到 citation span。
4. 任意失败页面都有 fetch attempt 记录。
5. 任务支持 pause / resume / cancel。
6. 报告可下载，且附带来源附录与冲突说明。
7. 关键服务有 healthcheck、日志和基本指标。

---

## 20. 本项目的实现哲学

Codex 不应把这个系统实现成：

- 一个大而乱的 agent playground
- 一个同步聊天接口包着搜索工具
- 一个只能 demo、不能回溯的“自动总结器”

Codex 应把它实现成：

- 一个以 research task 为核心对象的系统
- 一个以 ledger 和 evidence 为中心的数据流平台
- 一个可恢复、可审计、可逐步增强的工程骨架

文档到此为止。
