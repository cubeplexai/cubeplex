# M1-E1 · Cost Tracking 设计

**Status**: Draft · 2026-04-28
**Owner**: @xfgong
**Scope**: 按 user / workspace / model provider 聚合 LLM token 消耗与费用；管理员看板（M2 控制台独立 tab）；按 workspace 导出 CSV；暴露稳定读侧 API 供 M1-E3 EE Policy 消费。
**属于**: v1 开源发布待办 · M1-E1
**Backlog 索引**: `docs/superpowers/specs/2026-04-21-v1-oss-release-backlog.md`
**依赖**: M2 控制台骨架（admin sub-nav 挂新 tab）、M0（`require_org_admin` dependency）

---

## 1. 背景与目标

### 1.1 现状

- 无任何 token / cost 记录机制；LLM 调用完成后 `usage_metadata` 数据随 SSE 流流走，不落库
- `ModelConfig.cost`（`backend/cubeplex/llm/config.py`）已有 input/output/cache_read/cache_write 单价（USD/百万 token）
- 每次 LLM 调用完成时，`usage_metadata.input_tokens / output_tokens` 已在 `AIMessage` 上现成可读（`stream.py:96-110`）

### 1.2 目标

- **Finance / 账单分账**：企业管理员按 workspace 对账，CSV 导出做月结；对账粒度与 provider 真账单一致（per-call 明细）
- **成本看板**：管理后台"成本"tab 展示当月汇总、按 workspace / model 分组
- **EE Policy 接缝**：暴露 `BillingRepository.get_workspace_spend(...)` 稳定读侧 API，供 M1-E3 预算检查消费（v1 不做 enforcement）

### 1.3 非目标

- 按 skill 聚合（skills 是 prompt pattern，无执行边界，无法可靠归因）
- 沙箱计算时长 / 存储计费（v1 只管 LLM；架构已为其留好扩展点）
- 实时流式预算断流（归 M1-E3 EE Policy）
- 多币种汇率转换服务（v1 以 provider 配置的计价币种直接记录；同币种 SUM，不跨币种折算）

---

## 2. 决策记录

