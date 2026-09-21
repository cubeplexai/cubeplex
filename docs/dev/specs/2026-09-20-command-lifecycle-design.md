# 后台任务生命周期：公共管理，command 作为首个实现

- 状态：按用户确认的方向及 review 修订；公共后台任务生命周期、command／monitor 首个实现。2026-09-21 用户授权补齐首条通知取消与迁移门槛后开始实现；本文仍是目标契约，不表示功能已实现，不授权部署或线上数据操作。
- 日期：2026-09-20；更新：2026-09-21（America/Phoenix）。保留原文件路径供已有链接引用。
- 核对基线：CubePlex `f622d97e`，CubeLoop `65ff7096ca2e78e707bdd1d96f8dbca38ef5a0cc`。
- 关联：[原 managed commands 设计](2026-09-18-sandbox-background-execute-design.md)、[lifecycle 实施计划](../plans/2026-09-20-command-lifecycle.md)。
- 本版取代本草案之前的 CubeLoop live-run 等待建议，该提案不再是本方案的依赖；配套 plan 已按本版重排并获用户确认。

## 1. 核心决定

**后台任务属于 conversation，run 只是某一轮模型执行。等待几小时不需要让发起它的 run 一直活着。**

后台任务没有结果时，模型做完当前能做的事，说明进度，然后正常结束 run。CubePlex 的后台任务管理层继续观察和控制具体执行。任务有结果且未被用户停止时，向所属 conversation 投递后台事件：有可接收输入的 run 就通过内部 steering 注入；没有活跃 run 就发起新一轮 automated run。

**复用输入机制，不复用用户发言的身份与界面。** 后台事件必须有明确来源和稳定事件 ID，不进入输入框上方的用户 steering 列表。聊天主“停止”撤销本会话当前执行和自动唤醒权限，并对受管后台任务请求停止；只有执行方确认后才显示底层工作已停止。正常结束 run 与用户主动停止不是同一件事。

新增的是一层薄的公共生命周期管理，不是通用 Job/Workflow 平台，也不是把所有工具改成后台执行。它统一归属、停止、结果通知、恢复、Todo 等待和 UI；执行细节仍属于具体适配器。本轮只接入现有 command／monitor，不实现 MCP 长任务适配或 detached subagent。复用现有 command 的管理逻辑、wake outbox 投递协议、durable steering、conversation checkpoint 和 one-active-run 协调，不增加挂起／恢复旧 Session 的协议。

这里的 `BackgroundTask` 是一次受管执行，不是 Todo 条目，也不是定时任务配置。普通短工具调用继续直接返回工具结果；需要跨 run 管理且具备明确执行句柄与观察方式的工作才登记为后台任务。它可以先前台等待再交给后台，不因一次调用“比较慢”就获得可恢复承诺。

### 三种“结束”分别表示什么

| 事件 | 含义 | 不代表 |
| --- | --- | --- |
| 工具返回 running + task ID（command 另带 command ID） | 工具已把执行交给后台管理 | 执行已经结束 |
| run 发出 Done | 这一轮模型执行和回复已结束，释放 active-run slot | 所有后台任务或业务目标已经完成 |
| 后台任务进入终态 | 执行方已提供完成、失败或取消的可靠证据 | 结果已送达模型、模型已验证业务目标完成 |

每个 run 仍按现有契约只发一个公共 Done。后续处理结果是同一 conversation 的新 run，不复活旧 run，也不在结束后继续向旧 SSE stream 发回复。

## 2. 当前问题与可复用的能力

### 已验证的现场问题

调查对象：`conv-1t5FZdOjxXNbkq`。证据来自 `backend/logs/k8s-215.log` 和 `backend/cubeloop-traces/2026-09-21/` 下的 `defe4b21662999e9101d321ca6a54580.jsonl`、`73549d271bb4ed649eff2fd5a57254d4.jsonl`。以下为此前调查采样，不代表会话现在的活动状态。

| 事实 | 本版如何理解 |
| --- | --- |
| 原 run 取消后约 6.8 秒，run-lifetime 命令的 completion 启动另一个 run | 旧契约下不该跨 run 的通知被提升为会话通知；不能把这一行为直接当作新方案已经正确 |
| 模型反复执行 ps/sleep；Todo 对带未完成项的纯文本回复强制续跑 | 缺少“剩余工作正在等后台结果，可以结束本轮”的合法出口 |
| cleanup interrupt 返回 provider 500 / not running；之后 poll 得到 exited/236 | 停止请求和进程事实混淆；236 的具体原因尚不确定 |
| 两次 kill 报 not found，但同 scope 的持久行已是 exited/0 | 不存在、已完成、停止未确认被混为一个布尔结果 |
| root-owned 日志目录导致普通用户 rm 分片失败，现场分片均已写入目标日志 | 写入确认和临时文件清理混淆；本次未发现日志缺片 |
| 后续 run 结束后 root span 补齐 | 不是 trace 损坏，不纳入本次生命周期必修项 |
| 输入框上方积累 command／monitor 通知，关闭请求返回 422 | 内部 wake 被当成用户 steering 展示，但取消接口拒绝内部 ID；需要分开事件身份、展示和控制 |

输入框堆积的直接原因已经定位：`_deliver_wake` 为同一 wake 的不同目标 run 创建不同 steering ID；run 结束时未提交的投递可能成为 failed，重投到新 run 后旧行仍在。bootstrap 返回 queued／dispatched／failed 行时没有区分来源，`PendingSteers` 把它们全部显示成可撤回或恢复的用户消息。日志中该会话的 46 次 `/steer/cancel` 请求均返回 422，因为 `CancelSteerRequest` 拒绝 `scmw-` 内部前缀。它不是“有这么多个独立 monitor”的证据。

只放开取消校验不能修复这个问题：取消单次 steering 不等于取消源 wake，投递层仍可能重试。只在前端按 ID 前缀隐藏也不够；实时投影、bootstrap、历史读取与控制入口都必须使用同一来源分类。

### 不是从零开始做会话通知

`sandbox/command_coordinator.py::_deliver_wake` 已按 conversation 查询 active run：running 时 enqueue durable steering；无 active run 时调用 `start_run(trigger="automated")`；paused HITL 时延后投递。已存在 checkpoint 对账、稳定 delivery ID、成员权限检查与重试逻辑，应复用并补齐竞争测试。

主要冲突在 `middleware/sandbox.py`：当前 `notify_on_complete=true` 默认绑定 `lifetime=run`，`on_run_end` 等待这些命令，finalize 又清理 run-scoped 进程。通知需求、进程生命周期和 run 生命周期因此耦合。

更新后的 CubeLoop 仍在 `on_run_end` 前关闭 input admission；此前 13 项现有测试和行为探针确认了该契约。正确方向是移除宿主在收尾钩子中的长等待，而不是让 CubeLoop 为小时级后台命令保持 admission。

### 为什么公共层不能继续叫 command

当前 `SandboxCommand` 强制绑定 sandbox，并保存 shell 文本、exit code、日志 cursor 等进程字段；`SandboxCommandWake` 也直接依赖 command ID。会话 Stop、结果路由和 Todo 等待如果继续依赖这些字段，其他后台执行只能重复实现或伪装成命令。

现有 MCP 路径由 `cubeloop/mcp/http_loader.py` 等待 `session.call_tool(...)` 返回；subagent 在 `cubeloop/middleware/subagents.py` 中等待 `child.prompt(...)` 并转发父调用的取消信号。二者当前都不是已接入的持久后台任务。公共契约应允许未来接入，但不能把这些现状描述为已经支持 detach／重启恢复。

## 3. 方案取舍

| 方案 | 结果 |
| --- | --- |
| 保持 live Session，新增 IdlePolicy 等待阶段 | 需维护长时间输入、取消、心跳和 worker 存活；本次不采用 |
| 后台任务独立运行，结果按 conversation 投递 | 复用现有 monitor 路由；run 可结束，worker 重启后可接管持久命令；采用 |
| 新建通用任务调度／持久工作流系统 | 扩大本次范围；不采用 |

本版取消以下前置设计：CubeLoop live-wait／IdlePolicy、新增 waiting ExecutionResult、专门的 `run_activity=waiting_commands`、用于维持原 run 生命周期的 `sandbox_command_scopes` 表。不移除现有 active-run claim、attempt fencing 或 HITL 协议。

公共层有三种边界选择：全部规则留在 command 内，眼前迁移最少但未来会重复；薄的后台任务生命周期加类型适配器，统一控制和通知而保留执行差异，本版采用；一次性实现任意工具后台化和持久工作流，范围与恢复承诺过大，不采用。接口是内部实现边界，不建设插件框架或预先实现未来适配器。

后台事件与用户发言在身份和展示上分离，调度采用“活跃 run 在安全输入边界接收结果、空闲时新建 run”。不要求所有通知都等当前 run 完成，也不把界面上的系统事件映射为修改模型 system prompt。

