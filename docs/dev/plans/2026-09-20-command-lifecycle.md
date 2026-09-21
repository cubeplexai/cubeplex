# 后台任务生命周期实施计划

- 状态：按 review 修订后的 [spec](../specs/2026-09-20-command-lifecycle-design.md) 重写；2026-09-21 用户授权补齐两项 review 后开始实现。实施进度以实际验证记录为准，部署与线上数据操作仍需独立授权。
- 日期：2026-09-20；更新：2026-09-21（America/Phoenix）。保留原路径供已有链接引用。
- Goal：让 command／monitor 跨 run 运行、可恢复观察并可靠停止，结果按 conversation 投递，用户能区分模型运行、后台执行与通知处理。
- Architecture：CubePlex 的公共 task service 保存生命周期，command 适配器只处理具体进程、环境和日志；conversation 控制服务统一执行批次、Stop、删除及新请求受理。每条后台 notice 独立通过现有 Session 输入机制投递，空闲时开启新 run，UI 从公共快照和待处理摘要恢复；CubeLoop 仅增加 Todo 收尾扩展，不保持小时级 live Session。
- Tech stack：FastAPI、SQLModel／PostgreSQL、Redis、CubeLoop ExecutionSession、现有 Sandbox driver、React 19／Next.js／`@cubeplex/core`。
- 核对基线：CubePlex `f622d97e`；本地 `../cubepi` 的 CubeLoop `65ff7096ca2e78e707bdd1d96f8dbca38ef5a0cc`。

## 0. 开工条件与交付边界

- 设计 worktree 没有 `.worktree.env`、服务或数据库分配；按 [worktrees](../../worktrees.md) 建立隔离实现环境，读取分配，不用主库验证。
- 设计已获用户审阅确认；按 feature workflow 先形成设计 PR，再实施。代码按下述关注点拆分，不将设计提交与代码混成一个 PR；部署与数据切换不属于本次实现授权。
- execute 缺省总期限已确认：后端配置 `sandbox.command_default_timeout_seconds` 默认 3600 秒（1 小时），显式 `timeout_seconds` 优先；C1 实现配置校验和持久 deadline，C4 更新工具说明，C5 展示实际期限。15 秒前台预算、monitor 期限及平台上限保持各自语义。
- 本计划取代旧 live-run 计划；旧 CubeLoop awaiting-work spec／plan 不再是依赖，也不在本轮修改。没有 IdlePolicy、waiting ExecutionResult、run waiting 事件或 run command scope 表。
- MCP 普通调用与当前同步 subagent 保持现状；不实现未来适配器、工作流 DAG、业务重试、跨会话迁移或独立任务中心。
- 每个单元按一个关注点组织提交／PR，依赖单元串行落地，不让多个实现者并行改同一生命周期状态机。中间版本不承诺可独立部署；完整链路通过后统一切换，不为阶段上线增加转发兼容 API 或双写状态。

## 1. 顺序与公共契约

| 单元 | 交付关注点 | 依赖 |
| --- | --- | --- |
| C1 | 公共持久 task 与 command 唯一执行管理 | 无 |
| C2 | 执行受理、主 Stop、会话删除与调度边界 | C1 |
| C3 | 独立后台事件投递与 checkpoint 对账 | C1、C2 |
| R | CubeLoop Todo 可校验等待声明 | 可独立开发，仅在上游仓库交付 |
| C4 | 工具交接、宿主 Todo 校验与正常结束 run | C1–C3、R |
| C5 | 公共读写 API、后台事件 UI 与冷刷新恢复 | C1–C4 |
| C6 | 命令日志写入确认与临时分片清理 | C1；验收与 C3/C5 联调 |

推荐审阅顺序 C1 → C2 → C3 → C4 → C5；R 可先完成，C6 可在 C1 接口稳定后独立交付。这里按所有权和接口拆分，不按最初六个症状拆补丁。

实施记录（2026-09-21）：#634 仅交付 C1 的基础模型、增量结构、事务预留和默认期限配置；87 项本地回归及该 PR 的 CI 通过，未接入运行时。C1 运行时本地提交 fafd5fef 已实现原实例接管、owner 隔离、停止事实和默认期限恢复，110 项局部回归通过；monitor、日志收尾及宿主接线仍待完成，尚未 push／启用。R 在独立 CubeLoop worktree 实现中，已补宿主等待校验、输入失效、Stop 竞争和 extra 持久化测试；以最终 PR 验证为准。C1 余项、C2–C6、R 交付及依赖集成、数据回填和统一切换仍未全部完成，不能从基础 PR 全绿推断整套方案完成。

各单元中未带仓库前缀的 backend 路径均相对于 `backend/cubeplex/`；R 单元的路径相对于 CubeLoop 仓库。标“新”的文件由对应实施单元创建。

### 1.1 身份、事实与写入者

| 记录 | 锁定契约 |
| --- | --- |
| `background_tasks` | 唯一公共执行状态；scope、conversation、task kind、发起身份／run／tool、可选 parent、generation、notify policy、deadline、停止原因、通知取消标记、owner token／lease、结果引用、revision、backgrounded_at |
| `sandbox_commands` | `task_id` 唯一一对一；保留 command ID、user_sandbox_id、不可变 sandbox_instance_id、provider_ref、命令参数、真实进程观察／exit code、日志 cursor／state、monitor 匹配与限流 |
| `background_task_events` | 单一 outbox；稳定 notice ID、task ID、reason、摘要／结果引用、去重键、pending／claimed／delivered／discarded、投递 attempt 及 checkpoint 证明 |
| `conversation_execution_admissions` | 来源／稳定 source ID、actor、conversation、generation、run 关联、不可变用户请求摘要与有效执行设置快照；scope 内来源键唯一。C1 建基础关联，C2 在开放受理入口前补齐摘要／快照及事务契约。不是第二份 run 状态机或调度计划 |
| conversation／输入 | conversation 保存单调 generation 与关闭标记；run 关联受理记录，用户／内部输入保存 generation 和明确 source；task revision 与事件 revision 分开 |

公共 task 与事件由同一 service 按适配器证据更新；command 详情不是第二个状态写入者。采用 spec 的 starting／running／waiting_input／succeeded／failed／cancelled／unknown，command 不产生 waiting_input。停止意图、执行事实、日志状态和通知状态不互相代替。

新增 task、事件、受理记录的 public ID 前缀拟为 `bgt`、`bge`、`cea`，在注册表声明并检测冲突；迁移来的 notice 保留原 ID，不因前缀旧而重发。业务时间一律 tz-aware。

### 1.2 内部接口

以下是实施要共同遵守的 typed 边界，不是逐行实现稿：

- `admit_execution(scope, actor, source_kind, source_id, request_fingerprint) -> admission`：首次受理绑定 generation，并在同一事务持久保存服务端首次解析的执行设置快照；重试读取原绑定和快照。冲突 actor／目标／用户请求摘要不可复用身份。内部事件身份仍由源记录确定，不接受客户端提供内部摘要或执行快照。
- `close_execution(scope, expected_generation, reason) -> stop_receipt`：主 Stop 可指定旧批次幂等重入；删除另在同一事务写 deleted_at。
- `reserve_task(scope, admission, task_spec, execution_details) -> reservation`：事务内验证执行权、父任务约束、command cap 和环境身份；提交后才产生远端副作用。
- `handoff_task(task_id, owner_token, tool_result_evidence)`：选择前台结果或后台事件，保证只交付一次终态。
- `record_observation(task_id, owner_token, observation)`：按证据写公共状态、具体结果及需产生的事件，同一事务提交。
- `request_task_stop(scope, task_id, reason) -> stop_receipt`：持久受理，再联系执行适配器；deadline 不等于用户取消通知。
- `ack_notice(notice_id, checkpoint_evidence)`：只有持久输入证明才能 delivered；已提交与取消竞争按真实历史处理。
- `validate_task_wait(scope, admission, task_ids, prior_validation) -> valid | cancelled | invalid(reason)`：查询持久事实；cancelled 仅用于先前有效声明随后被用户取消的自然收尾，不能用来创建新等待声明。不将平台模型传进 CubeLoop。