| # | 决策 | 备选 | 选用理由 |
|---|---|---|---|
| D1 | 记录粒度：**per-LLM-call 一行**（每次模型 HTTP 请求一条 `billing_events` 行） | per-turn / per-conversation 聚合 | per-call 与 provider 账单粒度一致；`usage_metadata` 在每次调用完成时即可拿到；明细可追溯 |
| D2 | 写入点：**`CostMiddleware` 包 `awrap_model_call`**（middleware 链末尾） | LangChain callback / LLM client 子类 / stream layer | 与现有 middleware 风格一致；直接访问 `ModelRequest / ModelResponse`；`usage_metadata` 比 callback 侧的 `llm_output.token_usage` 更可靠（streaming 时后者常空）；测试友好 |
| D3 | 写入方式：**`asyncio.create_task` fire-and-forget，独立 DB session** | 同步写 / 消息队列 | LLM 调用耗时 >> DB 写入；不阻塞 LLM 路径；DB 故障时 log 不抛，用户体验不降级 |
| D4 | cost 计算：**写入时 snapshot**（当时 `ModelConfig.cost` × tokens → `cost_amount_micro`；单价同步存入行） | 读时计算 | finance 改价后历史账单不回溯变化；单价列本身也提供审计追溯 |
| D5 | 不引入 traceloop / OpenLLMetry 做 cost capture | 复用 M1-E2 span 触发 | M1-E1 与 M1-E2 平行开发；finance 数据不应依赖 trace pipeline 的 retention / 可用性；代码量相当但解耦 |
| D6 | **Class Table Inheritance**：`billing_events`（父表）+ `billing_llm_events`（子表） | 单张宽表 / UNION ALL 多表 | 父表聚合查询无需 JOIN；子表保留 LLM 专属类型安全字段；未来扩展 sandbox / storage 不改父表 |
| D7 | 表名统一 `billing_` 前缀：`billing_events` / `billing_llm_events` / `billing_sandbox_events`（未来）... | `cost_events` / `llm_cost_events` 混用 | 同概念同前缀；代码侧类名 `BillingEvent` / `LlmBillingEvent` / `BillingRepository` 对齐 |
| D8 | **不分区**（MySQL 单表 + 索引）；月度批量 DELETE 做 retention | MySQL RANGE 分区 | v1 行数级别 < 1M/年，索引查询 < 200ms；MySQL 分区要求 PK 含分区键，破坏现有 UUID PK 模式；行数破 5M 时再迁移（非破坏性） |
| D9 | `currency CHAR(3) DEFAULT 'USD'` + `cost_amount_micro bigint`（不固定 USD 命名） | 固定 `cost_usd_micro` | provider 可按 CNY 计价；同币种聚合无需汇率服务；未来多币种 SUM 按 `GROUP BY currency` 即可 |
| D10 | Fallback hop 兜底：`LLMFactory.create_default()` 为 fallback chain 每个 runnable 挂 `LightweightFallbackCallback`，`on_llm_error` 写 `status="fallback_failed"` 行 | 忽略 / 估算 | primary 失败的 prefill token 部分 provider 计费；独立行可追踪；不污染 middleware 主路径 |
| D11 | Subagent：同一 `billing_events` 表通过 `parent_run_id` + `subagent_depth` 追踪层级 | 单建 subagent 子表 | 父子同类型，共享所有聚合查询；subagent depth 仅用于明细钻取 |
| D12 | EE Policy 接缝：v1 只暴露 `BillingRepository.get_workspace_spend(...)` 读侧 API，不预 wire enforcement | 预 wire `before_llm_call` hook | M1-E3 自建 `BudgetMiddleware` 消费读侧 API；M1-E1 单纯记录，与 enforcement 解耦 |
| D13 | 失败 LLM call 的 `input_tokens` 记 0 | 估算 / 从 request 倒推 | 请求未完成时无准确 token 数；0 + `status="error"` 让 finance 识别后手工补；比不诚实的估算更可信 |

---

## 3. 数据模型

### 3.1 `billing_events`（父表）

```sql
CREATE TABLE billing_events (
    id              CHAR(36)     NOT NULL,
    org_id          CHAR(36)     NOT NULL,
    workspace_id    CHAR(36)     NOT NULL,
    user_id         CHAR(36)     NOT NULL,
    conversation_id CHAR(36)     NOT NULL,
    event_type      VARCHAR(32)  NOT NULL,   -- "llm_call" | "sandbox_compute" | "storage" | ...
    cost_amount_micro BIGINT     NOT NULL,   -- 以 currency 计的 × 10⁶ 金额
    currency        CHAR(3)      NOT NULL DEFAULT 'USD',
    started_at      DATETIME(6)  NOT NULL,
    ended_at        DATETIME(6)  NOT NULL,
    duration_ms     INT          NOT NULL,
    status          VARCHAR(20)  NOT NULL,   -- "success" | "error" | "fallback_failed"
    created_at      DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id)
);

CREATE INDEX idx_be_org_ws_time    ON billing_events (org_id, workspace_id, started_at DESC);
CREATE INDEX idx_be_org_user_time  ON billing_events (org_id, workspace_id, user_id, started_at DESC);
CREATE INDEX idx_be_org_time       ON billing_events (org_id, started_at);
CREATE INDEX idx_be_conversation   ON billing_events (conversation_id);
```

### 3.2 `billing_llm_events`（子表，LLM 专属）