## 4. 用户流程

### 4.1 安装／构建等一次性长命令

1. 在同一事务登记公共 task 和 command 详情，再请求 provider 启动；command 前台最多等 15 秒。显式 background 则尽快返回句柄。15 秒是 command 的交互预算，不是所有适配器的统一超时。
2. 前台已拿到终态且最终输出可读时，工具直接交付最终结果，不额外发送同一完成通知。交给后台时，返回 task ID、command ID、真实执行状态、日志路径、deadline 和是否自动通知，并持久标记结果由任务事件通道负责。进程已退出但输出尚未就绪也可交后台，明确 result_pending，不能把它伪装为仍在执行或已经交付完整结果。Todo 和公共控制引用 task ID；command ID 只用于命令领域操作。
3. 模型继续独立工作。剩余步骤都依赖后台结果时，保留未完成 Todo，说明“任务在后台运行，完成后继续”，结束本轮 run。
4. coordinator 管理进程、日志、deadline 和 sandbox 保活；此时不需要 Agent、旧 Session 或旧 run heartbeat。
5. 命令结束后创建带来源的会话事件。有当前 run 就内部注入；没有就新建 run。模型结合原会话和 command 结果继续验证、执行后续步骤、回复用户。用户看到任务状态和折叠结果行，不看到一条伪装成自己输入的待发送消息。

例如“两小时构建 → 查看报告”：启动构建的 run 可以几十秒内结束；两小时后的通知开启新 run 查看报告。中间用户仍可聊天；若通知到达时用户的新 run 正在工作，结果进入这个新 run，而不是启动并行 run。

后台交接与终态观察通过 task 的当前 owner 串行处理，明确选择“本次工具直接给终态”或“工具给 running、之后任务事件给终态”，不能两条路径各通知一次。后台交接已经提交但工具结果投影失败时，任务仍能从持久列表找到；不因 worker 崩溃重新执行同一 shell command。该交接规则属于公共层，不由各适配器再实现一份通知流程。

前台 owner 在交接前崩溃也不能漏管：确认旧 attempt 已失去执行权后，接管者检查原 tool result 的 checkpoint。最终结果已交付则不补 completion；尚未交付则接管已受理命令、完成后台交接，让后续结果走 wake。含 result_pending 的终态快照不是最终交付证明。无法确认旧 attempt 已退出或 checkpoint 状态时保留恢复中，不因一次查询失败就重复通知。

### 4.2 monitor 与 server

- monitor 本来就属于 conversation，本轮作为 command 适配器的 monitor 子类型，保留 line／exit 通知、限流和防刷屏规则，使用公共任务事件投递层。
- 安装、构建自身的结束由 execute completion 通知，不让模型额外启动 monitor 去轮询同一个进程。
- `notify_on_complete=false` 的 server 继续后台运行，退出只更新状态，不自动调用模型。
- 通知是后台任务的新输入，标明 task ID、类型和事件原因；命令详情另带 command ID。它不是用户主动发言，也不是旧 tool call 的第二个 ToolResultMessage。
- 活跃 monitor 在后台任务区域占一条任务记录；多次输出更新结果，不新增一排“待发送 monitor”。去重只合并同一事件的投递重试，不吞掉不同的有效输出或退出事件。

## 5. 后台任务归属、停止和时间

### 5.1 生命周期与通知分离

所有接入的后台任务都归 conversation 管理，本轮为 managed execute 和 monitor。`originating_run_id`、`tool_call_id`、`agent_id` 保留发起来源和 UI 关联，不再决定正常 run 结束时是否结束任务。公共层保存结果通知策略；execute 的 `notify_on_complete` 映射为完成通知开关，不决定执行归属。

持久 task reservation 是执行已经被系统受理的边界，包括 starting 状态。run 正常完成或非用户原因失败，不自动撤销已受理的后台工作；用户主 Stop 则显式撤销本会话当前执行，包括这些 reservation。未被受理的新工具调用仍受现有 attempt ownership 和取消控制约束；本版不承诺 Stop 与远端启动存在跨系统原子事务。

### 5.2 停止范围：主 Stop 是彻底停止当前会话执行

| 操作 | 语义 |
| --- | --- |
| 聊天主“停止” | 停止本会话当前 run／子代理，取消待确认 HITL、尚未提交的用户 steering，向所有受管后台任务请求停止，并禁止旧工作的后续自动唤醒 |
| 任务卡片“停止任务”／命令工具“停止命令” | 持久停止指定 task，取消其尚未送达的自动通知，按适配器能力停止执行；有受管后代时一并停止，不影响同级任务 |
| 结果行折叠／收起 | 只改变展示，不取消通知、不杀进程，也不删除历史 |
| 删除 conversation | 在软删除的同一事务关闭执行批次并登记停止／通知取消；隐藏后继续清理，不再接受新工作 |
| sandbox Restart/Delete | 沿用既有权限与确认，影响整个 sandbox；环境确认消失后收敛进程状态 |

主 Stop 不以有无 active run 为前提：只有后台任务或待发结果时也能停止。范围是本 conversation 的受管执行，包括之前正常结束的 run 留下的工作、starting reservation 和已结束但结果尚未送达的任务；不是销毁整个 sandbox，不影响其他 conversation，也不承诺发现和杀掉所有非受管进程。

为覆盖 Stop 与新启动／通知重试的竞争，conversation 持久保存 `execution_generation` 和停止标记。generation 表示一批仍有执行权限的工作，不等于 run ID；多个正常结束的 run 及其后台工作可以属于同一批。run、task、任务事件与待提交输入都关联受理时的 generation。只有 generation 与当前批次相同且未停止才有继续执行资格；停止后新开批次时单调递增，不能只清空停止时间使旧工作重新有效。停止流程为：

1. 先持久关闭本批次的执行与自动唤醒权限，再发送取消信号和做进程清理。不能只扫描一次当前 running 行后逐个 kill；否则扫描后的 reservation 或迟到 completion 会漏过。
2. 关闭标记提交后，新 reservation、内部输入投递、automated run admission、后续模型／工具执行都检查批次仍有效。已在途的远端请求无法瞬间撤回：保留其真实回执；迟到启动回执必须登记执行句柄，随后按已有停止意图和适配器能力处理，不能丢句柄或清除停止标记。
3. 持久取消该批次的未提交 wake／内部输入及用户 steering，撤销该批次的运行和 HITL 继续执行资格。尚未完成的清理可由 coordinator 重试；worker 重启、owner 过期、Redis key 消失不能重新授予执行权限。已有更早停止批次的未完成清理也继续进行。
4. Stop 之后明确受理的新用户消息，或独立授权的下一次调度执行，可以开启新批次；旧请求重试、旧任务结果和 HITL resume 不可以。重复点击或重试同一次 Stop 只作用于原目标批次，不能误停后来的新任务。新请求仍等待旧 active-run slot 安全释放，不能通过取消中的旧 run 接收。旧通知不会因用户恢复聊天或新调度到期而获得新批次身份。

受理来源必须由服务端区分，不能仅凭 `trigger="automated"` 决定能否开启新批次。现有 fixed-target schedule 会重复进入同一 conversation；它的新 occurrence 保留独立授权，但旧 command completion 没有这个权限。持久受理记录保存 `source_kind`、稳定 `source_id`、目标 conversation 和受理时 generation；同一来源重试只读取原绑定，不能重新分配批次。用户消息、schedule occurrence、既有 trigger occurrence、后台 notice 分别来自已鉴权的入口，客户端不能自报为内部调度。

用户消息的受理身份还绑定不可变请求摘要：正文、有序 attachment IDs、请求的 model_key 与规范化 reasoning 等影响执行的提交字段。相同 client_message_id／receipt／steer_id 携带不同请求时拒绝复用，不能把改过的消息当作原 run 的成功重试。摘要按规范化请求计算，不包含发送时间、临时 URL 或重试时重新解析的默认模型。首次解析出的模型／provider 选择、有效 reasoning 等执行设置，以不含凭据的不可变快照与受理记录在同一事务持久保存，先于排队及 run 创建；不能只存在内存或尚未创建的 run 上。受理提交后崩溃，即使默认配置改变，重试仍读取原快照而不重新选默认值；权限和凭据仍按当前状态校验，原选择不可用则明确失败，不静默换模型。旧记录若没有可靠请求证明，不猜造摘要或执行快照。

首次用户受理的事务同时保存其会话 model_key／reasoning 选择，并校验、锁定附件后将其从 pending 标记为 attached；任一部分失败则整体回滚。会话选择按新的受理顺序更新，相同来源重试只读取原受理，不覆盖后来消息的选择；后台新 run 因而能看到最后一次已受理的会话配置。附件孤儿清理与受理按同一行的锁／删除资格串行裁决：受理先成功的附件不能被先前扫描结果删除，清理先获得删除权时受理明确拒绝，不能接受即将消失的对象。持久附件引用不保存临时签名 URL。