适配器只暴露真实能力：启动、观察／重连、取结果及可选远端取消等。租约丢失后不可提交新观察；迟到的已知启动句柄仍须通过受约束的登记入口保存并交新 owner 清理，不能因 fencing 丢失它，也不能覆盖新事实。

## C1. 公共 task 与 command 首个实现

### Files

以下 backend 路径以 `backend/cubeplex/` 为根；标“新”的是计划新增。

- `models/background_task.py`、`models/conversation_execution.py`、`repositories/background_task.py`、`repositories/conversation_execution.py`（新）：上述公共持久契约和有 scope 的查询／更新。
- `models/sandbox_command.py`、`repositories/sandbox_command.py`：command 改成类型详情，保存实例身份，退出公共状态／owner 的重复写入。
- `models/conversation.py`、`models/__init__.py`、`models/public_id.py`：generation、模型注册和 ID 前缀。
- `services/background_tasks.py`（新）：登记、交接、观察、停止和事件原子写入。
- `sandbox/command_adapter.py`（新）、`sandbox/base.py`、`sandbox/opensandbox.py`、`sandbox/local.py`、`sandbox/manager.py`：具体能力、按原实例重连、进程证据及 cap／保活，不负责模型唤醒。
- `sandbox/command_coordinator.py` 拆出公共调度到 `services/background_task_coordinator.py`（新）；`api/app.py` 生命周期只注册一个 coordinator。
- `backend/config.yaml`、`config.py`／`api/app.py` 的配置加载与启动校验：增加 `default.sandbox.command_default_timeout_seconds: 3600`，沿用环境变量覆盖，不复用 provider HTTP timeout 或 sandbox TTL。
- `backend/alembic/versions/`：模型 metadata 生成结构 migration；数据迁移见第 2 节。

### Core logic

- 固定锁顺序：conversation → sandbox → task；reserve、Stop、删除和 owner 更新不能互相倒序取锁。短事务完成 cap 与权限检查，provider I/O 在锁外。
- 持久 task／command reservation 和实际 sandbox_instance_id 后才 start；回执携 provider_ref 合并到原实例。UserSandbox 同一行 revive 不改变旧任务的实例绑定。
- coordinator 在原环境观察、取消、保活；观察路径不得隐式创建替代容器。实例不同不等于已证明旧环境销毁；无证据继续 unknown，占 cap。
- 停止采用 observe → 必要时请求取消 → 再观察，not-running 字符串／kill 异常不当作终态；重复停止已终态任务返回原 exit code。
- 15 秒仅是工具前台预算；总期限优先使用显式 `timeout_seconds`，否则读取 `sandbox.command_default_timeout_seconds`，默认 3600 秒；环境变量为 `CUBEPLEX_SANDBOX__COMMAND_DEFAULT_TIMEOUT_SECONDS`。配置严格为正整数，0／负数／非整数／null 校验失败时明确拒绝启动，不作为无限期或静默默认值。受理时计算绝对 deadline，配置修改／交接／接管不重置；background 和 notify=false 不能绕过。monitor 保留既有期限、line／exit 与防刷屏规则；notify=false 只关通知。
- terminal＋result＋事件原子提交。只有当前 owner 更新状态；前台崩溃后先确认旧 attempt 失权，再检查工具结果 checkpoint，不能盲目重启或双发 completion。
- `config.py` 定义共用 `MAX_COMMAND_TIMEOUT_SECONDS = 2_147_483_647`，配置启动校验、显式 deadline service 和 C4 工具 schema 使用同一上限。超出上限在计算前拒绝；保留日期溢出防御检查，不钳制。默认仍为一小时，合法显式值可更长。测试上限／上限加一及超大整数，配置站点页同 PR 说明技术边界。
- 现有同步 subagent 使用共享工具时保留发起 agent/tool 关联；恢复证明覆盖其父工具中持久的子事件，不能只搜索 root 顶层 ToolResult，也不把当前 child Agent 宣称为持久 detached task。

### Tests / docs

- 新 `backend/tests/unit/test_background_task_state.py`：状态映射、取消能力、deadline 与用户 stop 通知策略；纯函数不碰 DB。
- 配置／期限用例覆盖默认 3600、YAML／环境变量覆盖、显式参数优先且可大于 3600、非法值拒绝；e2e 验证后台及 notify=false 同样固化期限，配置变化／worker 重启不改已有 deadline，monitor persistent 与旧无期限数据不被误改。
- 新 `backend/tests/e2e/test_background_tasks.py` 并调整 `test_sandbox_commands.py`：真实 Postgres 验证 scope、cap、owner fencing、交接、终态／事件原子性、迟到 provider_ref。
- 同一 UserSandbox 行由实例 A 换成 B，再运行恢复／kill／日志观察，执行方边界只可收到 A；无历史实例证据保持 unknown，明确销毁才能释放 cap。
- 故障注入位于外层执行方，service／repository 用真实实现。记录恢复期间零重复 start，不用“mock 方法被调用”代替业务结果。
- 更新现有站点 `docs/site/docs/guides/conversations/sandboxes.md` 的受管任务、默认 1 小时及显式覆盖、期限和取消事实；`docs/site/docs/deployment/backend-config.md` 同 PR 补配置键、环境变量、合法值与仅影响新命令的规则，不声称 UI 已完成。

## C2. 受理、停止、删除共用 conversation 控制

### Files