```sql
CREATE TABLE billing_llm_events (
    id                           CHAR(36)    NOT NULL,
    billing_event_id             CHAR(36)    NOT NULL,   -- FK → billing_events.id
    provider                     VARCHAR(64) NOT NULL,   -- 来自 LLMFactory 的 provider_name
    model_id                     VARCHAR(128) NOT NULL,
    input_tokens                 INT         NOT NULL DEFAULT 0,
    output_tokens                INT         NOT NULL DEFAULT 0,
    cache_read_tokens            INT         NOT NULL DEFAULT 0,
    cache_write_tokens           INT         NOT NULL DEFAULT 0,
    price_input_per_mtok_micro   BIGINT      NOT NULL,   -- 写入时 snapshot
    price_output_per_mtok_micro  BIGINT      NOT NULL,
    price_cache_read_per_mtok_micro  BIGINT  NOT NULL DEFAULT 0,
    price_cache_write_per_mtok_micro BIGINT  NOT NULL DEFAULT 0,
    parent_run_id                CHAR(36)    NULL,        -- subagent 时指父 billing_event_id
    subagent_depth               SMALLINT    NOT NULL DEFAULT 0,
    error_class                  VARCHAR(128) NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_ble_billing_event FOREIGN KEY (billing_event_id)
        REFERENCES billing_events (id) ON DELETE CASCADE
);

CREATE INDEX idx_ble_provider_model  ON billing_llm_events (provider, model_id);
CREATE INDEX idx_ble_parent          ON billing_llm_events (parent_run_id);
```

### 3.3 未来子表（占位，v1 不建）

```
billing_sandbox_events  (billing_event_id FK, sandbox_id, cpu_seconds, memory_mb_seconds, image_id)
billing_storage_events  (billing_event_id FK, storage_path, size_bytes, period_days)
```

### 3.4 Retention

- 默认保留 **13 个月**（12 全月 + 当月）
- 月度 cron（后台 task）：`DELETE FROM billing_events WHERE started_at < NOW() - INTERVAL 13 MONTH LIMIT 10000`，分批 + 限速，CASCADE 清子表
- 行数超 5M 时迁移到 MySQL RANGE 分区表（非破坏性：建新分区表 → INSERT SELECT → RENAME）

---

## 4. 记账流程（`CostMiddleware`）

### 4.1 新增文件

`backend/cubeplex/middleware/cost.py`

```python
class CostMiddleware(AgentMiddleware):
    def __init__(
        self,
        *,
        recorder: BillingRepository,   # 直接注入 repo，不额外包装
        org_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        parent_billing_id: UUID | None = None,
        subagent_depth: int = 0,
    ): ...

    async def awrap_model_call(self, request, handler):
        run_id = uuid4()
        started_at = utcnow()
        try:
            response = await handler(request)
            asyncio.create_task(
                self._record(request, response, run_id, started_at, status="success")
            )
            return response
        except Exception as exc:
            asyncio.create_task(
                self._record(request, None, run_id, started_at,
                             status="error", error_class=type(exc).__name__)
            )
            raise
```

**fire-and-forget 细节**：
- `create_task` 在独立 `AsyncSession` 里执行两个 INSERT（`billing_events` + `billing_llm_events`）同一事务
- DB 故障时 `structlog.warning(...)` 记录，不抛异常，不影响 LLM 响应路径

### 4.2 Provider / model 解析

`LLMFactory.create()` 在返回 LLM 实例前赋两个属性：

```python
llm._cubeplex_provider = provider_name    # e.g. "openai"
llm._cubeplex_model_id = model_config.id  # e.g. "gpt-4o-mini"
```

`CostMiddleware._record` 从 `request.model` 读取这两个属性。

### 4.3 Token 提取

```python
usage = response.result.usage_metadata or {}
input_tokens      = usage.get("input_tokens", 0)
output_tokens     = usage.get("output_tokens", 0)
cache_read_tokens = (usage.get("input_token_details") or {}).get("cache_read", 0)
cache_write_tokens= (usage.get("output_token_details") or {}).get("cache_write", 0)
```

失败时（`response=None`）全部记 0。

### 4.4 Cost snapshot 计算

```python
cost = model_config.cost
cost_amount_micro = int(
    (input_tokens       * cost.input        / 1_000_000
   + output_tokens      * cost.output       / 1_000_000
   + cache_read_tokens  * cost.cache_read   / 1_000_000
   + cache_write_tokens * cost.cache_write  / 1_000_000)
   * 1_000_000
)
```

### 4.5 Subagent 透传