schedule occurrence／trigger event 在首次持久领取时保存已渲染内容及影响执行的非凭据参数，包括当次目标策略和模型选择；目标 conversation 尚未确定时先随源记录保存，目标确定后将该快照绑定到 admission，再排队。忙碌重试、IM 转交和 worker 恢复均使用同一快照，不重新渲染可变模板或读取修改后的 prompt；编辑定义影响之后的新 occurrence／event。当前停用／撤销授权、目标删除等控制仍重新检查，快照不授予永久执行权限，也不重开旧 generation。

trigger 的 202 受理还必须有持久的后续消费者，不能只依赖进程内 create_task。完成入口校验后，将可执行事件、冻结内容及排队状态持久提交，再返回 accepted；用于去重／审计但尚未通过入口校验的记录不自动获得执行资格，重复请求不能把未完成受理误报为已排队成功。专用 trigger worker 启动和运行中均领取可执行事件，用有期限的 claim 接管崩溃遗留工作；重试时间和尝试次数持久保存。conversation 目标一旦创建就与事件在同一事务绑定，重试不另建会话；run／IM 转交沿用稳定来源和已绑定 admission，回执丢失先对账，不重复执行。不绕过过滤、限流、停用或当前权限，也不改变调度计划或建立新通用工作流引擎。

首次受理与 Stop 使用同一 conversation 锁串行：固定会话的 occurrence 被领取并登记待执行时就绑定批次，不等 worker 真正开始调用模型；先受理则属于 Stop 关闭的旧批次，后受理且来源有独立授权才可进入新批次。Stop 后不得把旧 occurrence 的 busy 重试、IM 队列重试或恢复任务解释为下一次触发。经 IM 转交的调度仍保留原 occurrence 身份。这里只补入口分类、幂等绑定和停止边界，不改变调度计划、missed／busy 策略或引入新调度引擎。

停止接口持久受理后返回 202；这表示平台内旧工作的继续执行权限已撤销，不表示底层执行已经消失。UI 立即显示“正在停止”，支持远端停止但尚未确认的任务显示“停止未确认／正在重试”，全部确认后才显示“已停止”。如果适配器明确不支持远端取消，显示“已停止后续处理，远端任务无法取消”；不显示取消成功，也不无限重试一个不存在的取消接口。底层状态未知则继续如实显示未知。不能在持久写入失败时返回成功，不能把清理失败或取消后的迟到结果变成新的模型唤醒。

单独停止 task 同样先持久写入停止意图与通知取消标记，再异步联系适配器；所有入口共用该流程，但不关闭整个会话批次。即使任务刚刚自然完成，也可以取消其尚未送达的通知，必须保留真实结果，command 的 exit code 不得改写。已提交到 checkpoint 的输入和已经发生的副作用无法撤回；主 Stop 会取消正在处理它的旧 run，保留历史事实，不承诺回滚。停止 task 的后代约束在受理新子任务时也检查，不能只扫描一次已有孩子；本轮 command 没有受管子任务，不改变当前同步 subagent 的实现。

删除会话不是仅停止通知。复用主 Stop 的持久控制流程，在写入 `deleted_at` 的同一事务关闭批次、记录受管任务停止意图和取消未提交通知；事务失败则不报告删除成功。既有删除权限与软删除返回契约不变，返回成功只表示删除和停止意图已持久受理，不表示远端已退出。删除后普通会话／任务 API 仍返回不可见；coordinator 按原 scope、实例身份和已登记清理权限继续观察／取消，包括迟到启动句柄及更早批次未完成的清理，不依赖用户重新访问会话。不得级联删除清理所需的 task、执行句柄、通知对账证明和审计记录；deleted 标记永久阻止新用户、调度或恢复为该会话重新开批次。删除与 reservation／首次受理使用同一锁，清理不能触及其他会话的任务。

### 5.3 等待预算、执行期限与资源期限分开

公共任务可以携带绝对 `deadline_at`；到期意味着禁止该任务继续派生工作并请求取消，不保证远端立即结束。deadline 与用户主动停止的原因分开：超时结果仍可按通知策略报告；用户停止才同时取消它后续自动通知。前台等待预算、执行 deadline、远端结果保留期限和资源保活不是同一个时间。具体类型声明自己的默认值，本轮仅确定以下 command 规则：

| 时间 | 规则 |
| --- | --- |
| execute 前台 15 秒 | 只决定何时返回，不 kill 进程 |
| execute 显式 timeout | 从受理启动时计算的总期限，允许小时级；移除当前 1800 秒上限，正整数秒，不因后台交接或 worker 接管重置 |
| execute 未指定 timeout | 使用后端配置 `sandbox.command_default_timeout_seconds`，默认 3600 秒（1 小时）；显式 `timeout_seconds` 优先于该默认值 |
| monitor | 本轮保留非 persistent 默认 3600／最大 36000 秒；更长或无期限监听明确使用 persistent。返回实际 deadline，不能只承诺“会一直等” |
| sandbox／平台资源限制 | 仍生效；遇到上限明确拒绝或报告环境失效，不能把“无命令 deadline”描述为进程一定永久存活 |

配置项位于 `backend/config.yaml` 的 `default.sandbox.command_default_timeout_seconds`，环境变量覆盖为 `CUBEPLEX_SANDBOX__COMMAND_DEFAULT_TIMEOUT_SECONDS`。单位为秒，只接受正整数；0、负数、非整数或 null 是配置错误，不解释为无限期，也不静默回退。它是部署级的 managed execute 默认总执行期限，不是前台等待预算、HTTP request timeout、sandbox TTL 或所有任务类型的统一期限；不新增配置 UI，不改变 monitor 的默认值／persistent 语义。

受理时按“显式 `timeout_seconds` → 当前配置默认值”选择总期限，并一次性计算、持久保存 `deadline_at`。显式 timeout 可以短于或长于 1 小时，仍受平台资源限制；`background=true` 和 `notify_on_complete=false` 都不会绕过默认期限，server 如需更长时间须明确指定。配置更新只影响之后新受理的命令，不重算已受理任务的 deadline，不为历史无可靠期限记录补一个立即到期的值；worker 接管仍使用原 deadline。

绝对 `deadline_at` 持久保存，返回给模型和 UI。到期写停止意图；provider 不可达时显示“已到期，停止未确认”。正常自然退出、超时和环境失效可按通知设置报告；用户主动停止则遵循上节的通知取消规则。基础设施 blocking execute 保留原用途，不混入这条 agent-facing 托管契约。

## 6. 公共生命周期与具体执行的边界

### 6.1 由 CubePlex 管理，CubeLoop 只执行 agent

| 组件 | 单一职责 |
| --- | --- |
| 后台任务 service | 公共 task 登记、归属与批次、owner claim、前后台交接、状态落库、停止意图、deadline 和事件原子写入 |
| 具体执行适配器 | 启动执行、以既有句柄观察或恢复连接、请求取消、取得结果；返回证据和能力限制，不自行唤醒模型 |
| Command 适配器／Sandbox driver | 本轮唯一具体实现；管理 sandbox 进程、provider_ref、exit code、日志 cursor、8-command cap 与资源保活 |
| Coordinator | 认领到期需处理的 task，调用 service 与对应适配器，有界观察、取消重试和恢复；不解析 shell 或 MCP 协议 |
| 任务事件投递层 | 校验执行批次与停止意图，根据 conversation 状态选择内部输入或新 run，凭 checkpoint 确认投递；不依赖 command 字段 |
| Conversation 控制服务／RunManager | 持久受理主 Stop、关闭执行批次、协调 run／HITL／输入取消及后台清理；各入口复用同一控制流程 |
| CubeLoop Session | 一轮正常模型执行、已有输入协议和 HITL；不为外部任务保持运行，不保存平台任务表或决定产品停止范围 |
| UI | 分别展示用户 steering、后台任务和结果事件，从同一持久事实恢复，不展示内部投递尝试列表 |

前台 owner 交接后不再观察同一执行；middleware 本地 map 只可做关联缓存，不是 cleanup、deadline 或存活的依据。移除 `on_run_end` 中的等待及重复 provider polling。普通 finalize 只结束本轮资源并交出 task owner，不能把后台任务当作 orphan 终止；主 Stop 的持久清理不能依赖 finalize 是否成功执行。

coordinator 不需要为每个 task 保留一个活数小时的等待协程。沿用有界调度和 owner lease；能重新连接的执行在 worker 重启后按原句柄接管。恢复观察不等于重新提交工作。task owner lease、资源保活和执行 deadline 是三件事，续 lease 不延长 deadline。command 的原 sandbox 确认被替换或销毁时不能自动重启到新环境，也不能把旧 provider_ref 用于新实例；绑定规则见第 9.2 节。

### 6.2 共同身份与状态，不统一执行参数