- `services/conversation_execution.py`（新）、C1 的受理 repository：受理和关闭的唯一事务入口。
- `api/routes/v1/conversations.py`、`api/schemas/conversations.py`、`repositories/conversation.py`：消息首次受理、带 generation 的主 Stop、软删除与停止原子提交。
- `api/routes/v1/workspaces.py`、`models/workspace.py` 及 workspace teardown 共用服务：持久 deleting 标记、停止／清理门槛、生命周期记录的外键删除顺序；其他组织级删除入口复用同一门槛。
- `api/routes/v1/auth.py`、`models/user.py` 及账号删除服务：固定待删 actor、持久删除资格与跨 scope 清理；保留账号行和恢复证明直到可安全物理删除。新业务入口拒绝 deleting actor，认证入口仍允许本人查询／重试删除。
- `models/deletion_operation.py`（新）、对应 repository／service、`models/public_id.py` 注册前缀值 `delo`：生成器自行添加分隔符，最终 ID 为 `delo-<body>`，并补前缀格式测试。删除操作及 token 摘要无目标级联 FK；账号与 workspace 各自只读状态 handler，不共用 scope 参数分支。终态与硬删除同事务，回执 30 天后回收。
- `api/routes/v1/ws_members.py`、`workspaces.py` 的 leave、`admin_members.py`、membership models／repositories 及鉴权依赖：持久 revoked 标记、scope 内执行撤销和可恢复清理；成员管理及离开 UI 处理 cleanup_pending，站点 `admin/members.md` 同步。
- `im/identity.py`、`api/routes/v1/shares.py` 及其他直接读取 membership 的消费者：统一采用有效成员条件，排除撤销／删除中的身份。实施时枚举 `Membership`／`OrganizationMembership` 的直接查询、join、exists 和关联访问；清理用途显式区分，不能把仍保留的清理行当作授权。切换门槛包含这份消费者清单的逐项核对。
- `frontend/packages/web/components/profile/DeleteAccountDialog.tsx`、账号初始化／auth 状态恢复、`components/workspace-settings/WorkspaceDangerZone.tsx`、对应 core API／store 和 en／zh 文案：cleanup_pending 的持续进度、退避查询／重试与终态退出，刷新恢复删除状态；站点 `guides/account/profile.md` 同步说明。
- `api/routes/v1/conversations.py` 的 install 快捷分支、技能安装 service 和 checkpointer append 边界：同一 admission 保存非 run 操作回执及稳定合成消息 ID，重试对账而不重复安装／写历史。
- `repositories/attachment.py`、`services/attachments.py`：附件保护加入受理事务，孤儿清理不得凭旧扫描删除已受理附件。
- `streams/run_manager.py`、`streams/run_events.py`、`streams/recovery.py`、`streams/hitl_resume.py`：run 关联持久 admission，准备／模型／工具／resume 边界检查，不靠 Redis TTL 恢复执行权。
- `models/steering_message.py`、`repositories/steering_message.py`：用户输入绑定批次，旧未提交输入取消。
- `schedules/poller.py`、`schedules/dispatch.py`、`models/scheduled_task.py`、`triggers/pipeline.py`、`im/worker.py`、`im/run_handoff.py` 及现有队列 model／repository：首次排队时绑定来源，转交／重试保留，不能只在最后 start_run 时区分。
- `triggers/ingest.py`、`triggers/worker.py`（新）、`models/trigger.py`、`repositories/trigger.py`、`api/app.py`：可执行 trigger 事件持久排队、lease claim／过期接管、重试和启动恢复；进程内通知仅作加速，不作唯一消费者。
- `api/routes/v1/ws_triggers.py`：定义编辑与原 actor 快照串行；删除改为持久停用／删除标记及逐事件取消对账，不能丢失未决源事件。
- `services/scheduled_task.py`、`repositories/scheduled_task.py` 及 schedule API：删除定义与 occurrence claim／执行启动裁决共用源锁，取消未执行的已领取／busy／IM 项，保留未决 admission 与交接证明。
- `backend/alembic/versions/`：来源关联所需结构通过 autogenerate。

### Interfaces / core logic

服务端来源枚举固定为 `user_message | schedule_occurrence | trigger_occurrence | background_task`。后台 source ID 是 notice ID；schedule 使用既有 occurrence 行 ID；trigger 使用既有 event ID；IM 主动用户消息使用已去重 receipt 身份，调度／trigger 经 IM 转交仍保留原来源。Web 首次消息请求增加稳定 `client_message_id`，同一次发送重试保留它；用户 steering 复用 steer_id。用户 source_id 在服务端按 web／steer／IM 入口区分名称空间，防止不同入口的 ID 相撞。客户端不得提供内部 source_kind 或自选有效 generation。

首次受理与 Stop 锁定同一 conversation；受理记录持久存在后才能排队或取得 Redis active slot。fixed schedule 在 occurrence 首次持久领取时绑定批次，早于 worker dispatch；目标尚未确定的 new-each-time 流程在目标固定后、首次排队前绑定。相同来源键在不同 actor／目标下冲突，不能重新指定目标绕过旧绑定。

用户来源还持久绑定规范化请求摘要：正文、有序 attachment IDs、请求的 model_key／reasoning 及其他影响执行的提交字段。复用 client_message_id 等身份但请求不同返回冲突，不更改原 run；不把临时 URL、发送时间或重试时重新解析的默认模型算入摘要。C2 为 admission 增加 typed request_fingerprint 和 resolved_execution 字段，首次解析的模型／provider 选择、有效 reasoning 等不含凭据的执行设置必须与受理在同一事务提交，先于排队或 run 创建；run 从它构造，不依赖重试时重新解析默认值。已有受理重试仍校验当前权限／凭据，原选择不可用则明确失败，不静默换模型。没有可靠历史请求证明的旧受理不猜填摘要或执行快照。

同一首次受理事务还更新 conversation 的 model_key／reasoning，并验证附件 scope／actor／目标后将 pending 改为 attached，相关 repository 只 flush、不自行提前 commit。持有 conversation 锁按新受理顺序更新设置，已有来源的重试不写会话选择，防止旧重试覆盖新消息。附件清理必须与受理在同一附件行的锁／持久删除资格上串行裁决，扫描后重新验证资格再删除对象；受理已成功则不能删，清理先获删除权则受理拒绝并整体回滚，不能只在受理端加锁而让 reaper 继续按旧快照删除。

需要新 run 的受理先在事务中预分配并绑定稳定 run_id，再调用已有接受显式 run_id 的 RunManager.start_run；禁止启动成功后才补关联。run 的持久启动／结束证明参与相同来源重试对账，不依赖 Redis 存活时间；已经启动／完成则只返回原 run，不以同一 ID 再执行。启动前崩溃与启动后响应丢失分开恢复，未能证明原 attempt 无副作用时保持待对账，不换 ID 重放。

admission 区分 model_run 与 install 快捷操作，首次决定后不可改分支。快捷操作保存安装目标、已完成结果及稳定 user／assistant message ID；安装数据库变更与操作回执同事务，外部内容准备不在未保存回执时宣称完成。恢复先读回执和 checkpoint ID 证明，只追加缺失消息、重放原一轮 SSE 结果，不把无 run_id 当作可启动模型。已有副作用保留事实；执行前检查 Stop／删除／当前权限，活跃 slot 和 attempt fencing 同普通受理，不靠随机 fallback_run_id 重新取得执行权。

自动来源的内容也要固定：首次领取 schedule occurrence／trigger event 时，在源记录持久保存已渲染 prompt 及影响执行的非凭据参数、当次目标策略和模型选择；尚未固定 conversation 时先保存在源记录，确定目标后再绑定 admission。后续 dispatch／busy／IM 重试只读同一快照，定义编辑影响新 occurrence／event，不重新渲染旧事件；当前停用／权限／目标可用性检查保留，不能以快照绕过撤销。内部源事件的内容身份同样不可变。

源快照固定 actor：trigger 持久受理时在定义行锁内保存原 run_as_user_id，schedule 保留原 occurrence actor。定义编辑不改变旧事件身份；worker／IM handoff 重新校验原 actor 当前账户、成员资格和目标权限，不能读新定义替换执行人。

schedule 删除在源定义锁下设置 deleted_at、next_fire_at=None 并取消未执行 occurrence；claim、目标绑定和执行授权检查与删除串行。恢复 sweep／IM worker 不能仅凭冻结快照或旧 claim 执行。已有 admission／预分配 run ID／回执丢失先对账，持久记录取消结果；队列交接不算执行开始，尚未执行的 IM 项仍撤销。已确认启动的 run 保留历史，不以删除计划误停同会话其他工作；未决来源记录不能提前硬删。沿用既有锁顺序：有 workspace／conversation 锁时先取这些锁，再锁源定义和 occurrence，领取事务释放源锁后才进入目标受理事务，避免反向持锁。

账号删除隔离 A 的 attempt 后，逐条对账其他 actor B 的 steering。源输入身份与接收目标 attempt 分离，原 run_id 接收目标保留为投递历史；只有证明未提交且旧 attempt 不再可写，才在 slot 释放后以 B 当前权限预绑定唯一后继 run，不改变已有普通用户 run 的稳定绑定。提交未决保留待对账；已提交但处理中断的项保留 checkpoint 与中断状态，只允许 B 主动继续，不自动重试可能已有副作用的工作。C5 展示中断；C2 的接口／bootstrap 已提供这一事实。