`_create_subagent_tool`（`middleware/subagents.py`）spawn 子 agent 时，把当前 `CostMiddleware` 实例 clone 一份：

```python
child_cost_mw = CostMiddleware(
    recorder=parent_cost_mw.recorder,
    org_id=parent_cost_mw.org_id,
    workspace_id=parent_cost_mw.workspace_id,
    user_id=parent_cost_mw.user_id,
    conversation_id=parent_cost_mw.conversation_id,
    parent_billing_id=current_billing_event_id,   # 父 LLM call 的 billing_event.id
    subagent_depth=parent_cost_mw.subagent_depth + 1,
)
```

将 `child_cost_mw` 加入子 agent 的 `inherited_middleware`（已有 `CitationMiddleware` / `SandboxMiddleware` 同样走这条路径）。

### 4.6 Fallback hop 兜底

`LLMFactory.create_default()` 为 `RunnableWithFallbacks` 中每个 runnable 挂 `LightweightFallbackCallback`：
- `on_llm_error` → `recorder.record_fallback_failure(provider, model_id, input_tokens_estimate=0, status="fallback_failed")`
- 只记失败路径，成功路径由 middleware 记，**不双写**

### 4.7 挂载到 `create_cubeplex_agent`

```python
# backend/cubeplex/agents/graph.py
if billing_recorder is not None:
    middleware.append(
        CostMiddleware(
            recorder=billing_repo,   # BillingRepository 实例，由 FastAPI dep 注入
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
    )
```

`billing_recorder` 在 API route handler 里通过 FastAPI dependency 注入；`org_id / workspace_id / user_id` 来自 `RequestContext`（已有）。

---

## 5. BillingRepository + 读侧 API

### 5.1 `BillingRepository`

`backend/cubeplex/repositories/billing.py`（继承 `ScopedRepository`，自动 `(org_id, workspace_id)` 过滤）：

```python
class BillingRepository(ScopedRepository[BillingEvent]):

    async def insert_llm_event(
        self, event: BillingEvent, detail: LlmBillingEvent
    ) -> None:
        """两个 INSERT 同一事务（父表 + 子表）"""

    async def get_workspace_spend(
        self,
        *,
        since: datetime,
        until: datetime,
        group_by: Literal["user", "model", "day"] = "day",
    ) -> list[CostAggregateRow]:
        """按 group_by 聚合当前 workspace 的花费"""

    async def get_org_spend(
        self,
        *,
        since: datetime,
        until: datetime,
        group_by: Literal["workspace", "user", "model", "day"],
    ) -> list[CostAggregateRow]:
        """跨 workspace 聚合（admin 看板用）"""

    async def record_fallback_failure(
        self,
        *,
        org_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        provider: str,
        model_id: str,
        started_at: datetime,
        ended_at: datetime,
        error_class: str,
    ) -> None:
        """记录 fallback chain 里 primary 的失败 hop（status="fallback_failed"，tokens 记 0）"""

    async def stream_events_for_export(
        self,
        *,
        since: datetime,
        until: datetime,
        workspace_id: UUID | None = None,   # None = 全 org
    ) -> AsyncIterator[dict[str, Any]]:
        """流式游标查询（billing_events JOIN billing_llm_events 平铺行），供 CSV 导出"""
```

`get_workspace_spend` 是 M1-E3 EE Policy 的**稳定读侧 API**——M1-E3 在 `check(user, "llm.invoke", resource)` 时调此方法判预算，无需感知底层表结构。

### 5.2 Admin API 端点