公共记录包括 task ID、类型、org／workspace／conversation、发起 run／tool call／用户、可选 `parent_task_id`、执行批次、描述、执行状态、停止意图、通知策略、deadline、owner／lease、结果引用和 revision。parent 只能关联同 scope、同批次的任务，不能形成环；它表示执行归属与停止传播，不是任意业务依赖图。

状态区分 starting、running、waiting_input、succeeded、failed、cancelled、unknown；本轮 command 不产生 waiting_input。succeeded 表示本次执行按该类型的结果契约成功，不等于整个业务目标完成；failed 需要执行失败或执行环境已失效的证据，不来自一次查询超时。具体执行方的原始状态／错误／结果保留在类型详情中，由适配器映射；不能把协议响应成功直接当作业务执行成功。

执行事实、停止意图和通知投递状态分别保存：请求取消不直接把执行状态写为 cancelled，暂时无法观察不写为 failed，模型已收到通知也不写为任务 succeeded。只有可靠终态证据才能进入终态；单次观察故障不抹掉已有终态。本轮 unknown command 继续占用资源名额，除非已有证据证明没有实际启动或执行环境已消失。

公共层不要求所有任务有 shell 文本、sandbox、PID、exit code、日志文件或可编辑输入。结果通过有类型的摘要／内容引用表达，日志只是某些类型的可选详情。后台任务与定时任务、Todo 保持独立模型，不把调度时间或 Todo 完成状态搬进来。

### 6.3 接入必须说明真实能力

适配器的最小职责是登记一次执行的句柄、观察事实、取回结果，并说明是否支持远端取消、重新连接观察、日志及执行中输入。公共层不要求所有类型实现空的 `poll/kill/log/steer` 方法；支持的能力才暴露操作。能力由服务端适配器依据实际执行方式确定，不由模型或客户端自报。

- 可恢复意味着重启后能用持久身份继续观察同一执行并取得结果，不是把一个 Python coroutine 放到后台。需要凭据时按原 scope 重新解析并检查权限，不把访问令牌存进 task 详情或返回给 UI。
- 创建意图先于外部副作用持久受理；启动响应丢失时，按执行方的幂等键／关联句柄对账。没有安全对账或幂等保证时保持未知，不盲目重试创建，以免重复部署、扣费或运行命令。
- 取消能力只表示可以发出取消请求，是否成功仍以结果证据为准。不支持远端取消不妨碍撤销本地继续执行和结果唤醒权限，但必须告知用户这一限制。
- 恢复、观察和通知重试不等于业务重做；公共层不自动重试失败的原始任务。结果记录过期或无法找回时报告不可用／未知，不伪造成功，也不重新执行来补结果。
- 公共层接受适配器提交的观察结果，不假定所有执行都靠周期 poll；未来回调接入仍需经过同一 scope、owner／批次与事件去重校验。本轮只实现 command 所需的观察方式。

### 6.4 类型边界与后续接入

| 类型 | 具体实现负责 | 本轮范围 |
| --- | --- | --- |
| command（execute／monitor） | sandbox 启动与控制、进程事实、日志、资源 cap／保活；monitor 匹配和限流 | 首个完整实现，共用公共任务与事件协议 |
| MCP 外部长任务 | connector／授权、远端 job ID、状态／结果查询和取消协议 | 仅确定接入条件；普通 `tools/call` 保持现状，不自动 detach |
| detached subagent | 独立执行上下文、checkpoint、模型预算、工具权限、审批及子任务控制 | 仅确定归属与控制边界；不改当前同步 subagent |

未来 MCP 接入需确认目标服务确实提供可观察的异步任务或专用 job 接口，并按实际协议版本适配。没有远端任务句柄和恢复方式的普通 RPC，不因超时或调用耗时就宣称可持久接管。本轮不预先实现 MCP Tasks 客户端、不承诺任意 MCP 工具可后台化。

未来 subagent 的 detach 是脱离父 run 的等待，不是脱离会话控制。它继承 conversation 执行批次，后代登记父任务关系；主 Stop 或停止父任务都禁止后代继续创建工作。它使用自己的执行上下文与 checkpoint，不与父会话共用消息状态或争用父会话唯一的前台 active-run slot；仍受独立并发和预算约束。最终结果经公共事件通道回到会话，过程事件不逐条唤醒主模型；审批必须绑定真实子执行，不能冒充父 run 的审批。本轮不实现这些执行／审批／调度机制，只要求未来接入不得破坏这些边界。

## 7. 会话通知协议

### 7.1 一个持久出口

把现有 `sandbox_command_wakes` 通用化为按 task ID 关联的 `background_task_events` outbox，迁移既有记录和去重标识，沿用投递、claim 与 checkpoint 对账机制；不是另开第二条并行消息管线。本轮 execute completion 和 monitor line／exit 都走这里，未来类型接入不再复制一套 wake 逻辑。不再同时保留“on_run_end 注入一次、orphan scanner 又发一次”的正常完成路径。

- completion 用 task ID + completion 类型去重；monitor line／exit 保留现有稳定事件标识。重试不生成新的 notice ID。
- 保存公共终态、具体结果引用与创建待发事件在同一数据库事务内完成；已取消通知的 task 不再生成待发事件，但仍保存迟到结果供查询。
- `notice_state` 若保留，只是完成通知的摘要，不能成为另一个独立消费者。wake 增加 discarded，不能把权限撤销或用户取消记成 delivered。
- 通知包含 `source=background_task`、`notice_id, task_id, task_kind, originating_run_id, execution_generation, reason` 及必要结果摘要／结果引用；可选父任务关联来自 task。command ID、execute／monitor 子类型和日志位置属于命令详情，不是所有事件的必填字段。发起 run 只是来源；目的地是 conversation。原发起用户是权限校验主体，不是这条事件的发言者。
- 进度更新默认只更新任务快照，不每次都产生模型输入。哪些事件需要通知由任务策略决定；本轮保持 execute 完成通知与 monitor line／exit 规则，错误和终态不能被高频进度覆盖。

### 7.2 投递规则

| 当前会话状态 | 操作 |
| --- | --- |
| 有可接收输入的 active run | 持久化带后台来源的内部输入，经公共 Session 输入通道在安全边界接收；不变成用户 steering |
| run 正在准备／收尾，暂时不能接收 | 保留 pending，等待原 run 接收或释放 slot 后重新路由；不能同时开第二个 run |
| 无 active run，且没有持久 HITL 待确认 | 经现有原子 active-slot claim 创建 automated run，加载同一 conversation 的 checkpoint |
| 存在 HITL 待确认，包括 Redis active key 已过期 | 保留 pending，不把审批消息当作已回答，不绕开 HITL 新建 run |
| 会话已删除、发起者权限失效、通知被取消或其执行批次已停止 | 不投递，未提交的事件记录 discarded 和原因；不换成 system actor 绕过权限，不移入新批次 |

新 run 使用现有 conversation 模型／reasoning 配置和原发起者身份检查。用户消息与通知同时到达时由现有 active-run claim 决定谁先取得执行权；失败的一方按自己的既有排队／重试协议继续，不能覆盖用户输入。

同一次调度中，属于同一 conversation、同一有效批次且已就绪的通知可以按稳定顺序投向同一个 run，减少重复创建 run；本轮不把多个 notice 合成一条物理输入。每个 notice 各有独立的 InputEnvelope／投递 ID、来源和 checkpoint 确认。空闲时用其中一个 notice 作为新 run 的初始输入，其余经相同 admission 校验后分别投递；不保证恰好只有一次模型调用，不为凑批长时间等待。monitor 在生成源事件前保留原限流／输出合并规则；源 notice 一旦生成，其身份和成员不因投递重试而改变。

因此 A、B 同时排队时，单独停止 A 只撤回 A 的输入，B 保持可投递。撤回返回 `closed`、响应丢失或提交状态未知，都不能当作“肯定未送达”；先按第 7.3 节对账，已提交的历史保留，未确认失去提交资格前不重投。不增加 Session 内修改已排队输入内容或拆批的协议，UI 的聚合展示也不改变输入边界。

新 run 的首条 notice 单独处理：它通过 PromptExecutionRequest 进入，不在 `cancel_input` 的可撤回队列中。宿主持久绑定首条 notice 与投递 attempt，准备阶段和调用 Session 前都检查源 task 的通知权限，不能只检查 conversation generation。初始输入的持久提交尚未确认时，B 等其他 notice 与用户追加输入保留在各自持久队列中，不向这个 Session 提交。确认初始输入已提交后，才允许其他输入进入；开放这一入口与首条 notice 的取消裁决必须串行，不能让取消 handler 读到旧状态后误停一个已经接收 B 的 attempt。