账号及 workspace 删除契约前后端一起实现：409 cleanup_pending 进入不可撤销的清理进度，服务端删除标记是刷新恢复的权威；本人仅保留查询／重试删除的受限认证入口。前端有界退避、单次请求和手动重试，不持久保存确认密码；明确成功回执后才清 auth／workspace 或移除工作区。401 按登录失效处理，不显示删除成功；断网仍显示待确认。关闭弹窗不能恢复业务权限或撤销删除，页面重新挂载后继续查询。

删除状态的终态证明使用独立 deletion_operation：客户端在首个请求前生成 256-bit 随机 deletion_token 并存 sessionStorage，原删除鉴权／密码确认成功后只保存 token 摘要、固定 actor／目标 ID 和状态；这些 ID 不使用指向待删对象的级联 FK。相同 token 不得改目标。硬删除与 completed 回执同事务；worker 完成后也不丢回执。账号 `GET /api/v1/auth/deletion-status` 与 workspace `GET /api/v1/ws/{ws}/deletion-status` 是独立只读 handler，以专用请求头的 token 校验对应操作，后者同时核对固定 ws；不依赖目标 membership 仍存在、不授予任何其他权限、不返回 PII。日志脱敏该头，终态保留 30 天；404／凭证遗失明确不可确认并停止自动轮询。覆盖首个请求响应丢失、两次轮询之间 worker 完成、事务失败、跨目标 token、过期及刷新恢复。

移除／离开 workspace 和 org 成员撤销复用 actor 清理服务而非 bulk-delete membership：先持久撤销资格，鉴权、受理、工具边界及输入边界立即拒绝，再停止 scope 内旧工作。权限锁先于 conversation／sandbox／task 锁，旧成员行和句柄留到对账完成；清理未知返回 cleanup_pending，管理员或本人受限离开状态可恢复查询。org 路径覆盖其所属 workspace，重加成员不得消除旧 admission 的撤销事实。对活跃命令、排队输入、IM 身份识别／转交、组织与 workspace 分享读取／创建、重启、并发重新加入分别做真实 DB 测试：成员行仍在且 cleanup_pending 时也不得通过授权，既有最后管理员／owner 保护仍成立。

用户 steering 也持久绑定 actor：HTTP、准备缓冲、pub/sub、DB claim 和最终 Session.submit_input 全路径只向同 actor run 注入。B 到 A 的 run 时保持 queued（UI 显示等待当前运行结束），slot 释放后经同一受理门槛预绑定 B 的唯一后继 run；不改正文／身份、不复制消息。相同 ID 重试、用户撤回、Stop／权限撤销及已提交中断对账沿用现有规则，当前权限失效不能借 A 执行。自动 run 与普通 run 使用同一身份兼容规则。

TriggerEvent 增加与审计结果分离的持久 dispatch 状态、claim token／lease、next_attempt_at 及冻结内容。入口完成校验并持久排队后才返回 202 accepted；早先插入的去重／审计行不能被 worker 当作可执行事件，重复未完成请求要恢复入口裁决或明确失败。worker 启动及定期扫描只领取已排队／可接管的过期 claim，沿用同一事件身份和有界重试；旧 owner 失权后不能更新状态。new-each-time 的 conversation 创建与目标绑定同一事务，后续 run 启动／IM outbox 沿用 admission 幂等与回执对账，不重复新建目标或执行。注册 app lifespan 并移除仅靠 create_task 的受理后执行路径；过滤、限流和当前权限检查保留，不扩充为通用调度系统。

trigger 删除与受理／claim 锁定同一定义后再锁事件：持久 disabled／deleted 标记并取消未交接事件，普通列表／详情隐藏，保留事件及绑定关系直到未决 run／IM handoff 对账完成。删除后的 drainer 只能取消／对账，不能继续新建目标或执行；已交接 run 的历史保留，不取消共享会话的其他工作。没有未决交接／清理后才允许物理删除，凭据撤销不删除恢复证明。

Stop 之前已经受理的来源保持旧 generation；之后的新用户请求或独立授权的新 occurrence 才能开启下一批。重新开批不取消旧清理、不放行旧 notice。新 active-slot claim 后和每个副作用前仍复查 generation；两套存储不宣称原子提交。

主 Stop 请求 `{execution_generation}`；202 返回 `{execution_generation, accepted, cleanup_pending}`，成功表示持久控制已关闭，不表示远端退出。单 task Stop 不关闭 conversation。恢复与重复旧 Stop 不能作用于新批次。

删除入口复用控制 service：软删除、关批次、记录 task 停止／通知取消在同一事务，提交后再通知 run／HITL／输入取消。API 沿用既有删除返回语义；coordinator 绕过“普通用户可见行”过滤只为清理已受理工作，仍校验原 scope／实例，不新增宽权限用户接口。保留句柄与对账证明；删除后任何来源均不得重开该会话。

workspace 删除先持久标记 deleting，串行关闭受理／调度并登记所属会话 Stop；标记由恢复清理扫描消费。未完成真实停止及输入对账时返回 409 cleanup_pending，保留 scope／成员／provider 句柄和事件证据，重试可继续。确认完成后按 event → command 详情 → task（后代先于父）→ admission 的外键顺序删除，再执行既有 workspace 子表清理；不能仅扩展 bulk-delete 列表就丢弃活进程。仅 admission、没有 task 的 workspace 同样覆盖。所有受理路径遵循 workspace → conversation → sandbox → task 的外层锁序，已有 coordinator 不反向取 workspace 锁；组织级入口共用删除门槛。

账号删除采用同一先撤权／清理、后硬删门槛，但以 actor 而非 workspace 定位：持久删除标记先阻止新用户／自动受理，固定所有 scope 的原发起身份，保留清理所需 user／membership／凭据／事件／checkpoint。本人拥有的会话按现有删除范围关闭；他人共享会话仅停止该 actor 的工作，不关闭整个 generation 或取消其他人的 run。未决返回 409 cleanup_pending，后台恢复扫描继续；仅本人查询／重试删除仍可认证，其他业务入口拒绝。全部对账后先按依赖删除该 actor 的 lifecycle 行，再执行既有账号清理；不能从原 auth 路径提前 bulk-delete TriggerEvent、ScheduledTaskRun、IM receipt 或 checkpoint。受理与删除资格检查串行，跨 scope 清理按稳定 ID 排序取得锁，不把普通 workspace service 提升为任意用户可调的全局接口。

### Tests / docs