挂在 `backend/cubeplex/api/routes/v1/admin.py`（已有 admin router，全部 `require_org_admin`）：

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/v1/admin/cost/summary` | 全 org 汇总：默认本月，by_workspace + by_day + by_model |
| GET | `/api/v1/admin/cost/by-workspace/{ws_id}` | 单 workspace 钻取，`?group_by=user\|model\|day` |
| GET | `/api/v1/admin/cost/export.csv` | 全 org 时间窗口 CSV 流式导出 |
| GET | `/api/v1/admin/cost/by-workspace/{ws_id}/export.csv` | 单 workspace CSV 导出 |

所有端点带 `?from=YYYY-MM-DD&to=YYYY-MM-DD`；默认 `from`=本月第 1 天、`to`=今天。

### 5.3 响应 schema

```python
class CostAggregateRow(BaseModel):
    bucket: str              # workspace_id / user_email / "openai/gpt-4o-mini" / "2026-04-28"
    bucket_type: Literal["workspace", "user", "model", "day"]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_amount_micro: int   # 客户端 / 1_000_000 得 float USD
    currency: str
    call_count: int

class CostSummaryResponse(BaseModel):
    from_date: date
    to_date: date
    total_cost_amount_micro: int
    currency: str
    total_calls: int
    by_workspace: list[CostAggregateRow]
    by_model: list[CostAggregateRow]
    by_day: list[CostAggregateRow]