停止首条 A 的 task 时，若仍在准备／初始提交未决阶段，先关闭该 attempt 的其他输入入口，再取消对应 attempt，并按 checkpoint 对账，不把 `closed` 当作取消成功。A 已提交保留 delivered 历史，未提交且 attempt 已失权则 discarded；B 等此时尚未进入 Session 的输入仍可重新路由。若初始提交已确认并已开放其他输入，单 task Stop 不再取消整个共享 attempt，只撤销 A 后续执行／未提交通知；尤其不能让已提交 B 因停掉共享 attempt 而失去处理机会。主 Stop 仍可取消整个批次。这里不新增 delivered 通知的自动业务重试，也不新增 CubeLoop Session 输入 API。

### 7.3 排队成功不等于结果已送达

内部输入只有收到 `InputCommitted(durability="checkpoint")` 或恢复时查到相同 notice ID，才能标记 delivered。启动新 run 返回成功也不算送达，仍需检查其初始输入已经持久保存。delivered 表示输入已入历史，不承诺模型已成功处理业务结果；run 失败不能靠重复同一通知盲目重做任务。

如果原 run 在投递途中结束，先对账 checkpoint：已提交就确认；未提交且原投递已确定不能再提交，才取消旧 steering、重新路由。旧 attempt 的 fencing 必须生效；不能仅因一次“没查到”就向另一个 run 重发。worker 崩溃、claim 过期、start_run 响应丢失都沿用同一 notice ID 对账。

用户停止 task 或主 Stop 与事件 claim 竞争时，以 task／父任务的通知取消标记和 conversation 执行批次为准，生产、投递和新 run admission 都检查，并取消尚未提交的内部输入。仅 deadline 到期不能误用通知取消规则吞掉超时报告。已经在途的 checkpoint 提交仍可能先完成；按实际历史确认 delivered，不能为表示“停止”而删除它或重写为 discarded。主 Stop 后它不能继续驱动旧批次执行，也不能因重试变成新批次输入；不声称 Postgres、Redis、Session 之间存在一个跨系统事务。

### 7.4 输入机制与用户消息分离

- 可以复用 durable steering 的存储／投递能力和 `ExecutionSession.submit_input`，不另建通用消息队列；内部记录必须保存来源、notice ID 和批次，不能只靠内容或 `scmw-` 前缀推断。
- checkpoint、输入确认、实时事件和历史投影保留同一来源。面向模型可使用 provider 支持的消息 role，并显式说明这是后台执行结果；面向用户则按后台事件呈现，不冒用用户头像、姓名或气泡。后台输出是任务数据，不是用户新指令或审批答案。
- 不修改稳定 system prompt，不回写旧 tool result，不伪造旧 tool call 的第二个结果。来源元数据随新输入持久保存，刷新和重放不能把它还原成用户发言。
- `pending_steers` 与用户 steering 实时事件只包含 `source=user` 的主动输入。`/steer`、`/steer/cancel` 只处理用户消息；source 由服务端入口确定，客户端不能通过自填 source 获得内部通知身份，保留内部 ID 的防伪校验。
- 后台结果的产品状态来自源 wake 及 checkpoint 对账；一次投递尝试失败不是一条新的产品消息。同一 notice 换 run 重试、重新 claim 或 bootstrap 后仍是同一条事件，旧 failed／queued steering 不得重新进入用户列表。
- 来源分类也约束个人记忆提炼：首条输入或本轮已提交追加输入含 background_task notice 时，不触发本轮自动个人记忆 reflection，避免将日志／自动结果当成用户偏好或纠正。普通纯用户输入的 reflection 保持原行为；不回写历史或修改 prompt 缓存前缀。本轮采用保守跳过混合轮次，不新增一套自动结果记忆系统。
- 自动 memory consolidation 同样排除后台来源：读取持久历史时，先按输入来源与 run 关联识别并排除含后台 notice 的整个轮次，再裁剪窗口及转成 role／text。仅跳过后台 run 的即时调度不够，后续普通用户 run 再次合并历史也必须使用相同过滤；旧通知按可靠 wake 关联分类，不靠正文猜测。窗口边界和缺失关联不能把孤立的后台回复误算为纯用户轮次，无法可靠分类的片段不用于自动记忆。无合格历史时不调用提炼模型、不写个人或 workspace memory，但按现有 cutoff／consumed 规则完成本次扫描，保留扫描期间新增 run 的计数，避免反复扫描同一批排除内容。正常纯用户历史的合并保持原行为，不回写 checkpoint。
- UI 的折叠不改变投递。需要阻止后续处理时，停止源 task 或使用主 Stop，由服务端取消源事件和未提交输入；不提供“把内部通知恢复到输入框”或仅取消某次内部 steering 的入口。

## 8. Todo 如何允许本轮结束

只改通知路由还不够：现有 Todo 会因 unfinished + 纯文本要求模型继续。因此需要一个明确的“剩余工作等待后台结果”的收尾例外，而不是移除全部 Todo guard。

建议扩展现有 `write_todos`，增加可选 `wait_for_tasks: list[str]`，默认空列表，引用公共 task ID。不新增 wait 工具，也不增加 Todo 的 completed 变体：

1. 模型显式声明：当前没有可独立执行的剩余步骤，它们在等这些后台任务的结果。保留原 Todo 列表和 pending／in_progress 状态。
2. CubePlex 经公共 task 查询校验 ID 属于当前可访问 conversation、当前批次仍有效、确实由后台管理、尚有待交付的结果、会产生自动通知且自身／父任务未被用户撤销继续处理和通知权限。仅 deadline 到期、仍有待交付超时结果时不据此拒绝等待。notify=false 的 server、不可恢复且已失去执行方的任务不能成为自动续办的等待凭据。
3. 等待声明和对应 Todo 快照随 Session extra 一起 checkpoint。普通 Todo 更新未携带等待声明时清空它，不把旧声明套在新计划上。
4. 自然收尾时重新校验；对有效等待声明跳过“未完成所以必须再调用模型”的 finalization guard，保留 payload 校验、错误处理和显式 stop。任务已终态但结果尚未送达，也允许结束本轮，由通知接续。
5. 用户新输入或相关结果输入提交后，旧收尾许可失效，模型先处理新输入、更新计划；若仍需等待，再显式声明。不能因为会话里任意一个 monitor 还活着，就放行所有未完成任务。
6. 声明创建时就做宿主校验并持久保存成功校验的绑定；若自然收尾前其中依赖被用户单独停止，宿主可返回 cancelled 收尾结果，而不是强制模型忙等。仅适用于同一 Todo／输入边界上先前有效的声明，且每个依赖仍有效或有后来发生的用户取消事实；任意无效 ID、权限不明、查询故障不获得此许可。保留未完成 Todo，记录“等待已由用户取消”的收尾原因后正常结束当前 run，不发新的结果通知、不唤醒空闲 run，也不终止已接收其他输入的共享 attempt。新输入仍使该绑定失效，主 Stop 按原取消机制优先处理；不能把已取消任务作为新的等待依据。

无 Todo 的简单命令不需要补一次 write_todos；正常给出进度回复并结束即可。后续 run 通过现有 `load_checkpoint` 同时恢复消息和 extra，不自行拼接私有 Agent 状态。业务步骤完成仍由模型验证，不因命令 exit/0 自动把整个任务勾完。

这可能需要 CubeLoop Todo 的小范围公共扩展：等待元数据和可选收尾校验策略，默认行为不变；task 查询和类型能力判断留在 CubePlex，CubeLoop 不依赖 command 或 MCP 模型。它不是修改 Session／agent loop 生命周期，也不依赖旧 IdlePolicy 提案。工具 schema／说明统一发布，动态 task ID 只进工具结果和输入，不随运行状态改系统 prompt 或工具集合。本轮只有 command task 作为实际等待来源，不为未来类型增加伪适配器。

## 9. 持久事实与 command 首个实现

### 9.1 公共生命周期只有一个写入来源

采用公共 task 主记录和类型详情，不能让通用表与 command 表各保存一份可独立更新的运行／停止／投递状态：

| 持久记录 | 权威内容 |
| --- | --- |
| `background_tasks` | 第 6.2 节的公共身份、状态、owner、控制与结果引用；统一 `deadline_at`、`stop_requested_at/stop_reason`、`notifications_cancelled_at`、`last_observed_at`、`revision`、`backgrounded_at` |
| `sandbox_commands` | 与 task 一对一、`task_id` 唯一的命令详情；保留 command ID、稳定 `user_sandbox_id`，另存不可变 `sandbox_instance_id`，以及命令参数、provider_ref、原始进程观察／exit code、日志路径／cursor／log_state、monitor 匹配和限流数据 |
| `background_task_events` | 从原 wake 表迁移的单一通知 outbox；关联 task ID，保存 notice ID、事件事实、去重键与投递／claim 状态 |
| `conversation_execution_admissions` | 首次受理的来源身份、目标 conversation、generation、run 关联及不可变请求摘要／有效执行设置快照；同一 scope 内 `(source_kind, source_id)` 唯一。只用于停止边界与重试幂等，不保存另一份任务运行状态或调度计划 |