- 新 `backend/tests/e2e/test_conversation_execution_control.py`：无 active run、有 starting reservation、paused HITL、旧批次 cleanup 场景；停止与首次受理／checkpoint／新消息的双向 barrier。
- 删除事务失败不隐藏会话；提交后 worker 崩溃仍能清理，迟到句柄不丢、deleted API 404、其他会话不受影响。
- 调整 `backend/tests/e2e/test_scheduled_tasks_firing.py`、`test_scheduled_task_destinations.py`：fixed 新 occurrence 可运行；旧 busy／IM 重试不可换批；Stop 与首次领取竞争有唯一结果。
- Web 消息重试、IM receipt 重试、trigger 重试不重新授权；伪造内部来源、跨 scope／actor 复用键失败；原调度权限和 HITL 限制不回归。
- 相同用户来源 ID 改正文／attachments／model_key／reasoning 逐项拒绝；相同 payload 重试仍返回原 run 和 generation。用 barrier 在 admission 提交后、run 创建前模拟崩溃，改变默认模型／reasoning 后重试仍使用首次快照；并发受理只有一个胜出的快照，事务失败不留下缺少快照的新受理，权限撤销／原模型不可用不静默重选。
- 用户 start_run 已成功但返回／关联写入边界断开，等原 run 结束并清除 Redis active slot 后重试，仍只有原 run 和一份模型／工具副作用；并发首次受理只有一个稳定 run ID。
- 受理提交后、run 创建前崩溃：自动 notice 仍使用新会话选择；旧请求重试不能覆盖后来消息的设置。推进附件 orphan TTL 并执行真实清理，已受理附件仍可读；清理／受理双向 barrier、事务回滚及跨 actor 附件复用均验证，不只测 attached 字段存在。
- occurrence／event 已领取后修改 prompt／template／目标策略，再 busy 或 IM 重试，仍执行原快照；新来源采用新定义。覆盖 new-each-time 目标确定前崩溃，以及当前停用／撤销权限不被快照绕过。
- 新 trigger 恢复 E2E：202 后、目标解析前停止 worker，再创建新 worker，断言同一事件最终启动且只有一个目标／受理。覆盖双 worker claim、lease 到期、目标提交后崩溃、start_run 响应丢失及 IM outbox 重试；过滤失败、限流拒绝、未完成入口校验和停用事件不得被扫描执行。
- trigger 202 后编辑 actor，重试仍使用原身份；撤销原 actor 权限则失败，不借新 actor 继续。删除分别与 claim、目标绑定、run／IM handoff 回执丢失竞争，源证明保留、无重复或新执行，已交接历史不丢。
- workspace 只有普通 admission、含运行任务、含已提交／未提交事件三类删除 E2E；清理失败／unknown 返回 cleanup_pending 且句柄仍在，worker 重启继续；清理完成后无 FK 错误，跨 workspace 不受影响。并发新受理不能越过 deleting 标记，删除事务失败可重试。
- 账号删除覆盖 admission-only、本人会话活任务、在他人共享会话发起的任务／通知、待交接 trigger／IM、清理失败和 worker 重启；保留恢复证明且不取消其他参与者独立工作，完成后无新 FK 阻塞。另覆盖 B 向 A 活跃 run 投递 queued／提交在途／committed 输入时删除 A：未提交只交付一次，已提交保留中断事实不自动重放，状态未知保持对账，后继只能用 B 的当前权限。
- schedule 删除与 claim、busy 重试、目标绑定、IM 入队及执行／回执丢失双向 barrier 测试；未执行项取消且重启不复活，已执行历史保留，同会话其他任务不受影响。
- 前端业务流测试账号及 workspace 删除：cleanup_pending → 断网／重试 → 刷新恢复 → 明确成功后退出／移除；401 不误报成功，关闭弹窗不能撤销删除。验证服务端拒绝新业务而允许本人删除状态查询，不用静态文案存在测试代替。
- install 快捷操作分别在安装事务提交后、checkpoint append 后丢失响应，再用同一 client_message_id 重试：只有一次安装／一组稳定消息，原 SSE 结果可重放，无模型 run；Stop／当前权限失效阻止尚未执行的操作。
- 同 PR 更新站点 `guides/conversations/basics.md`、`sandboxes.md`、`guides/automation/scheduled-tasks.md` 及 `guides/account/profile.md` 的 Stop／删除／清理进度与下一次独立触发语义。

## C3. 每条 notice 独立投递与确认

### Files

- `services/background_task_delivery.py`（新）、C1 的 task/event repository：从现 command coordinator 提取现有 wake 路由，不新增第二条消息管线。
- `streams/steering_delivery.py`、`streams/execution_adapter.py`、`streams/run_manager.py`：输入来源、稳定 delivery ID、checkpoint ack、取消与 attempt fencing。
- `models/steering_message.py`、`repositories/steering_message.py`、`agents/schemas.py`：持久 source／notice／generation，后台事件与用户 steering 的实时投影分流。
- `api/routes/v1/conversations.py` 的 bootstrap／历史读取：旧内部输入关联源 notice 归类，不回写 checkpoint。
- `streams/run_manager.py` 的 reflection／consolidation 调度、`services/reflection_runner.py` 与 `services/memory_consolidation.py` 的来源契约：含后台 notice 的自动／混合轮次跳过 reflection；consolidation 在历史窗口裁剪与 `_render_history` 前过滤这些轮次及其回复，正常纯用户历史不变。

### Interfaces / core logic

`BackgroundTaskNotice {source: background_task, notice_id, task_id, task_kind, originating_run_id, execution_generation, reason, summary, result_ref}`。command 详情可选；权限主体来自持久发起身份，不从消息角色推断。每条 notice 单独映射初始输入或 InputEnvelope，不跨 notice 拼接一条可撤回输入。

有 active run 且其 RunContext／持久 admission 的 actor 与源 task.started_by_user_id 相同，才在安全输入边界接收；其他 actor 的 run 或身份无法可靠对账时保持 pending，不能仅因 conversation／generation 相同就借用其凭据。准备／收尾不能接收时保留 pending；无 active run 且没有持久 HITL 才走 C2 的旧工作受理校验和现有 active-slot claim，并以源 actor 构建 RunContext。同一调度的多个同 actor 就绪 notice 可进入同一 run；不保证一次模型调用，buffer 满时保留未投递项，不丢弃也不忙等。

只凭 InputCommitted(checkpoint) 或同 notice ID 的持久历史证明确认 delivered。初始消息不能因 start_run 返回成功就 ACK。提交证明已存在时不因后续模型失败再次投递；事件显示“已送达”而非“业务已完成”。

A/B 独立输入时停止 A 只撤回 A，B 不变。取消返回 closed、响应丢失或 checkpoint 在途，先对账；确认原 attempt 不能再提交且历史无证据才重路由。用户停止／删除取消通知，deadline 仍可报告超时；通知取消后观察迟到终态只保存事实。

初始 notice 不在 Session 的 cancel_input 队列中，需单独的宿主路径：投递记录绑定首条 notice／attempt；准备阶段及 Session 执行前检查源 task 的通知权限。初始输入持久提交未决时，其他 notice 和用户追加输入留在各自持久队列，不向 Session 提交。初始提交确认后开放其他输入，与首条取消裁决在同一 attempt 绑定上串行：取消先获权则保持入口关闭、取消 attempt 并对账 A，B 仍 pending 可重路由；提交确认先开放入口则单 task Stop 不再取消这个共享 attempt，避免中断已提交 B。已提交 A 保留历史，未提交且 attempt 已失权才 discard；主 Stop 仍可停整个批次。复用 RunManager 准备任务取消与既有 attempt fencing，不扩充 CubeLoop Session API，也不引入 delivered 通知的自动业务重试。

`pending_steers` 和用户 steering SSE 只返回 source=user。后台输出作为有来源的任务数据，不作用户指令或审批答案；不改稳定系统 prompt、不补旧 ToolResult、不修改历史 metadata。

该来源规则贯穿副作用消费者：首条或本轮已提交输入含 background_task 时跳过自动个人记忆 reflection，不把命令日志当作用户偏好／纠正；本轮先保守跳过整个混合轮次，不建设新记忆系统。consolidation 不能只在当前 run 的调度点跳过：以后由普通用户 run 触发时，仍须先按持久输入来源及 run 关联排除含后台 notice 的完整轮次，再裁剪 HISTORY_MSG_CAP 和转成文本，避免留下孤立的后台回复。旧输入使用可靠 wake 关联，无法可靠分类的片段不用于自动记忆，不回写 checkpoint。全部被排除时不调用提炼模型、不写个人／workspace memory，仍按已有 cutoff／consumed 高水位规则结束扫描，不能重置或消费扫描期间新增 run 的计数。

### Tests / docs