```

---

## 6. CSV 导出

**响应头**：
```
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="cost_2026-04_acme-org.csv"
```

**列顺序**：
```
started_at, workspace_id, workspace_name, user_email, conversation_id,
provider, model_id,
input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
cost_amount, currency,
status, subagent_depth, duration_ms
```

实现：`StreamingResponse` + `BillingRepository.stream_events_for_export()` 游标，逐行 `yield` 不全量 load。

---

## 7. M2 Admin Console Tab

### 7.1 Admin sub-nav 新增项

`frontend/packages/web/components/admin/AdminSubNav.tsx` 在 Sandbox 和"扩展"分隔线之间插入：

```tsx
<NavItem href="/admin/cost" icon={CircleDollarSign}>成本</NavItem>
```

### 7.2 路由文件

`frontend/packages/web/app/admin/cost/page.tsx`（**真实功能，不是 Coming Soon**）

### 7.3 页面结构

```
/admin/cost
┌──────────────────────────────────────────────────────────┐
│ 成本概览                 [本月 ▾]  [导出全 org CSV ↓]     │
├──────────────────────────────────────────────────────────┤
│ 总花费: $12.34  USD · 调用次数: 3,421                     │
├──────────────────────────────────────────────────────────┤
│ 按 Workspace                                             │
│ Workspace    调用次数   Tokens      花费      操作        │
│ Personal     1,200      2.4M        $5.20    [导出 CSV]  │
│ Work         2,221      4.1M        $7.14    [导出 CSV]  │
├──────────────────────────────────────────────────────────┤
│ 按 Model                                                 │
│ openai / gpt-4o-mini    2,100   3.1M   $3.10            │
│ sensedeal / doubao-...  1,321   3.3M   $9.24            │
└──────────────────────────────────────────────────────────┘
```

- 时间选择器：本月 / 上月 / 最近 30 天 / 自定义（date-range picker）
- 金额显示：`(cost_amount_micro / 1_000_000).toFixed(4)` + `currency` 标签
- 多币种：各 currency 分栏显示，不混合 SUM
- shadcn `Table` 组件，按花费降序，v1 无分页

---

## 8. EE Policy 接缝（M1-E3，v1 不实现）

- `BillingRepository.get_workspace_spend(workspace_id, since, until)` 为 **稳定接口**，M1-E3 `CasbinPermissionChecker` 在调用 `check(user, "llm.invoke", resource)` 时消费
- M0 `PermissionChecker.check` 已定义；M1-E3 加 `action="llm.invoke"` 属非破坏性扩展（见 M0 §10.1）
- `CostMiddleware` 本身**不调** `PermissionChecker`；enforcement 逻辑全在 M1-E3 的 `BudgetMiddleware` 里
- v1 唯一要做的：`BillingRepository` 接口签名冻结，M1-E3 不需要感知底层 SQL 表结构

---

## 9. 测试策略

### 9.1 Backend Unit 测试（不走 E2E LLM）

位置：`backend/tests/` 普通层（非 `e2e/`）

- `BillingRepository`：insert_llm_event + get_workspace_spend + get_org_spend，走真实 test DB（MySQL）
- `CostMiddleware` 主路径：mock handler 返回 `ModelResponse`，断言写入了正确的 billing 行（tokens / provider / cost_amount_micro / currency）
- 失败路径：handler 抛异常 → 断言写了 `status="error"` 行，异常向上传播
- Subagent depth：`parent_billing_id` + `subagent_depth=1` 正确写入
- Cost 计算：给定 `ModelCost` 配置，断言 `cost_amount_micro` 计算结果

### 9.2 Backend E2E 测试（走真实 LLM）

位置：`backend/tests/e2e/test_billing.py`

- 发一条消息 → 等 done → 查 `billing_events` 断言 `event_type="llm_call"` 行存在 + `cost_amount_micro > 0` + `input_tokens > 0`
- 查 `billing_llm_events` 断言 `provider` / `model_id` 非空
- 触发子 agent 的 prompt → 断言存在 `subagent_depth=1` 的行且 `parent_billing_id` 非空

### 9.3 Frontend E2E 测试（Playwright）

- admin 身份进 `/admin/cost` → 断言页面有"成本概览"标题 + 至少一行 workspace 数据
- 点"导出 CSV" → 断言响应 `Content-Type: text/csv`

---

## 10. 交付清单

### 10.1 新增文件

**Backend**
- `backend/cubeplex/middleware/cost.py` — `CostMiddleware` + `BillingRecorder`
- `backend/cubeplex/models/billing.py` — `BillingEvent` + `LlmBillingEvent` SQLModel
- `backend/cubeplex/repositories/billing.py` — `BillingRepository`
- `backend/cubeplex/api/schemas/billing.py` — `CostAggregateRow` / `CostSummaryResponse`
- `backend/cubeplex/api/routes/v1/cost.py` — 4 个 admin cost 端点
- `backend/alembic/versions/<hash>_billing_tables.py` — `billing_events` + `billing_llm_events`
- `backend/tests/test_billing_repository.py`
- `backend/tests/test_cost_middleware.py`
- `backend/tests/e2e/test_billing.py`

**Frontend**
- `frontend/packages/web/app/admin/cost/page.tsx`
- `frontend/packages/web/components/admin/CostSummaryTable.tsx`
- `frontend/packages/core/src/api/billing.ts` — API client 函数
- `frontend/packages/core/src/types/billing.ts` — TS 类型

### 10.2 修改文件

**Backend**
- `backend/cubeplex/llm/factory.py` — `create()` / `create_default()` 赋 `_cubeplex_provider` / `_cubeplex_model_id`；fallback chain 挂 `LightweightFallbackCallback`
- `backend/cubeplex/llm/config.py` — `ModelCost` 加 `currency: str = "USD"`
- `backend/cubeplex/agents/graph.py` — `create_cubeplex_agent()` 新增 `billing_recorder` / `user_id` 参数，条件挂 `CostMiddleware`
- `backend/cubeplex/middleware/subagents.py` — `_create_subagent_tool` 透传 `CostMiddleware` clone
- `backend/cubeplex/api/routes/v1/admin.py` — include cost router
- `backend/cubeplex/api/routes/v1/conversations.py`（或 messages route）— 注入 `BillingRecorder` + `user_id` 传给 `create_cubeplex_agent`

**Frontend**
- `frontend/packages/web/components/admin/AdminSubNav.tsx` — 加"成本"nav item

---

## 11. 未决事项

- [ ] `billing_events` retention cron 实现方式：FastAPI lifespan task vs 外部 cron job（实现时定）
- [ ] 多币种 dashboard 展示：同 org 有 CNY + USD 时，分栏 vs 明确提示"多币种，无法合并"（实现 admin tab 时定）
- [ ] `LightweightFallbackCallback` 如何从 fallback context 取准确 `input_tokens`（实现 factory 时定；v1 可先记 0）
- [ ] CSV 中 `workspace_name` / `user_email` 来自 join 查询，需确认 `BillingRepository.stream_events_for_export()` 的 join 效率（实现时确认索引覆盖）