公共状态由 task service 根据适配器证据统一更新，命令原始 exit code 是证据，不是另一套业务状态机。类型详情和任务记录的关联必须满足同一 org／workspace／conversation，读取和控制均不能绕过 scope。具体结果和公共终态／事件需要一起提交时使用同一数据库事务；不依赖双写后异步补齐两套事实。

conversation 保存执行批次和停止标记；run／task／事件／内部输入关联受理时批次，输入另外保存来源与 notice ID。受理记录在第一次持久排队或启动前提交，不能只存在 Redis run metadata 中；旧 occurrence 的重试读取同一记录。`backgrounded_at` 在后台交接提交时写入，显式 background 同样需要这一步；不是从 notify 或本地 map 推断。所有时间 tz-aware。新增业务记录的 public ID 前缀在 `models/public_id.py` 注册，不以 provider job ID 代替平台 task ID。此次不恢复 run command scope 表，不增加 MCP／subagent 详情表。

每次 owner claim 使用唯一 token；状态、cursor 和 lease 写入都校验 token。失去 owner 后不得继续提交观察结果。数据库锁不能撤销已发出的远端操作，新 owner 必须重新观察，不直接重启进程。

### 9.2 Command reservation、进程观察与停止

保留既有 sandbox scope 和 8-command cap；它是 command 适配器的资源限制，不是所有后台任务共用的并发上限。锁定 sandbox、检查 cap、验证 conversation 批次并创建 task／command reservation 在同一事务完成，并与 Stop 的批次关闭串行。不能先登记一个独立执行再补公共 task，使主 Stop 漏过它。

先登记再启动。无论取消或连接中断，已知 provider_ref 都必须持久保存。回调迟到时合并 provider_ref，但不能清除已有停止／通知取消标记。启动结果未知的 reservation 继续占 cap 并显示状态未确认，不能删行释放名额后重复启动。新批次开始后旧进程仍未确认退出时，仍计入资源占用并展示清理进度。

`user_sandbox_id` 是可以原地重建的稳定行，不是执行环境身份。外部启动前，将实际 attachment 的 provider sandbox ID 持久写入 `sandbox_instance_id`，并与之后返回的 provider_ref 绑定；该绑定终生不改指向新容器。reconnect、poll、kill、日志收集均定位并校验这一个实例，不能用当前 UserSandbox 行上的新 ID 替换它，也不能为了观察旧任务调用会创建新容器的恢复路径。替换后的旧实例有可靠销毁证据才收敛为环境失效并释放 cap；只有不匹配、无可靠退出证据时保留 unknown，若仍能安全访问旧实例则只对原实例执行清理。实例确认和启动之间仍可能遇到销毁，按失败／未知启动处理，不重新执行命令。

命令 tool、HTTP、deadline 共用公共停止入口，再由 command 适配器执行 poll → 仍运行则请求 kill → 再观察；reason 决定是否取消后续通知。kill 返回或抛错都不是终态证据。已确认退出则同时保存真实进程结果并收敛公共 task 状态；仍不确定则保留停止中并重试。

同 scope 已终态 command 的重复 kill 返回原事实，不报虚假 not found；跨 org/workspace/conversation 或不属于当前 sandbox 的停止请求仍不泄露。provider 的 not-running 字符串不能代替观察，也不能把已知 exited/236 改为 guessed killed/None。

### 9.3 命令日志确认独立于执行状态

command log writer 区分写入成功和删除临时分片成功。它不是所有 task 必须实现的日志服务。专用目录在运行用户权限下可写；限定路径、检查非目录／symlink，不递归 chown 工作区，也不以 root 跟随代理可控制的路径。

poll 提供候选 cursor，确认日志数据写入后才持久 ack。cleanup-only 失败不重放已确认输出；write 失败保留旧 cursor，记录 retrying。进程可先进入终态，但依赖尾部输出的 completion／monitor exit 事件保持 pending，直到最终日志可读，或有可靠证据证明不可恢复并明确标为 unavailable；临时失败不能直接当作不可恢复。结果就绪由适配器报告给公共投递层，投递重试先检查它，不能用截断结果触发一次无人续办的模型处理。coordinator 在无活跃 run 时继续收集，UI 仍可立即显示真实退出状态及日志恢复中，Todo 可等待尚未交付的最终结果。

不承诺文件与数据库之间 exactly-once：写入成功但 cursor 未提交可能导致重复片段，不能为去重而静默跳过未知数据。日志重试不重发完成通知。现场 orphan 的删除是另需批准的运维动作。

## 10. API 与界面

公共控制与快照使用 `/api/v1/ws/{workspace_id}/conversations/{conversation_id}/background-tasks`，不要求 UI 为不同类型复制列表、Stop 或恢复逻辑。复用现有后台任务区域与 Terminal／command 详情，不新增独立任务中心。

### 10.1 控制与快照

- `GET .../background-tasks` 默认查询 inflight，支持有界 task IDs 查询；`GET .../background-tasks/{task_id}` 返回单条只读快照。查询不顺便 poll 执行方、抢 owner 或消费日志；具体详情按 task kind 返回，不强制存在日志／exit code。
- 同一 conversation 下新增只读 `GET .../background-task-events?delivery=pending|all&cursor=...&limit=...`，返回 `items, next_cursor, has_more`；默认 pending 与 summary.has_pending 仅计算 state ∈ {pending, claimed}，包括这些状态下待重试／对账的源事件。delivered 和 discarded 明确排除，只进入 delivery=all／历史。按不可变 `(created_at, notice_id)` 稳定分页，limit 有上限。每项带 task ID，可用任务详情接口查找并停止已经终态但通知未送达的任务；不依赖历史消息或默认 inflight 列表发现它。跨 scope 的 cursor／ID 不泄露记录，查询不消费事件。
- 现有 `POST .../conversations/{conversation_id}/cancel` 升级为第 5.2 节的主 Stop；请求绑定目标 `execution_generation`，重复请求不能作用于后来开启的新批次。无 active run 但有后台工作时照样受理。202 返回目标批次、停止已受理和清理进度；scope／权限不变，不能只因 Redis 无 active key 就返回“无事可停”。
- `POST .../background-tasks/{task_id}/stop`：持久受理但底层尚未确认结束返回 202；已确认终态返回 200 + 原事实。响应分别表达本地停止已受理、远端取消能力和确认状态，不把 HTTP 成功当作远端取消成功。无法持久受理返回真实错误。终态任务的待发事件也按用户停止规则取消。
- 当前 `sandbox-commands` 列表／快照／kill 调用方在实施时一并切换到公共接口，不保留只做转发的旧控制 API。命令专属工具仍可以接收 command ID，由其一对一关联找到 task 后调用同一 service；不新增一套泛化工具替代全部领域工具。
- 快照提供 task ID、类型、来源／父任务、执行状态、结果引用、deadline、停止意图、通知状态、能力与 revision；command 详情额外提供 exit code 和日志状态。所有类型都不向客户端暴露内部执行句柄、凭据、owner token 或 cursor。
- 历史读取可展示该会话旧 sandbox 的记录；单命令的远端停止仍限当前 sandbox。主 Stop 的通知取消覆盖本会话旧记录，但不向其他 sandbox 发送未授权的停止请求；旧环境已消失以观察结果收敛，无法确认则保留状态未知，不报已停止。不可见 ID 不泄露存在性。
- bootstrap 返回当前执行批次、主 Stop 状态／清理进度与用户 `pending_steers`；另外返回 `background_summary {has_inflight, has_pending, has_cleanup, can_stop}`，从整个可访问 conversation 的持久状态计算，不受任务页、事件页或历史 tail 截断影响。`has_pending` 表示仍需对账／投递的源事件，`has_cleanup` 包括旧批次尚未确认的停止清理和日志收尾；`can_stop` 表示后台工作仍有可撤销的执行／通知权限，不能因旧清理还在就暗示又能取消一次。主 Stop 另外结合现有 active run／HITL 控制状态，不让后台摘要成为 run 状态的第二个权威。后台事件使用与分页接口相同的有界投影，包含 notice ID、来源、task ID／kind、reason、摘要／结果引用、投递状态和 revision，以及 next_cursor／has_more。命令日志位置只是类型详情。实时路径与历史读取使用相同分类，不依赖 UI 临时过滤。

### 10.2 三类信息各有位置

| 信息 | 显示与操作 |
| --- | --- |
| 用户运行中追加的话 | 输入框上方的 steering 列表；可撤回，失败后可恢复编辑；不混入后台事件 |
| 活跃后台任务 | 现有后台任务区域；每个 task 一条状态，按能力提供详情和停止。command 可进入 Terminal 看日志；monitor 输出不新增任务卡 |
| 结果／需报告的进度事件 | 对话中的紧凑事件行，标注后台来源和类型，默认折叠、点击展开结果或可用日志；不是用户气泡，不带撤回或恢复草稿按钮 |