- 新 `backend/tests/e2e/test_background_task_delivery.py`：真实 Session／Postgres／Redis，active／idle／preparing／finishing／HITL 路由，start_run 响应丢失、worker 重启、claim 接管。
- A、B 同 run 排队 → Stop A 与 InputCommitted 竞争 → B 恰有一份持久输入；A 已提交保留历史、未提交不续办，closed 不被当成取消成功。
- 首条 A 已 claim、run 尚在准备 → B 保留独立投递 → Stop A：用 barrier 验证 A 不进入模型且 B 最终只提交一次；另覆盖初始 checkpoint 在途、已提交、取消响应丢失和 worker 崩溃后的逐 notice 对账。
- A 初始提交／取消裁决双向 barrier：其他输入在未决期间不能提交；B 已提交后 Stop A 不结束共享 attempt，模型可继续处理 B。纯后台与用户＋后台混合轮次均不调度自动个人记忆 reflection，普通用户轮次仍正常。
- 真实 checkpoint 历史含纯用户、后台、混合轮次及窗口边界的后台回复：后续普通用户 run 触发 consolidation 时，自动结果及其回复不进入提炼输入；个人／workspace memory 不受其影响，纯用户历史正常合并。全部被过滤时不调用模型；用并发 barrier 验证只扣除本次已扫描计数，后到 run 仍能触发后续合并；失败重试沿用相同过滤。
- 旧 generation 的 completion／monitor line／调度重试不复活；多个投递 owner 不重复确认；权限失效 discarded，不以 system actor 兜底。
- 两参与者共享会话：A 任务结果到达时 B 正在运行，B 的 Session／模型／工具不收到 A 的输入；B 结束后 A 结果以 A 身份运行且仅提交一次。期间撤销 A 权限则 discarded，禁止借 B 的凭据／个人上下文继续；同 actor 多 notice 合并 run 仍有效。
- 更新 `test_steer_endpoint.py` 和历史投影用例：内部 queued／failed 不进入用户列表，真实用户撤回／恢复正常，旧 checkpoint 不被重写。
- 现有站点 sandbox 页同步说明后台事件与用户 steering 分离。

## R. CubeLoop Todo 等待声明，小范围上游扩展

这一单元在 CubeLoop 仓库独立 PR 交付；不是修改本地依赖副本。源代码以 `/home/chris/cubepi` 为核对位置，实施需遵守上游 worktree／review 流程。

### Files / interface

- `cubeloop/middleware/todo.py`：`WriteTodosInput.wait_for_tasks: list[str] = []`；保持普通 payload 约束和已完成状态规则。
- `TodoListMiddleware` 增加可选宿主异步等待校验回调：输入 task ID 列表、当前 AgentContext 与先前校验绑定，返回 valid／cancelled／invalid(reason)；未配置时保持旧 guard 行为，不接受未经校验的绕过。
- Todo extra 保存 task IDs、对应 Todo 快照／声明时输入边界；普通更新不带等待则清空。新用户或相关结果提交后失效，包括新 Session 的初始输入，而非只监听 live steering。
- `tests/middleware/test_todo_wait.py`（新）、现有 Session checkpoint 测试；`website/docs/guides/middleware/todo.md` 更新说明与示例。
- `cubeloop/agent/agent.py` 的现有事件持久化：工具轮次结束及 HITL 暂停先保存 extra，再发布事件，避免等待声明只在正常结束才落盘；写入失败标记 checkpoint 不一致，不发布成功暂停。不是增加 Session 生命周期或等待状态。

### Core logic / tests

等待声明只豁免 unfinished＋纯文本导致的强制续跑，不豁免 payload 错误、显式 stop、HITL 或其他中间件终止条件；无 Todo 不要求额外工具调用。声明创建时校验并 checkpoint 成功绑定，含 Todo／输入边界；自然收尾重新校验，已终态但结果 pending 可等待，通知已取消不可新建等待。先前有效的声明若后来被用户取消，且所有依赖均仍有效或有随后取消的持久事实，返回 cancelled 并正常结束本轮、保留未完成 Todo 及用户取消原因，不强制模型再跑一次。新输入使绑定失效；无效 ID、权限不明、校验故障不能返回 cancelled，不产生新通知或新 run。

CubeLoop 不知道 CubePlex 表、command、MCP 或 sandbox；任务存在性和 scope 留给宿主。使用 FauxProvider、真实中间件与 checkpointer 验证旧行为不变、合法收尾、新输入失效、checkpoint reload、校验故障不能静默放行；不增加 Session 等待阶段。

## C4. 宿主工具交接与 Todo 收尾集成

### Files

- `backend/cubeplex/middleware/sandbox.py`、`prompts/sandbox.py`：工具调用公共 service，明确 task ID／command ID、deadline、日志和通知策略。
- `backend/cubeplex/middleware/background_task_wait.py`（新）：宿主等待校验与 input 身份解释，平台查询不进入上游 Todo。
- `backend/cubeplex/streams/run_manager.py`、`middleware/_compose.py`、`agents/graph.py`：绑定 R 的公共回调和 live state_context，移除 run-end 长等待、重复 polling／finalize kill。
- 通过 `uv add` 更新已发布的 CubeLoop 依赖与 `uv.lock`；不手改 pyproject 或 .venv。

### Core logic / tests

前台最多等待 15 秒，已交付最终可读结果不发 completion；超时仅交后台，原 run 给进度后正常 Done。进程已终态但输出仍在恢复时，也可交后台并返回真实执行状态与 result_pending，不能返回仍在执行或声称已交付完整结果。正常完成／非用户失败不撤销受理任务，主 Stop／删除走 C2。前台 owner 崩溃按 C1 恢复，不复活旧 Session。

wait_for_tasks 只接受本 conversation／有效批次、后台可观察、通知仍有效且有待交付结果的公共 task ID。notify=false server、失去恢复来源、用户取消的任务不能建立新的等待许可；deadline 待报告结果可以。先前有效声明随后被取消走 R 的 cancelled 收尾，不冒充仍有结果会来。工具 schema 和静态说明统一发布，动态 task 状态不改 prompt 前缀。

- 新 `backend/tests/e2e/test_background_task_run_flow.py`：脚本化最外层模型＋真实宿主，构建运行 → 保留 Todo → 原 run Done／slot 释放 → 模拟小时推进及 coordinator 重启 → 结果入新 run → 验证业务结果。
- 等待期没有输入／事件则模型调用数不增长；current run 可接收通知；用户新输入使旧等待失效；prompt 与 respond 均正确恢复 extra。
- 前台终态结果与交接竞态只交付一次；同步 child 发起的 command 同样受管而不改 child 执行模式。
- 单 task Stop 与自然收尾复查的双向 barrier：先前有效的等待通过 cancelled 收尾，不增加模型调用、不完成 Todo、不杀共享 run 的其他输入／同级任务。Stop 后才声明的任务、无效 task ID、新输入失效及查询错误均不能得到这项许可。
- 运行现有 prompt-cache、唯一 Done、required consumer 故障和 HITL 回归；`real_llm` nightly 另检查模型不主动 ps/sleep 忙等，确定性测试不冒充真实模型行为。
- 更新站点 sandbox 页的“等待时本轮已结束、结果自动续办”，明确不等于 Todo 已成功完成。

## C5. 公共 API、三类展示和完整发现协议

### Files

- `backend/cubeplex/api/routes/v1/background_tasks.py`、`api/schemas/background_tasks.py`（新）、`api/routes/v1/__init__.py`、`api/app.py`：workspace conversation 路由；移除旧 sandbox-commands 控制路由及调用方。
- `backend/cubeplex/api/routes/v1/conversations.py`、`api/schemas/conversations.py`、公共 task repository：权威 summary、事件分页和历史分类。
- `frontend/packages/core/src/api/backgroundTasks.ts`、`types/background-task.ts`（新），`api/runStreams.ts`、`types/events.ts`、`stores/messageStore.ts`：typed API、source、独立任务／事件状态和后台刷新。
- `frontend/packages/web/components/layout/InputBar.tsx`、`PendingSteers.tsx`、`components/chat/MessageList.tsx`、`TaskProgressCard.tsx`、`components/panel/sandbox/SandboxTerminalView.tsx`：Stop 可用性、任务区域与紧凑结果行，复用现有详情组件。
- `frontend/packages/web/messages/en.json`、`zh.json`；站点 `guides/conversations/sandboxes.md` 和 `basics.md` 同 PR 更新。

### API 契约

路径前缀均为 `/api/v1/ws/{workspace_id}/conversations/{conversation_id}`。

| 请求 | 结果／规则 |
| --- | --- |
| GET `/background-tasks` | 默认 inflight；可按有界 task IDs 查询终态快照，不触发 provider I/O |
| GET `/background-tasks/{task_id}` | task 公共字段、类型详情、能力、通知／停止状态和 revision |
| POST `/background-tasks/{task_id}/stop` | 202 持久受理但未确认；200 已终态原事实；都执行通知取消。响应区分本地受理、远端可取消与执行确认 |
| GET `/background-task-events` | `delivery=pending|all`，默认 pending；cursor 分页，limit 默认 50／上限 100；返回 items／next_cursor／has_more |
| 现有 bootstrap | 增加 execution_generation、停止状态及 `background_summary {has_inflight, has_pending, has_cleanup, can_stop}` 和事件首页 |
| 现有 cancel／消息请求 | C2 的目标 generation／稳定 client_message_id；前后端一起切换，不接受缺字段绕过控制 |

pending 与 has_pending 仅计算 state ∈ {pending, claimed}（包括这些状态下等待重试／对账的事件）；明确排除 delivered 和 discarded，二者只进入 delivery=all／历史。分页按不可变 created_at、notice_id。cursor 绑定 scope／conversation／过滤条件，拒绝跨上下文复用。summary 在同一 DB 读取快照中从完整可访问集合计算；当前页之外的待处理也计入，查询错误不能回 false。has_cleanup 包括停止未确认和日志收尾；can_stop 只表达后台工作仍可撤销的权限，不保存第二份 run 状态。Redis active-run 另按现有协议对账，不假装与 DB 原子。

### UI / core logic

- 用户追加输入仍在 steering 列表，后台 task 每项一条，结果按 notice ID 紧凑显示；同 notice 的历史输入与源事件合并，不能再生成用户气泡。
- Terminal 保留 command 详情，普通公共列表不要求 exit code／sandbox 字段。终态但通知 pending 可从“待处理”分页进入 task 详情并停止，不只靠 inflight 列表或历史 tool result。
- 每次冷刷新先 bootstrap；summary 任一 has_* 为真就保持有界低频刷新，can_stop 与 active run／HITL 状态一起决定主 Stop 可用性。空页／失败／旧响应不能清掉已知 pending，切换会话取消旧请求并隔离 scope。
- 原 SSE 已结束时仍发现后续 run；其在两次检查间快速结束也能从历史显示。summary 全清且最终历史／active-run 对账完成后才停止后台轮询；页隐藏降频，回到页面重建。
- Stop 受理前不移除卡片，202 显示等待确认，未知／取消不支持明确显示；停止后未完成 Todo 不勾成功。事件折叠不发取消请求。
- 本期请求／事件每条独立；UI 的 monitor 输出聚合不改变投递身份。日志作为原始文本，不作可信 HTML／Markdown 指令。

### Tests / docs

- 新 `backend/tests/e2e/test_background_task_api.py`：scope／actor、只读性、typed 200/202、全部 task 终态但 pending 超过两页、cursor 隔离、summary 与记录一致。
- 新 `frontend/packages/web/__tests__/e2e/background-tasks.spec.ts`：用户 steer 与后台结果同时到达 → 重试／冷刷新仍分开 → 单任务停止 → 主 Stop → 迟到 completion 不续办。
- 超过窗口的 pending 可分页发现和取消；查询失败不当无事可做；所有 task 终态仍能发现自动回复。A 停止后 B 继续，不以元素计数当验收。
- 扩展既有 steering／messageStore 业务流测试，保留 SSE ownership、窄屏、键盘、i18n 和主题。先构建 `@cubeplex/core` 再验证 web，不改压缩／CSRF／代理规则。
- 截图若暂缺，在匹配站点页面留明确 placeholder，不省略用户文档。

## C6. 命令日志确认独立于进程状态

### Files / interface

- `backend/cubeplex/sandbox/log_io.py`（新）、`base.py`、`opensandbox.py`、`local.py`：poll 返回候选 cursor，日志写入确认后才 ack；限定目录下区分 write 与 cleanup。
- C1 的 command adapter／task service：原实例绑定、fenced cursor、终态后的日志重试；coordinator 支持 terminal＋log retrying，不依赖 inflight 查询。
- `append_output(path, data) -> {data_written, cleanup_done}`；write 失败不 ack，cleanup-only 失败不重放已确认输出。

### Core logic / tests

固定内部目录以运行用户可写权限准备；拒绝 symlink／非目录，不递归 chown 工作区、不以 root 跟随代理可控路径。只清理本次明确生成的临时片段，不删除现场 orphan。

执行可先终态，completion／monitor exit 源事件原子保存为 pending，但依赖尾部输出的事件在最终日志可读前不允许 claim／提交给模型。公共投递层读取适配器提供的结果就绪事实，不要求所有 task 都有日志。保留句柄与旧 cursor，在原实例继续收集；可靠不可恢复时标为 unavailable 并交付明确不完整的最终结果，临时错误继续重试。cleanup-only 失败不阻挡可读结果。日志恢复只解锁原事件，不生成第二次 completion；停止／删除后的通知取消仍优先。写入成功而 DB cursor 未提交可重复片段，不为去重跳过未知数据。

- unit 覆盖 write／cleanup 分类和路径校验；e2e 用真实 DB 加执行方外边界故障验证进程终态、重启后继续收集、cursor fencing。
- 进程退出、日志写入失败、原 run 结束、coordinator 重启后日志才成功：恢复前模型不消费 completion，恢复后只有一次通知且能读到完整输出。另测不可恢复的明确不完整结果、cleanup-only 不阻挡、日志恢复与 Stop 竞争不唤醒；前台 15 秒内终态但日志未就绪同样交后台。
- 真实 OpenSandbox 用唯一输出片段检查普通用户写入／清理和 symlink 拒绝；外部服务不具备条件时具名 skip，不用假服务冒充 E2E。
- 同 PR 更新 sandbox 用户文档中的日志不完整状态。AsyncSession 用例只放 e2e，现有误分类测试在实质改写时迁入正确目录。

## 2. 迁移、切换与验证门槛