事件创建后可以显示“待处理”，checkpoint 确认后显示“已送达”，取消则显示“已取消”；这些是该事件的状态，不是用户待发送消息。已送达不等于模型已成功处理。按 notice ID 合并源事件与输入历史投影，不能既显示事件行又显示同内容的 user bubble。日志按原始文本渲染，不把输出里的 Markdown／HTML 当作可信交互内容。

同一 command 的多条 monitor 事件按时间聚合显示并保留可展开详情，复用已有速率限制；不同的有效事件不被重试去重误删。通知投递重试只更新状态，不反复新增行或抢焦点。暂时失败显示重试中，不能自动恢复的错误显示在对应任务／事件上，提供错误详情和有效的停止入口，不要求用户删掉一排内部消息。

主 Stop 在仍有 active run、HITL、后台任务或未处理自动唤醒时可用，不能只在模型 streaming 时出现。收到持久受理前显示“正在提交停止”；受理后立即停止表现为正常思考／可继续执行，转为“正在停止／等待执行方确认”。错误就地可见并保留有意义的重试，不能先移除卡片，再因取消失败悄悄塞回来。全部清理确认后才展示停止完成；无法远端取消的任务单独显示能力限制，不隐藏仍在执行的事实。

### 10.3 恢复与后续回复

- run 正常结束后聊天不再显示模型仍在思考，task 卡片继续显示真实后台状态；未完成 Todo 不显示成功。主 Stop 后未完成 Todo 也不伪造完成，旧等待声明失效。
- 删除上一版新增 run waiting 事件的要求；任务状态以数据库快照更新，旧 revision 不覆盖新事实，不重写历史 tool result。
- 原 run 的 SSE 结束后仍须发现后续 automated run。页面可见且 `background_summary` 任一 has_* 为真时，做有界低频快照和 conversation bootstrap 刷新；发现新 active run 后接入现有 run SSE。即使新 run 在两次检查间已完成，也通过历史刷新显示回复。主 Stop 的可用性依据 can_stop 与 active run／HITL 控制状态，不从本页任务／事件是否为空推断。
- 不在 task 刚变终态或某一页事件为空时立即停掉所有刷新：要确认权威 summary 无剩余工作并完成最后一次历史／active-run 对账。主 Stop 后也要观察旧批次的清理，但只读刷新不得启动模型。冷刷新、重连或回到页面时重新 bootstrap；has_pending=true 时展示待处理入口并可分页查找、停止单项，不需要先把全部历史加载到内存。翻页期间源事件可能送达或新增，按 notice ID／revision 合并并刷新 summary，不能以最后一页为空代替全局对账；未知／查询失败不当作全 false。后台页降频，不依赖旧 SSE 长连接。
- 用户 steering 与后台事件都从服务端事实重建；旧客户端缓存中的内部 steering 按权威快照移除，不转成可编辑草稿。源 notice 已送达／取消时，旧投递尝试不能在刷新后复活。
- 中英文文案同步、复用现有组件与主题；实施时在同一 PR 更新 `docs/site/docs/guides/conversations/sandboxes.md`。

## 11. 切换边界与排除项

配套 plan 按公共 task／事件持久契约与 command 首个适配 → 会话控制与通知 → Todo 收尾与公共 UI／恢复组织；命令日志确认作为独立关注点交付。实施前审阅本 spec／plan，不再有“先实现 CubeLoop live wait 才能集成”的依赖，也不把 MCP 或 detached subagent 的实现纳入这一轮。

本轮只实现当前 command 真正需要的公共能力，不为未来类型添加空适配器、额外详情表或调度框架。后续 MCP 与 detached subagent 各自形成独立 spec／plan／实现 PR，接入同一任务、Stop 和事件协议，而不是再次复制公共生命周期。

旧数据不能仅因新代码上线就获得新的自动执行权限。旧 run-lifetime command／wake 保持原契约：原 run 关闭后不自动启动新 run；未送达记录按证据作废，已写历史保留。切换前清点旧 inflight；仍在活动 run 内的旧命令应先完成或经明确操作停止，不能直接批量改成 conversation lifetime。现有 conversation monitors 在未被停止时保留合法通知；批次关联的初始化不得把已取消工作归入新的有效批次。无法确认历史停止意图的旧待发通知不自动重放，保留可见记录供明确处理。

旧内部 steering 按源 wake 关联在读取层归类并退出用户列表；source wake 已 delivered／discarded 时不重新投递，旧失败尝试只作为诊断记录。已经进入 checkpoint 的内容与 metadata 不回写，历史展示根据可靠关联分类；不能仅凭消息正文像系统通知就隐藏一条真实用户消息。

存储切换为每个受管 command 建立唯一 task 关联，将公共控制、owner 与状态迁移到 task，保留命令专属事实；原 wake 记录迁移到统一事件 outbox 并保留 notice ID、dedupe key 和已投递证明。不能因为换了表或 task ID 就重发结果。旧运行数据清点与切换在新旧写入者隔离后完成；最终只有公共 task service 写生命周期，旧 command 状态列和旧 wake 消费路径退出，不长期双写或靠后台同步维持一致。

migration 使用 autogenerate；无可靠历史 deadline 时不猜造过去期限并立即 kill。旧新 coordinator 不同时写同一批记录；保留 provider handles/cursors，部署、数据处理和环境清理需另获授权。

结构升级必须有数据回填门槛：隔离旧写入者后，仅升级到新增结构的指定 revision，保留旧字段／表；完成可重跑的回填及完整性核对后，才允许执行删除旧结构的 revision 并启动新生命周期写入者。Helm init container 与 Compose 的 backend-migrate 都必须使用同一个有门槛的升级入口，不再无条件 `alembic upgrade head`。已有数据的切换是独立维护阶段：先停旧 API／worker／coordinator／排队入口并确认退出，再由持有数据库迁移锁的单一执行者回填和升级；不能把停旧写入者寄托于 RollingUpdate 新 pod 的 init container，也不能让多个副本分别迁移。普通启动只接受已经核对完成的结构／数据；空库在同一迁移锁内通过无旧记录检查后可安装。删除旧字段的 revision 不与尚未具备门槛的版本一起启用。已有库、回填中断重跑、并发升级和绕过回填的启动均需验证。新增 task_id 等回填字段在新增结构阶段允许未绑定；回填核对通过后才收紧约束，历史实例未知仍按 unknown 契约保留，不因收紧约束伪造实例身份。

回填还覆盖旧终态与 outbox 分事务造成的空档：command 已 `notice_state=pending`、但 completion wake 尚未创建。先检查旧 wake 去重键和 checkpoint 的 notice／command 证明；已送达不重放，有明确合法通知权的 conversation-lifetime 工作幂等补一条稳定 completion 事件。缺少继续执行权限、旧 run 已关闭的 run-lifetime 工作或历史停止证据不明时，不提升为新授权，保留 discarded 原因供核对。重复回填或中断恢复不能补出第二条通知。

旧 command 的 sandbox_instance_id 只按可验证的原启动／运行记录回填，不能从当前 UserSandbox.sandbox_id 猜填。无法证明实例归属的 inflight 行保留 unknown 和原句柄，进入明确的人工核对清单，不自动向当前容器 poll／kill、不自动释放 cap。旧会话删除标记必须转换为持久清理意图；已删除会话不会因初始化 generation 而恢复。受理记录的历史来源与批次同样按证据迁移，旧调度／IM 重试不能作为首次新受理越过 Stop。

排除：通用工作流引擎／任意任务依赖图、任意工具自动后台化、MCP 长任务适配实现、detached subagent 重构实现、恢复旧 live Session、跨会话移动任务、另建业务任务分组／按分组取消、暂停所有未来自动化的全局开关、新 provider、自动删除 orphan、trace 检索改造、全站 UI 重做。主 Stop 关闭当前执行批次属于本次范围，不等于暂停独立的定时任务或禁用用户将来的新请求。可选父任务关系只用于执行归属与停止传播，不扩展为工作流依赖调度。

已确认的产品方向：公共后台任务生命周期、command 首个实现；聊天主 Stop 撤销本会话当前执行与后续自动唤醒并按能力停止底层工作；后台事件不显示为普通用户 steering。execute 未指定 timeout 时使用可配置的总执行期限，默认 1 小时；实施已获授权，部署及线上迁移仍需单独授权。

## 12. 验收不变量