1. 在隔离测试库验证结构和数据迁移。新增表／字段、旧字段退出分别由模型 metadata 经 `alembic revision --autogenerate` 产生；不手写／改写结构 migration。新增表承接原记录是数据复制，不依赖 autogenerate 自动识别 rename。新增结构阶段 task_id 等待回填字段允许为空，旧字段／表保留；回填验证后才生成／应用收紧约束和删除旧结构的阶段，未知历史实例不靠伪造值满足约束。
2. 数据搬迁工具计划放 `backend/scripts/dev/migrate_background_tasks.py`，默认只读 dry-run。列出旧 inflight、run／conversation lifetime、已删除会话、notice／checkpoint 证明、实例证据和未知 deadline；实际执行需独立授权。
3. 经授权隔离所有旧写入者（API／run worker／coordinator／相关排队入口），记录 checkpoint／provider 句柄和旧新 ID 映射，再回填 task、事件、admission。仍在运行的旧 run-lifetime 命令先完成或明确停止，不能直接授予跨 run 权限。
4. 每个 command 恰好一条 task；notice ID、去重键、投递证明保留。旧 terminal command 的 notice_state=pending 但 completion wake 尚未创建时，按旧 completion 去重键及 checkpoint notice／command 证明对账：已送达不重放，明确有通知权的 conversation-lifetime 工作幂等补一条稳定事件，无权继续的 run-lifetime／取消／证据不明记录保留 discarded 原因。只从可靠原始证据回填实例和受理批次；未知不猜当前容器、不重跑、不释放名额、不回放旧通知。已删除会话补持久清理意图，不能恢复可见性或执行权。
5. 回填可中断重跑、重复执行不增任务／事件。核对行数、唯一性、scope、终态事实与 pending 分类，失败停止切换。唯一合法升级顺序为：停旧写入者 → 升到新增结构的指定 revision → 回填并核对 → 删除旧结构／收紧约束 → 启动新 service，最终只有一个生命周期写入者；无双写同步或旧控制路由转发层。
6. 新旧客户端／API 配套切换。中间 PR 不直接投产，不能在 Stop／通知校验尚未齐备时启用新的跨 run 行为。切换后若回退，不让旧 worker 对新 schema／新权限语义盲写；回退与备份恢复需单独审核。
7. 开发按 [testing](../../testing.md) 的 red→green 保护各单元契约，只运行改动模块；联调再跑相关 E2E。Postgres／Redis／FastAPI 用真实服务，只在最外层注入故障；小时级时间用可控时钟、barrier 和有界状态等待，不真等几小时。
8. C4/C5 完成后验证唯一 Done、prompt cache、HITL、SSE 结束后新 run、原现场业务流程。保留每项命令与结果；不把文档检查当运行时验收。噪声测试输出写入 gitignored tmp 日志，失败先读 traceback，不重复盲跑。
9. 用户可见改动与匹配站点文档同 PR；常规 push 由 pre-push 运行对应 check-ci，不提前重复手跑。用户已批准实施；实际发布及线上数据操作仍需独立审批。

迁移门槛另作为切换实施项：`deploy/kubernetes/charts/cubeplex/templates/backend-deployment.yaml` 的 init 和 `deploy/docker-compose/compose.yaml` 的 backend-migrate 使用同一个有门槛的升级入口，禁止无条件 `alembic upgrade head`。已有库必须通过独立、串行的维护步骤停旧 API／worker／coordinator／排队入口并确认退出，再由持有数据库迁移锁的唯一执行者升级到新增结构 revision、回填核对、升级最终 head；不能在 RollingUpdate 新 pod 的 init 内假定旧 pod 已停止，也不能让多副本各自回填。普通启动只检查已完成的切换，空库可在迁移锁内完成安装；后端拒绝未完成回填的新生命周期写入者。C1 只交付新增结构阶段，不提前删除旧字段；最终删除阶段在 C2–C6 完整验证、门槛就绪后交付。新增 `backend/tests/e2e/test_background_task_migration.py` 验证旧数据保全、pending completion 缺 wake 的幂等补齐、回填中断重跑、核对失败阻止收缩和启动、空库安装、多个升级者争锁；Helm／Compose 启动入口都覆盖拒绝未切换旧库的契约，现有 Kubernetes／Compose 部署文档同步维护步骤。实际发布操作仍另行审批。

## 3. Spec 覆盖与完成定义

| Spec 验收编号 | 负责单元／关键验证 |
| --- | --- |
| 1、2 | C1、C4：run 可结束、小时级观察、原实例接管而非重做 |
| 3、4 | C2、C3：active／idle／HITL 路由与唯一 active slot |
| 5 | C1、C4：前台终态与后台交接唯一结果路径 |
| 6 | R、C4：合法等待与新输入失效，不强制续跑或伪造完成 |
| 7、8、13、14 | C2、C3：Stop 全范围、旧通知不复活、单任务控制与竞争 |
| 9、17、18 | C1、C2、C3：单一事实、能力、scope、deadline 与停止证据 |
| 10 | C6：日志确认与清理分离、终态后重试 |
| 11、15 | C3、C5：来源分流、恢复、无旧 SSE 仍发现回复 |
| 12 | R、C3、C4、C5：checkpoint、cache、required consumer、Done |
| 16 | C3、C5：独立 notice 输入，A 停止不影响 B；展示聚合不改变身份 |
| 19 | C1、R、C4：不改普通 MCP／同步 subagent，不交付假 detach |
| 20 | C2：删除与 reservation／迟到句柄竞争及崩溃后清理 |
| 21 | C2：fixed schedule 新 occurrence 与旧 busy／IM 重试区别 |
| 22 | C1、C6：sandbox 原地 revive 后不误用新实例，未知不猜填 |
| 23 | C5：窗口外 pending 冷刷新发现、分页控制与最终对账 |
| 24 | C3：首条 notice 准备期取消、初始 checkpoint 竞争及 B 独立交付 |
| 25 | 第 2 节切换：新增结构／回填／删除旧结构门槛与启动拒绝 |
| 26 | C3：后台／混合轮次不触发 reflection，后续 consolidation 也过滤这些历史及回复 |
| 27 | C2：请求摘要冲突拒绝、受理事务持久保存首次执行快照及 run 创建前崩溃恢复 |
| 28 | C2：会话选择／附件与受理原子提交，旧重试不覆盖新选择，reaper 竞争不删已受理附件 |
| 29 | C2：自动来源首次领取固定内容，定义变更不改旧重试，当前权限仍重查 |
| 30 | R、C4：先前有效等待的用户取消收尾，不强制续跑或冒用旧许可 |
| 31 | C2：trigger 持久消费与崩溃恢复，入口资格、唯一目标及转交幂等 |
| 32 | C2：trigger 删除与 claim／handoff 串行，保留未决源证明 |
| 33 | C2：自动来源冻结 actor，当前权限重查且不替换身份 |
| 34 | C2：workspace 删除持久关闭受理、清理后按 FK 顺序删除、失败可恢复 |
| 35 | C2：run ID 启动前持久绑定，成功响应丢失后重试无第二次执行 |
| 36 | C2：账号删除以原 actor 跨 scope 停止／对账，再清理外键，不取消其他参与者独立工作 |
| 37 | C3：后台续办仅进入同 actor Session，否则等待原身份的新 run |
| 38 | C2：install 快捷操作的持久回执、稳定消息 ID 和响应丢失恢复 |
| 39 | C2、C5：共享 run 中其他 actor 的未提交输入重新路由，已提交处理被中断时保留事实且不自动重放 |
| 40 | C2：账号／workspace 删除清理进度、受限认证、刷新恢复与明确终态退出 |
| 41 | C2：schedule 删除与已领取 occurrence／IM 交接串行并保留未决证明 |
| 42 | C2：删除回执跨硬删除保留，首请求前持有只读 token，worker 完成／响应丢失／过期可确认处理 |
| 43 | C2、C3、C5：所有用户输入投递边界检查 actor，其他 actor 排队后独立受理并在 UI 区分 |
| 44 | C2：成员撤销先持久撤权再停止／对账，重启及重新加入不恢复旧执行权 |
| 45 | C1、C4：配置／工具参数共用正整数及 32-bit 秒数上限，受理防御日期溢出 |

review 五项分别落到 C2（删除／调度）、C1（实例身份）、C3（独立输入）、C5（完整发现）。完成定义是这些不变量及业务流有实际验证证据，不是按五个 finding 各改一段文字，也不是通过静态 UI 数量检查。