1. 小时级后台任务存在时，原 run 能发 Done、释放 active slot；无新输入／事件时不产生模型调用，不依赖原 run heartbeat。本轮以 command 证明跨 worker 恢复，不把这一承诺自动扩展到没有恢复能力的执行方。
2. 原 run 结束后命令仍可观察；worker 重启后从同一 provider_ref 接管，不重新执行 shell command；sandbox 保活不依赖原 run。
3. 同一后台事件在活跃 run 中经内部输入通道接收，无活跃 run 时启动新 run；两条路径都保留来源与 notice ID，不伪装成用户发言；新 run 恢复原会话消息和 Todo，HITL 不被绕过。
4. run 收尾、用户新消息、wake claim 和 checkpoint 的竞争不导致并行活跃 run、丢通知或重复已提交的通知。
5. 前台已交付最终结果的命令不再发 completion；后台交接后即使原工具结果投影失败，命令与结果也可恢复查询。进程终态但输出仍在恢复时明确 result_pending，最终结果就绪前不触发 completion 消费。
6. Todo 的 `wait_for_tasks` 只接受可访问的公共 task ID，经公共查询校验；不存在／已被用户停止／无自动通知的任务、无恢复来源的执行和过期等待声明不能建立新等待许可，command ID 不能冒充 task ID。合法等待不强制续跑、不伪造完成，新输入使旧收尾许可失效；先前有效等待随后被取消按第 30 项收尾，deadline 到期但结果待交付不等于通知已被用户取消。
7. 主 Stop 在有／无 active run、paused HITL 时均能持久关闭本批次，取消 run／子代理／待确认请求及未提交输入，对受管 task 请求停止；本轮 command／monitor 必须继续观察到可靠退出事实或明确报告未知。旧 completion、watch 输出、通知重试和 worker 恢复均不能自动重开执行。已终态但通知 pending 的任务同样覆盖。
8. 旧 run-lifetime notice 在原 run 关闭后不能重开会话；不能把现场的取消复活漏洞通过更名当作修好。
9. 停止受理与实际退出分开，重复 kill 幂等；真实 exit code、deadline、8-command cap、scope 和 owner fencing 不回归。未指定 timeout 默认 3600 秒，配置可覆盖、显式参数优先；非法配置被拒绝，后台／notify=false 不绕过期限，配置变化和 worker 接管不改已有 deadline，monitor 规则不受影响。
10. 日志写入未确认不推进 cursor；cleanup-only 失败不重放；日志错误不把已退出进程显示为运行中。进程退出后日志恢复才成功时，最终通知仍只交付一次且消费者能读到完整输出；可靠不可恢复时交付明确不完整的结果，Stop 后不因日志恢复自动唤醒。
11. UI 在没有旧 run SSE 的情况下发现自动回复；刷新、乱序响应、快速完成的新 run 不造成假成功或漏回复。
12. prompt-cache 稳定、每 run 唯一 Done、required event consumer、durable checkpoint 与 attempt fencing 不被绕过。
13. Stop 与 reservation、迟到 provider_ref、wake claim、checkpoint 提交和新用户消息竞争时，不漏管远端进程、不删除已提交历史、不让旧批次借新批次恢复；重复旧 Stop 不误停新任务。202 与全部确认退出明确分开，provider 故障不会显示虚假停止完成。
14. 单独停止 task 取消其执行权限及未提交通知；已有／迟到受管后代继承停止约束，同级任务不受影响。折叠事件行不触发停止或取消 API。用户 steering 的撤回／恢复功能保留，内部事件不进入这些入口。
15. 实时投影、bootstrap、历史恢复、刷新与跨 run 重试均不将后台事件放入 `pending_steers`，也不生成同内容用户气泡；同一 notice 只有一个产品事件，旧 failed 尝试及已 delivered／discarded 的源通知不在输入框复活。
16. 多个已就绪事件可投递给同一 run，但各自保留独立输入／确认／撤回；停止 A 不撤回 B，A 的提交状态未知时先对账、不重投。失败结果和 monitor 退出事件保留。一个 monitor 的输出不变成多个任务卡；UI 聚合不改变 notice 身份，通知重试只更新状态，原始日志可查看且不被解释为可信页面内容。
17. 任务列表、Stop、事件路由和 Todo 校验只依赖公共 task 身份／状态／能力，不要求 shell、sandbox、exit code 或日志字段；命令详情仍保留真实进程证据。公共状态只有一个写入流程，无双写漂移，原 notice ID 在存储迁移后仍能对账。
18. 本地停止、远端取消请求和确认结束分别表达；不支持取消时仍阻断自动唤醒并显示限制，不伪造 cancelled、不重试不存在的接口。观察超时不等于执行失败，启动回执丢失不触发无幂等保证的重做；deadline 通知与用户停止后的通知丢弃不混淆。
19. 本轮不改变普通 MCP 调用或同步 subagent 的执行方式，不为“预留接入”发布可用的 detach 承诺。未来类型需另行验证恢复、权限、取消和结果契约；父任务／批次归属不能作为绕过审批或并发限制的手段。
20. 删除会话与关闭批次、登记停止意图原子受理；删除后 API 不可见仍能清理原实例，迟到 reservation／句柄不漏管、不重开会话，不影响其他会话。worker 在删除提交后崩溃，恢复仍能继续清理。
21. fixed-target schedule 的新 occurrence 在 Stop 后仍可按独立授权进入新批次；Stop 前已受理的 occurrence、其 busy／IM 重试及旧 notice 不能重开。首次受理与 Stop 竞争的归属唯一且持久，重复旧 Stop 不误停新 occurrence；权限或目标失效时仍拒绝执行。
22. 同一 UserSandbox 行重建为新实例后，旧 command 的 poll／kill／日志收集不落到新实例；无可靠旧实例证据时保留 unknown 和 cap 占用，不从当前行猜造成功／退出。原实例已确认销毁时可正确收敛并释放名额。
23. 全部 task 已终态、待发事件超过窗口且冷刷新时，summary 仍反映 pending；可分页发现 task 并停止指定通知来源，停止其他项不受影响。空页、乱序响应、查询失败不导致提前停止刷新；全部送达／取消后完成最终历史对账再停止轮询。
24. notice A 作为新 run 的首条输入时，在准备阶段停止 A 不调用模型、不写入其通知；提交在途则按 checkpoint 事实收敛。初始提交未决时 B／用户追加输入不进入 Session；初始提交已确认并开放其他输入后，单独停止 A 不取消共享 attempt，B 仍可处理且不重复提交。停止 A 不关闭会话批次，也不依赖 `cancel_input` 能撤回初始输入。
25. Helm 与 Compose 的旧库升级都在旧写入者确已退出、迁移执行者唯一时回填核对，再删除迁移源字段；未回填或核对失败时禁止收缩及启动新写入者。覆盖空库、旧库、中断重跑、并发启动和 pending completion 尚无 wake 的旧记录。
26. 包含后台 notice 的自动或混合轮次不触发自动个人记忆 reflection，且在后续 consolidation 读取历史时仍被排除；日志中的偏好／纠正文案及这些轮次的回复不能由两条自动路径写入个人或 workspace memory。过滤发生在窗口裁剪／文本化之前，无合格历史时不调用模型也不反复消费已扫描计数；普通纯用户历史保持原行为。
27. 同一用户消息来源 ID 重试复用原 generation／run；正文、attachments、model_key 或 reasoning 不一致时明确拒绝，不能篡改已受理请求。首次受理提交后、run 创建前崩溃，再改变默认模型／reasoning，重试仍使用受理事务保存的首次执行快照；权限撤销或原模型不可用时拒绝执行，不静默重选。
28. 用户受理、会话模型选择、附件保护原子提交；提交后崩溃再清理孤儿不删除已受理附件，后台通知看到最新已受理选择，旧请求重试不覆盖新选择。附件清理与受理双向竞争有明确结果，不接受已获得删除资格的对象。
29. schedule／trigger 首次领取后修改定义，再忙碌重试或 IM 转交仍使用当次固定内容和目标策略；之后的新 occurrence／event 使用新定义，当前权限撤销仍拒绝执行。
30. 单任务 Stop 与有效 Todo 等待声明的收尾复查竞争时，不因取消强制追加模型调用、不勾选未完成 Todo、不产生自动唤醒；保留用户取消原因。未经成功校验的新声明或已有新输入不能借用 cancelled 收尾许可，同级任务通知仍正常。
31. trigger 返回 accepted 后，即使 worker 在目标解析前退出，重启仍最终处理同一事件。多 worker claim、lease 到期、目标创建后崩溃和 run／IM 回执丢失不重复建 conversation、不重复受理；不合格或入口校验尚未完成的事件不能被恢复 worker 执行。

公共契约用 command 首个实现验证登记、停止、恢复、事件、Todo 与 UI；能力限制和状态映射保护语义，不靠伪造未来 MCP／subagent 适配器宣称集成已完成。涉及真实 Postgres／Redis／FastAPI 的用例放 e2e，只在最外层执行方注入故障，公共 service／repository／投递层用真实实现。前端业务流覆盖“用户 steering 与后台结果同时到达 → 重试／刷新仍分开 → 主 Stop → 迟到完成不再续跑”，以及单任务停止、失败反馈、事件展开不触发取消；不以静态元素计数代替契约验证。长等待用可控时钟推进与重启验证，不真等数小时；`real_llm` nightly 另查模型是否仍主动用 ps/sleep 忙等。设计文档通过检查不等于这些运行时验收已通过，实施需记录实际验证证据。
