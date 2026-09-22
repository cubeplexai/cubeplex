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

创建 run 的入口先持久预分配并绑定稳定 run ID，再调用 RunManager；不能在 start_run 成功返回之后才补 admission.run_id。同一来源并发、启动回执丢失或原 run 已终态后重试都返回原绑定；run 的持久启动／结束证明参与对账，Redis active slot 消失不代表尚未执行。没有可靠失败前未启动证明时不重放模型／工具，既不能改用新 run ID，也不能用同一 ID 重新执行已完成的 run。只返回已经启动／结束或被撤销的原绑定时，仍检查当前访问权限，但不因原模型已移除而拒绝读取回执；模型及凭据可用性是实际执行的门槛，不是读取幂等结果的门槛。

不创建模型 run 的用户快捷操作也受同一来源幂等保护。`install ...` 首次受理固定为快捷操作分支，保存安装目标／结果及稳定的合成消息 ID；安装变更与操作回执原子提交，checkpoint append 使用这些 ID 对账。安装后或消息写入后响应丢失，只补尚未提交的消息并返回原 SSE 结果，不重复安装、追加第二组历史或改走模型 run。Stop／删除与执行前权限复查仍生效；已提交的安装副作用不假装回滚。

schedule occurrence／trigger event 在首次持久领取时保存已渲染内容及影响执行的非凭据参数，包括当次目标策略和模型选择；目标 conversation 尚未确定时先随源记录保存，目标确定后将该快照绑定到 admission，再排队。忙碌重试、IM 转交和 worker 恢复均使用同一快照，不重新渲染可变模板或读取修改后的 prompt；编辑定义影响之后的新 occurrence／event。当前停用／撤销授权、目标删除等控制仍重新检查，快照不授予永久执行权限，也不重开旧 generation。

快照还固定原执行 actor；trigger 在持久受理事件时，在定义行锁内保存 run_as_user_id，后续修改只影响新事件。worker／IM 重试校验原 actor 当前的账户、成员身份和目标权限，不替换成定义的新 actor，也不借编辑获得不同的个人上下文。

trigger 的 202 受理还必须有持久的后续消费者，不能只依赖进程内 create_task。完成入口校验后，将可执行事件、冻结内容及排队状态持久提交，再返回 accepted；用于去重／审计但尚未通过入口校验的记录不自动获得执行资格，重复请求不能把未完成受理误报为已排队成功。专用 trigger worker 启动和运行中均领取可执行事件，用有期限的 claim 接管崩溃遗留工作；重试时间和尝试次数持久保存。conversation 目标一旦创建就与事件在同一事务绑定，重试不另建会话；run／IM 转交沿用稳定来源和已绑定 admission，回执丢失先对账，不重复执行。不绕过过滤、限流、停用或当前权限，也不改变调度计划或建立新通用工作流引擎。

删除 trigger 与事件受理／claim 串行：先持久停用并标记删除，取消未交接事件，普通接口隐藏它；不能立即硬删 TriggerEvent。已绑定目标、admission、run 或 IM handoff 的事件保留取消及对账记录，消费者只做取消／确认，不再启动新工作。已经交接的 run 保留历史，不因删除 trigger 误停同 conversation 的其他工作；需要停止当前会话仍走主 Stop。只有没有未决交接及清理时才能按依赖顺序清理源记录。

删除 schedule 使用同一源定义／occurrence 裁决：定义行锁内持久删除标记、清空下次触发，并取消未开始执行的 occurrence，包括已 claimed、busy 重试和已进入 IM 队列的项。领取、目标绑定、执行启动资格与删除串行，不能只在首次扫描时检查 deleted_at；冻结的 prompt 不授予删除后的启动权。已有 admission、预分配 run ID 或交接回执未决时保留来源证明并对账，不重新派发。已确认开始执行的 run 保留历史，删除定义不等于主 Stop，不取消同会话的其他工作；仅完成队列交接还不算开始执行。清理完成前不物理删除 occurrence。

首次受理与 Stop 使用同一 conversation 锁串行：固定会话的 occurrence 被领取并登记待执行时就绑定批次，不等 worker 真正开始调用模型；先受理则属于 Stop 关闭的旧批次，后受理且来源有独立授权才可进入新批次。Stop 后不得把旧 occurrence 的 busy 重试、IM 队列重试或恢复任务解释为下一次触发。经 IM 转交的调度仍保留原 occurrence 身份。这里只补入口分类、幂等绑定和停止边界，不改变调度计划、missed／busy 策略或引入新调度引擎。

停止接口持久受理后返回 202；这表示平台内旧工作的继续执行权限已撤销，不表示底层执行已经消失。UI 立即显示“正在停止”，支持远端停止但尚未确认的任务显示“停止未确认／正在重试”，全部确认后才显示“已停止”。如果适配器明确不支持远端取消，显示“已停止后续处理，远端任务无法取消”；不显示取消成功，也不无限重试一个不存在的取消接口。底层状态未知则继续如实显示未知。不能在持久写入失败时返回成功，不能把清理失败或取消后的迟到结果变成新的模型唤醒。

单独停止 task 同样先持久写入停止意图与通知取消标记，再异步联系适配器；所有入口共用该流程，但不关闭整个会话批次。即使任务刚刚自然完成，也可以取消其尚未送达的通知，必须保留真实结果，command 的 exit code 不得改写。已提交到 checkpoint 的输入和已经发生的副作用无法撤回；主 Stop 会取消正在处理它的旧 run，保留历史事实，不承诺回滚。停止 task 的后代约束在受理新子任务时也检查，不能只扫描一次已有孩子；本轮 command 没有受管子任务，不改变当前同步 subagent 的实现。

删除会话不是仅停止通知。复用主 Stop 的持久控制流程，在写入 `deleted_at` 的同一事务关闭批次、记录受管任务停止意图和取消未提交通知；事务失败则不报告删除成功。既有删除权限与软删除返回契约不变，返回成功只表示删除和停止意图已持久受理，不表示远端已退出。删除后普通会话／任务 API 仍返回不可见；coordinator 按原 scope、实例身份和已登记清理权限继续观察／取消，包括迟到启动句柄及更早批次未完成的清理，不依赖用户重新访问会话。不得级联删除清理所需的 task、执行句柄、通知对账证明和审计记录；deleted 标记永久阻止新用户、调度或恢复为该会话重新开批次。删除与 reservation／首次受理使用同一锁，清理不能触及其他会话的任务。

删除整个 workspace 也不能绕过这些保证。先持久标记工作区正在删除，关闭新受理／调度入口并对所属会话登记停止，再异步清理和对账；新受理与该标记的裁决必须串行。清理未确认时返回明确的 cleanup_pending（409，可重试），不删除 membership、sandbox、task 或投递证明，不谎报 workspace 已硬删除；后台按持久删除标记继续清理。全部完成后才按外键顺序删除事件、command 详情、task、admission 及原有子表，最后删除 workspace。仅有普通消息 admission 的工作区也能删除；失败可重入，组织删除等复用路径遵循同一门槛。

`auth/delete-account` 同样先持久标记账号正在删除、阻止其新受理及旧工作继续执行，并停止／对账该 actor 在所有 scope 发起的工作。本人独占的会话按既有范围清理；仍有其他有效参与者的共享会话／topic 不因创建者删除账号而删除，只取消该 actor 的 run、task、未提交输入和自动来源，不关闭整个共享会话、不取消其他参与者独立发起的工作。清理未完成返回 409 cleanup_pending，保留身份行、成员、凭据及句柄供受限清理，不先删 checkpoint／trigger event／IM receipt；删除状态下仅允许查询或重试自身删除，不允许新业务执行。原始身份和待删范围固定，worker 重启继续清理；对账完成后按外键顺序删除相关生命周期行，再清理独占数据，不按 creator／uploader 直接批量删除仍被共享的历史。仅有 admission 而没有 task 的账号也必须可删除。

共享资源在账号删除的初始权限事务中完成归属转移，不把非空 user 外键留给最后的硬删除处理。topic 接任者为仍有效的 owner，原 owner 离开则按下述规则同步选出；独立共享会话选最早加入的有效参与者，同时间按 participant ID 排序。接任者必须是未删除中的用户，仍有有效 org／workspace 成员资格及原资源访问权，不能为方便清理给陌生成员新增访问。该事务一并转移保留资源的 `Topic.creator_user_id`、`Conversation.creator_user_id` 和共享 `UserSandbox.user_id`，只改需脱离待删用户的归属引用，记录原归属和接任事实；原 run／task 的 actor 不变。保留 conversation／topic ID、sandbox 行与原实例、scope、已有 volume 配置、文件、checkpoint、artifact 和其他参与者任务；后续连接／revive 按新归属读取，不能用旧缓存再写回 A。共享历史里已 attached 的附件保留原对象，删除上传者后 uploader 可为空，显示为已删除用户而非伪称由接任者上传；未附加的个人附件仍清理。

此转移不赠送 A 的个人环境、MCP 凭据或秘密，也不因 B 接任而把 B 的个人凭据自动注入共享实例。A 的 egress 引用在撤权时失效并清除缓存；B 原有合法引用继续有效，后续命令按实际执行身份重新解析授权。依赖 A 私人凭据的旧命令可能报授权失效，须保留真实结果，不承诺它在撤权后仍能访问秘密；不得因此 kill B 的整个实例。仍采用 creator／user scope 并共享 A 个人 sandbox 的旧 topic，不做隐式个人目录转让：账号删除预检返回明确的 `shared_personal_sandbox_in_use`，未受理删除且不置 deleting，列出本人可见的依赖目标。用户需先结束这些共享关系并完成相关执行清理，或另行明确授权资源迁移；本轮不新增个人目录迁移功能。该预检与创建共享关系／选择个人 sandbox 串行，不能检查后再出现新依赖；页面说明阻塞原因，不将其当作 cleanup_pending。

接任候选和其成员资格与撤权／删除共用锁序，在提交前重查；候选失效则回滚重选，不能留下半转移或将资源交给也在删除的用户。没有其他有效访问者的资源进入独占清理；只有子会话访问者却没有合法 topic 接任者等不能自动转移的情况，在受理前返回 `shared_owner_transfer_required`，先由原权限允许的管理操作解决，不伪造新 topic 权限。已有资源级删除则继续原清理，不以接任取消既定停止／删除。硬删除前再次验证不存在引用待删用户的保留资源，保留资源不得进入账号 bulk-delete 集合。重启及重复删除复用已提交的转移事实；资源归属与执行身份始终分开。

共享会话的搜索数据也是保留资源：`ConversationChunk.creator_user_id` 及所有状态的 `EmbeddingJob.creator_user_id` 与 conversation 在同一归属事务转移，保留已有索引内容、向量和消息范围，不先删索引再等重建。运行中的索引任务失去原领取资格，按原消息范围重新排队；旧 worker 的迟到成功／失败不能覆盖新归属或新领取状态。索引写入和 job 完成在同一受资格校验保护的事务提交，模型请求在锁外；新入队和恢复都读取 conversation 当前归属，不信任旧调用上下文中的 creator。独占资源删除则取消索引工作并清理其行，旧 worker 不得重新插入。搜索可见性仍按当前会话权限判断，不能将索引 owner 当作新增访问授权；B 在转移后仍能搜到原共享历史。

账号硬删除的外键清单覆盖全部用户引用，不限于新增 task 表：共享资源及索引转移，保留对象的纯作者署名可置空，个人凭据／令牌／身份链接与独占数据按依赖清理，执行与交接证明必须先对账。禁止靠关闭约束或级联删除保留资源绕过遗漏；验收创建实际引用并最终删除用户，检查共享数据存活及旧后台写入不能复活这些引用。

公开分享的授权不只存在于数据库。artifact share、artifact／attachment Office 预览以及 sandbox 下载的 Redis 令牌，都必须绑定签发 actor、原授权实例／版本及精确资源；IM 签发使用原 admission 的 actor，不能采用后来修改的 connector 身份。公开页面和每次文件请求均重新检查该身份、原授权及资源当前资格。账号删除或撤权事务一旦提交，A 的失效授权就不能再用；不依赖 Redis 批量删 key 成功，也不等 7 天 TTL。硬删用户后仍拒绝，重新加入不恢复旧授权，B 独立合法授权不因 A 的删除被撤销。历史令牌无法证明签发身份／授权时在统一切换后拒绝，需重新签发，不从资源的新 owner 猜补。预览与分享 API 保留分开的入口和原 TTL，复用下面的校验服务；响应禁止公共缓存，已下载的文件无法追溯收回。

撤权的共同规则还覆盖已经发出的授权和已打开的连接，不只约束新业务 HTTP 请求。原身份／权限实例／版本用于确定要检查什么，不能代替当前资格；角色降级、成员重加和资源归属变化不能使旧授权自动复活。新请求做当前资格检查，长连接每批数据检查有界资格租约，异步写入在最终数据库事务中与撤权串行；外部服务调用不持数据库锁。清理 pending 期间也必须遵守下表，不能等硬删除才生效。

| 既有入口 | 撤权后必须发生什么 |
| --- | --- |
| OrgInviteToken | org 成员移除、管理员降级或账号删除时，撤销该人在对应 org 尚未使用的邀请；接受邀请必须校验原签发者当前管理员资格，消费令牌与授予成员身份同事务，不允许旧邀请在撤权后增员／提权 |
| ConversationShare | 初始撤权事务停用受影响签发者的分享；公开／org／workspace 分享正文和复制的 artifact 都检查签发授权，不能因 public 提前跳过；后台复制完成也不能再激活已失权的分享 |
| sandbox browser／terminal 面板 | 签名令牌包含原 actor、授权、sandbox 行／原实例、端口和路由版本；HTTP 与 WebSocket 不只验签名。已连接 relay 在撤权或过期时断开，不能一直用到浏览器关闭 |
| run SSE 等长连接 | replay、coalescer flush、live tail 都检查订阅者当前访问权；只断开失权订阅者，不停止 B 的共享 run，不向公共 run 写假 error／done。重新连接仍要授权，不能用 Last-Event-ID 绕过 |
| MCP OAuth state／callback | 保留签名、PKCE 和浏览器 ticket 检查，另校验原 actor 及原授权；换令牌前和写 vault／grant 的事务内重查。失权的旧 flow 不得新增／覆盖 user、workspace、org 凭据，外部调用返回后失权则不落可用凭据 |

长连接使用每条连接共享、最长 5 秒的资格租约，不按帧／token 频率查询数据库。短事务续租，每批转发只检查本地租约仍有效；两向 WS 共享一次检查，独立 watchdog 在租约／令牌到期时停止两向转发并关闭，即使连接空闲或 I/O 阻塞也生效。撤权通知尽快废止租约，漏通知时也不能续到超过一次已授予的 5 秒窗口；持久撤权立即阻止新请求和新租约，但既有连接明确存在最长 5 秒的传播窗口，不再承诺零延迟断流。查询失败／超时或租约过期即关闭，不沿用旧许可，不持小时级 DB 事务。run SSE、user event SSE、sandbox 下载／面板和 admin provider 流均按各自原身份／scope 校验；跨 workspace 用户事件只能使用对应 scope 的有效租约，不能套用另一 scope。前端失权结束后停止自动重连；此前已转发数据不能收回，业务写事务仍即时检查，不使用这项连接租约宽限。

Google／企业 SSO 的旧登录流程不能在账号硬删除后自动重建同一账号。账号删除受理时，为已知外部身份及按现有匹配规则规范化的邮箱保存不含明文身份的短期隔离记录，记录不引用待删 User。resolve_identity 的更新、绑定和自动创建路径都在最终事务检查该记录及原登录 state 的绝对期限，不能只依赖进入 callback 时 Redis state 尚未过期；创建用户、身份绑定和相关 bootstrap 数据不能分开提交。删除期间保留隔离记录，硬删除后至少保留到所有旧 state 必定过期（当前最长 300 秒，加时钟容差）；此短窗口内同身份自动登录／重建明确拒绝，过期后按正常策略允许新流程，不永久封禁。旧 state 无论 provider 返回多晚都不能再创建用户／组织／workspace；历史 state 缺少可验证期限时切换后拒绝。隔离记录到期有界回收，不无限保留可关联身份的信息。

共享 run 中其他人的输入必须逐条对账：删除 A 先撤销 A 的 attempt 权限，再核对 B 投给它的 steering。只有已证明原 attempt 不能再提交、且输入未进 checkpoint 的项，才保留原 steer ID／正文／发送者，以 B 当前权限等待 slot 释放并启动后继 run；目标投递 attempt 与源输入身份分别保存，不改写普通消息已绑定的原 run ID，也不复制一条用户消息。无法证明提交状态时继续对账。已经提交的 B 输入保留历史；若 A 的中止使其处理未完成或结果未知，明确显示“处理中断”，由 B 主动继续，不自动重放可能已有副作用的模型／工具。checkpoint 已提交只证明接收，不证明处理完成；不得静默将 B 标记取消或已处理。

账号删除 UI 将 409 cleanup_pending 显示为持续的“正在清理”，不恢复成可再次开展业务的普通错误状态。本人删除状态通过受限鉴权的查询／幂等重试可恢复；前端有界退避查询、允许手动重试，刷新后仍进入删除进度。收到明确删除成功回执才显示完成并清理 auth／workspace 状态、跳转登录；401 只表示会话失效，不当作删除成功。断网或清理未知保留 pending 提示，不声称取消成功。workspace 删除页面同样区分 cleanup_pending 与普通失败，删除确认后再移除工作区；关闭进度界面不撤回已提交的删除。

删除回执必须活得比目标资源长。客户端确认删除前生成并暂存在 sessionStorage 的 256-bit 随机 deletion_token，首次删除请求在原密码／管理员权限校验后，将其摘要与删除操作绑定；重试沿用同一 token，不重新创建操作。删除操作记录不以待删 user／workspace 为级联外键，硬删除与写入 completed 回执在同一事务提交。只读状态接口可仅凭该 token 查询对应操作的 pending／completed，不依赖已经删除的身份或 membership；账号和 workspace 使用独立 handler，workspace 路径必须与操作绑定的 ID 一致，不返回个人资料或其他资源。token 不进 URL、日志或审计正文，数据库只保存摘要。终态回执保留 30 天，过期或丢失凭证明确显示“无法确认删除结果”并停止自动查询，不伪报成功或无限 pending。这样 worker 在两次查询之间完成删除、以及删除成功响应丢失，都仍能取得明确终态。

未完成删除由专门的恢复 worker 推进，不靠用户保持页面打开或重复 DELETE。每个应用进程在启动时及之后定期扫描 pending 操作，按数据库 claim／租约保证单一清理 owner；复用账号、workspace、成员、IM 和 sandbox 的原分阶段处理器，完成父子对账和安全硬删除。请求内最多推进一小步后即可返回 pending，状态 GET 始终只读；worker 崩溃由租约到期接管，旧 owner 不能写终态，迟到 provider／handoff 证据仍保留。扫描、并发和重试有界，持续 unknown 不伪造成功；清理所需权限来自已受理的原操作，不要求待删 actor 恢复登录资格。终态回执的 30 天回收是另一项工作，不能代替这个恢复 worker。

成员权限撤销也要关闭其后台执行权。workspace 移除成员／主动离开及 org 移除成员，先在权限事务持久标记撤销生效，阻止受影响 actor 的新受理、工具开始和输入投递，再停止并对账其在相应 scope 的旧 run／task／来源；不可只丢弃最后的通知。成员行保留到清理完成，但撤销标记立即使普通鉴权失效，只允许清理服务使用已有句柄。org 撤销覆盖该 actor 在该 org 的全部 workspace，不触及其他 org；重加成员不能恢复旧 admission。并发移除／受理串行，清理未知返回 cleanup_pending，授权管理员（离开操作为原本人受限查询）可以查询进度，最后再删除成员行。

成员清理复用独立删除回执，不能把待删 membership 行当作唯一进度证明。首次移除／离开前保存 deletion_token，原权限校验后将其摘要绑定操作、scope、原 actor 及原 membership ID，关联不使用目标级联外键；删除成员行与 completed 同事务。本人离开状态接口仅按 token 和固定 workspace 核对，不要求 membership 仍存在，不恢复业务权限；管理员移除使用独立的 workspace／org handler。回执保留 30 天，刷新、两次查询间 worker 完成及成功响应丢失均能确认终态；凭证丢失／过期明确不可确认。旧操作重试只能清理原成员实例，不能移除后来重新加入的成员。

执行身份兼容规则同样适用于用户 steering。A 的 run（含后台续办 run）只能接收 A 的新增输入；B 的消息仍先持久受理，但排队等待 slot 释放后以 B 的当前权限启动后继 run，不能调用 A 已组装的 MCP／sandbox 凭据。身份不可可靠确认时不注入。数据库领取、准备缓冲、pub/sub 和 Session 提交都检查同一 actor 绑定，不能只在 HTTP 入口拦截；用户仍能撤回自己的未提交输入，Stop／删除仍可撤销旧批次。UI 区分“等待当前运行结束”和已经提交处理。

HITL 的回答和审批也不是跨身份委托：只能由原 admission 的 actor 提交，并重新检查该 actor 当前权限。其他会话参与者可以看到待确认状态，但显示“等待发起者回答”，不能通过回答使原 actor 的凭据继续执行。拒绝发生在 resume claim 前，保留原问题和运行身份。按会话权限执行主 Stop 不属于回答／审批；它撤销并清理工作，不产生新的模型回合。

topic 删除（归档）同样必须停止所属会话，而不是只隐藏入口。归档事务串行阻止新建会话／新受理，关闭已有会话批次并记录 run／HITL／task 的停止和通知取消。隐藏后后台仍按原 scope 清理，重启不丢意图，其他 topic 不受影响。

IM connector 删除先停用并持久标记，与 webhook／队列入队及 worker claim 串行。未开始执行的交接取消；已有 admission、启动回执或 schedule／trigger handoff 未决时，保留 receipt、queue item 和来源绑定，不能由级联删除抹掉去重／核对证据。worker 在删除状态下只核对和清理，不重新执行旧交接；已执行的会话历史及无关工作不受影响。全部核对完成后才硬删 connector 及其交接记录，必要凭据仅保留给清理流程；删除进度可查询／重试。

IM 删除 API 与客户端配套切换：409 cleanup_pending 是已受理的持续清理，200 completed 才是删除完成。使用同一独立删除回执及预先保存的 token，状态查询在 connector 被硬删后仍有效；workspace 和 account ID 必须匹配原操作。前端保留“正在清理”项，单请求、有界退避查询并提供幂等重试；刷新后继续恢复，网络失败／401／404 不伪报成功，只有明确 completed 才移除账号并显示成功提示。关闭界面不取消清理，删除中的 connector 不能重新启用；匹配的 IM 使用文档随实现更新。

所有删除入口按 kind、scope 和目标实例去重，不只按客户端 token 去重。多个页面或管理员使用不同 token 删除同一目标时，原删除鉴权和目标锁内只建立一个清理操作；每个成功受理的 token 摘要及其原操作人单独绑定该操作，均可读取同一个终态，但不能查看其他凭证或借此取得删除权限。重新加入的 membership 是新实例，不能合并到旧操作。硬删除先完成、凭证尚未被持久受理的请求必须明确失败，不能返回无法兑现的 pending。

删除回执由应用启动和每小时触发的有界清理任务回收；多个 worker 通过数据库行锁跳过彼此正在处理的批次。只清理 completed 且 completed_at 已超过 30 天的操作及其凭证摘要，不按创建时间清理，不删除仍 pending 的恢复证明。状态接口即使遇到尚未扫除的过期行也拒绝凭证；清理失败可重试，保留期限不依赖有人再次访问界面。

父资源删除同时接管已受理的子清理，而不是先级联删除其目标。workspace 的范围包含成员、IM connector、topic 参与者和 sandbox 清理；账号删除覆盖实际将被其删除的成员／参与者／sandbox，以及所有 `acting_user_id=A` 的 IM connector，不能顺带删除其他 actor 的共享资源。账号受理在身份／connector 锁下停用这些 connector，并创建或接管原分阶段删除操作；来源 handoff、queue 和 receipt 未决时不硬删 connector 或 user，不以换成 B 执行来绕过清理。新建／修改 connector 的 acting_user 与账号删除共用门槛，不能在枚举后新增 A 的引用；其他 actor 的 connector 继续服务。子操作保存原 user／workspace／topic／目标实例 ID，不靠仍存在的目标 FK 找回。父操作在固定权限／目标锁序下关闭新增子删除受理、领取并隔离旧子 worker，复用同一清理事实；有未决启动／交接／远端停止时，父子均保持 pending，不能先删恢复证明。逐项清理可独立完成，不要求先等父操作完成；最后硬删除和所有受影响子操作的终态回执在同一事务落库，已存在的 token 继续查询。父已开始清理时，尚未受理的新子请求明确返回上级清理中，不能冒充子删除已受理；子先完成时父复用其终态，不重新清理。多个父删除重叠时也只使用原子操作和单一清理 owner，不形成互相等待父完成的依赖环。

topic 参与者移除也要持久撤权和清理。移除事务按实际访问规则逐会话判断：只有 actor 不再有任何有效 topic／conversation 参与资格的会话，才撤销其原 admission、停止其 run／task 并取消未提交通知；仍有 ConversationParticipant 访问权的会话及其他 actor 不受影响。先标记原 TopicParticipant 撤销并关闭受影响旧执行权，再在锁外清理，最后删除原参与者行；未决返回 cleanup_pending 并沿用独立 token 回执。授权查询必须排除撤销行，重新加入不能恢复旧 admission；清理完成前不原地复活参与者。新增会话／参与者、reservation 与移除串行，迟到 provider_ref 继承停止和通知取消；迟到的旧 run 不能通过补记参与者行恢复权限。不为移除一个人销毁共享 topic sandbox，不能确认某个任务已停止时继续显示清理中。

若离开的是最后一位有效 owner，标记撤销的同一事务就把最早加入的有效剩余参与者升为 owner，按 joined_at、participant ID 稳定排序；排除已撤销、账号删除中或 org／workspace 资格失效者。候选资格检查与撤权串行，有其他有效 owner 则不额外晋升，无有效参与者才允许无人接任。清理即使长期 pending，新 owner 也能立即管理 topic；最终删除旧参与者行不得再晋升一次，重新加入者按新成员规则处理。账号／上级成员撤销调用同一规则；并发 owner 离开／转移不产生有有效成员却无 owner 的窗口。

owner 角色接任不等于 sandbox 已换归属。本人主动删除账号或离开 topic／workspace／org，可以在受理前提示先解决个人 sandbox 依赖；但管理员移除／安全撤权不受此依赖或接任失败阻塞。后者必须在初始事务撤销原成员执行与访问权，同时持久关闭受影响的个人 sandbox 共享路由、停止依赖该路由的工作并登记清理，返回已撤权的 cleanup_pending。不能因为资源仍在清理而保留 A 的成员权限；已失权 A 也不能通过新建共享关系重新取得否决权。

管理员撤权后，B 的 topic／会话历史和不依赖该个人 sandbox 的工作仍可用，但相关 sandbox 工具／预览明确显示“原共享环境已失效”，不得继续连接 A 的个人目录或隐式改用 B 的个人目录。原实例上受影响的任务、迟到启动和工具缓存受同一持久路由关闭约束；必要时中断 B 对此环境的工作，这是安全撤权的明确例外，不假称完全无影响。只清理撤权实际影响范围，单 topic 撤权不得顺便销毁同实例上其他仍合法的私人工作；不能确认任务退出就保留清理证据与 pending，权限仍保持撤销。无接任者仍可完成撤权并保持环境不可用，不以等待迁移拖住清理终态。继续使用 sandbox 可由有效成员另建 dedicated topic，或另行明确授权迁移；本轮不新增原 topic 换绑／个人目录迁移接口，已关闭路由不会因新 owner 或重新加入自动恢复。dedicated／conversation scope 能安全接任时同事务转移实际归属，否则先禁用该资源；不能只修改角色。前端分别展示主动退出的未受理提示、管理员撤权已经生效与环境清理进度。

### 5.3 等待预算、执行期限与资源期限分开

execute 的配置与显式 timeout_seconds 共用技术上限 2_147_483_647 秒（有符号 32-bit 秒数），默认仍为 3600；这不是把默认一小时变成硬上限。配置加载、工具 schema 和受理服务均拒绝越界值，再计算 tz-aware deadline；计算仍保留日期溢出的防御检查，不把异常值静默钳制或当作无限期。monitor 的既有期限上限保持不变。

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
| 有可接收输入且执行 actor 与源任务相同的 active run | 持久化带后台来源的内部输入，经公共 Session 输入通道在安全边界接收；不变成用户 steering |
| active run 属于另一参与者 | 保留 pending，等待 slot 释放后以源 actor 新建 run；不能借用另一人的凭据、工具或个人上下文 |
| run 正在准备／收尾，暂时不能接收 | 保留 pending，等待原 run 接收或释放 slot 后重新路由；不能同时开第二个 run |
| 无 active run，且没有持久 HITL 待确认 | 经现有原子 active-slot claim 创建 automated run，加载同一 conversation 的 checkpoint |
| 存在 HITL 待确认，包括 Redis active key 已过期 | 保留 pending，不把审批消息当作已回答，不绕开 HITL 新建 run |
| 会话已删除、发起者权限失效、通知被取消或其执行批次已停止 | 不投递，未提交的事件记录 discarded 和原因；不换成 system actor 绕过权限，不移入新批次 |

新 run 使用现有 conversation 模型／reasoning 配置和原发起者身份检查。用户消息与通知同时到达时由现有 active-run claim 决定谁先取得执行权；失败的一方按自己的既有排队／重试协议继续，不能覆盖用户输入。

同一次调度中，属于同一 conversation、同一有效批次、同一执行 actor 且已就绪的通知可以按稳定顺序投向同一个 run，减少重复创建 run；本轮不把多个 notice 合成一条物理输入。每个 notice 各有独立的 InputEnvelope／投递 ID、来源和 checkpoint 确认。空闲时用其中一个 notice 作为新 run 的初始输入，其余经相同 admission 校验后分别投递；不保证恰好只有一次模型调用，不为凑批长时间等待。monitor 在生成源事件前保留原限流／输出合并规则；源 notice 一旦生成，其身份和成员不因投递重试而改变。

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
- 普通用户轮次的 reflection 是正常结束后的可选后处理，不为它保留 live run 或 active slot。它只接受原 attempt 正常完成及持久清理结束的证明，并在模型／工具边界重新检查原 actor、generation 和完成凭据；Stop、删除、失权或凭据丢失／替换后不再开始提炼工作。取消、失败、HITL 暂停或未清理完成不能授权它；正常结束本身不应误禁用已有自动记忆。已保存的记忆不因 Stop 回滚，失败的提炼不自动重放。
- reflection 的模型／工具检查不能代替写入事务检查。每次 memory_save／memory_update 将原受理身份带入数据库事务，先锁定并复查当前权限与原 generation，再读取／修改记忆，直到整次操作提交才释放锁；去重更新时间、容量清理和最终写入不能中途自行提交。Stop 先提交则写入被拒绝；记忆事务先取得权限锁则先完成，Stop 随后提交，之后不再有旧权限下的记忆写入。事务失败整体回滚，不保留部分容量清理结果；不在持锁期间等待模型。
- 自动 memory consolidation 同样排除后台来源：读取持久历史时，先按输入来源与 run 关联识别并排除含后台 notice 的整个轮次，再裁剪窗口及转成 role／text。仅跳过后台 run 的即时调度不够，后续普通用户 run 再次合并历史也必须使用相同过滤；旧通知按可靠 wake 关联分类，不靠正文猜测。窗口边界和缺失关联不能把孤立的后台回复误算为纯用户轮次，无法可靠分类的片段不用于自动记忆。无合格历史时不调用提炼模型、不写个人或 workspace memory，但按现有 cutoff／consumed 规则完成本次扫描，保留扫描期间新增 run 的计数，避免反复扫描同一批排除内容。正常纯用户历史的合并保持原行为，不回写 checkpoint。
- 自动 memory consolidation 还必须绑定触发它的原 admission／attempt／generation，不因来源是纯用户就跳过权限校验。正常结束回执完成后才开始，模型调用前及应用结果的事务中复查原身份；整批 extract／merge／archive、去重和容量清理在同一受保护事务内提交，不允许逐项 commit 或吞掉写失败后提交部分结果。Stop、删除或权限撤销在模型等待期间生效时，整批结果不得写入个人或 workspace memory；失败不自动重放旧执行，后续合法 run 仍按原 cutoff／consumed 规则决定新的扫描。
- 两条自动记忆路径的权限锁均覆盖 topic 的归档状态及授予访问权的 topic／conversation participant 行；不能只锁 workspace membership。检查期间会话移动到另一 topic，或只能依靠尚未锁定的新参与者记录时，重新受理而非沿用旧检查。topic 归档／移除参与者与写入事务必须串行。
- 结束状态对外可见时，原 worker 仍需持有有界清理租约，直到结束回执落库和 slot 释放；不能让快速下一次发送抢走清理权并导致正常后处理失去结束证明。HITL 暂停必须及时解除清理租约以便回答，worker 崩溃后租约到期仍可恢复。
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

自助 sandbox restart／delete 也走受管任务清理，不能只调用 provider kill 后隐藏行。首先在 sandbox 行的短事务写入持久 teardown 标记和原 sandbox_instance_id，阻止新 reservation、get_or_create／revive／保活；提交后再按 conversation → sandbox → task 的固定顺序给该实例的全部任务登记停止和通知取消，不能拿着 sandbox 锁反向取得 conversation 锁。共享实例包含其他会话／actor 的任务，界面须明确影响范围，但不停止其他实例上的工作。已在途的 start 回执仍登记到原实例并继承清理意图；只有可靠的进程退出或原实例销毁证明才可完成，kill 失败／provider 不可达保持 pending 和恢复凭据，不报告成功、不先替换为新实例。provisioning 或实例身份未决时，入口返回明确未受理的 409，不能先隐藏再等待迟到容器。

restart 和 delete 均有持久操作回执，绑定稳定 UserSandbox 行及原 provider 实例；相同操作重试不作用于后来创建的实例，同一实例的同类请求合并，不同 restart／delete 意图并发时后者明确未受理、待前者收敛后再请求。restart 确认旧实例销毁后保留行／PVC供后续合法请求重新分配，delete 确认清理后才完成软删除；均不自动删除 PVC。父 scope 删除可接管这份清理，终态区分旧实例清理完成与目标已随父删除，不能据此把已删资源显示成可重启。202／cleanup_pending 只表示持久受理，客户端刷新后可继续查询，不将所有 2xx 都显示为操作完成；后端入口、前端状态机、翻译及 sandbox 使用文档同 PR 交付。

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
- 不在 task 刚变终态或某一页事件为空时立即停掉后台工作刷新：要确认权威 summary 无剩余工作并完成最后一次历史／active-run 对账。之后，可见会话仍每 30 秒执行一次 bootstrap 基线发现，覆盖未来 schedule／trigger 才产生的新 run 和已经完成的回复；summary 全 false 不是永远不会有自动工作的证明。隐藏页面暂停基线，重新可见立即 bootstrap；每会话最多一个在途请求，失败指数退避至最多 120 秒。主 Stop 后也要观察旧批次的清理，但只读刷新不得启动模型。冷刷新、重连或回到页面时重新 bootstrap；has_pending=true 时展示待处理入口并可分页查找、停止单项，不需要先把全部历史加载到内存。翻页期间源事件可能送达或新增，按 notice ID／revision 合并并刷新 summary，不能以最后一页为空代替全局对账；未知／查询失败不当作全 false，不依赖旧 SSE 长连接。
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
32. trigger 删除与 claim／目标绑定／交接竞争时保留源事件和未决证明；删除后不启动新工作，重启可完成取消或对账，已交接历史不丢且其他工作不受影响。
33. 自动来源固定原 actor；编辑 trigger 执行人不改变已受理事件身份，原身份权限撤销后不能借用新身份执行。
34. workspace 删除先持久关闭受理并停止所属工作，清理未知时保留句柄及对账证明并明确返回 cleanup_pending；重启继续清理，完成后按外键顺序删除。只有普通 admission 的工作区同样可删，并发新受理不能越过删除标记。
35. 用户 run 启动成功后丢失响应，原 run 结束及 Redis active slot 消失后再次提交相同来源，仍返回预先绑定的原 run，不产生第二次模型／工具执行；启动前崩溃保持可恢复。
36. 删除账号包含其在他人共享会话发起的任务／通知，不因 admission 外键失败、不先删恢复证明、不停止其他参与者独立发起的工作；共享 run 内的其他人输入按第 39 项对账。只有普通 admission 的账号可删除，清理失败和重启可恢复。
37. A 的后台结果遇到 B 的 active run 时不进入 B 的 Session；slot 释放后只以 A 的当前权限运行。A 权限撤销则不投递，同 actor 通知仍可共享 run。
38. `install ...` 的安装或合成消息提交后丢失响应，重试沿用同一 admission／结果，只出现一次安装及一组历史，不创建额外模型 run；执行前 Stop／权限撤销仍阻止副作用。
39. 共享会话中删除 A 时，B 投给 A 的未提交 steering 在旧 attempt 被隔离后以原身份只交付一次；已提交但处理中断的输入保留历史并明确中断，不自动重放副作用。原状态未知时继续对账，不丢弃 B 的输入或冒用 A 的凭据。
40. 账号／workspace 删除返回 cleanup_pending 后，前端持续显示清理进度、重试及刷新恢复；成功回执触发相应退出／移除，网络错误和 401 不伪报删除成功。
41. schedule 删除分别与 occurrence claim、busy／IM 排队、启动及回执丢失竞争，未执行来源不再启动，未决证明保留并可恢复；已执行 run 的历史和同会话其他工作不受影响。
42. worker 在删除状态两次查询间完成硬删除，或成功响应丢失后，客户端仍可用原删除凭证查询 completed；凭证不能读取其他操作或恢复业务权限，过期明确不可确认。不同 token 并发删除同一目标只产生一个清理操作，所有已受理凭证都能确认终态；多 worker 清理只回收完成满 30 天的操作及凭证，不删除 pending 或新完成的回执。
43. B 的用户 steering 到达 A 的自动 run 时不进入 A 的 Session，也不借其凭据执行；释放 slot 后只以 B 的当前权限执行一次，准备期／HITL 排队／pub-sub 不能绕过身份检查。
44. workspace 移除／离开及 org 成员撤销与活动命令竞争时立即撤销新执行权、保留清理证据并停止原工作；重启可继续清理，重新加入不复活旧工作，其他成员和其他 org 不受影响。有效成员条件覆盖所有直接读取成员行的授权入口，包括 IM 身份识别和组织／workspace 分享的读取、创建；cleanup_pending 期间成员行仍在也不授予权限，不能只修改常规鉴权依赖。worker 在两次查询间删除 membership 或完成响应丢失后，本人仍能凭独立回执确认终态；旧操作不能删除重新加入的成员实例。
45. 配置和显式超时同时覆盖默认、合法较长值、技术上限及上限加一；超大值在配置加载／参数验证时拒绝，不产生半条 reservation 或未处理的日期溢出。

46. topic 归档与新建会话／首次受理／reservation 竞争时，停止范围唯一且持久；隐藏后和重启后仍清理所属会话，不影响其他 topic。topic 参与者移除与 reservation／迟到 start 竞争时，失去全部访问权的 actor 原工作被停止并持久撤销，重新加入不复活；仍有会话参与权限的工作、其他 actor 及共享 sandbox 不被误停。清理失败／刷新／重启可查询同一回执。
47. IM connector 删除与入队／claim／交接回执丢失竞争时，不丢 receipt／queue／来源证明，不重复执行；未决清理可恢复，不提前硬删。UI 区分 pending 与完成，断网／刷新／重试不伪报成功，worker 在两次查询间硬删后仍可取得终态回执。
48. 可见页面已经完成空 summary 对账后，仍能发现后来触发的 schedule／trigger run 及其回复；隐藏恢复、查询之间已完成和请求失败退避均覆盖。
49. 其他参与者不能回答／批准原 actor 的 HITL 后借其凭据执行；原发起者失权同样拒绝。主 Stop 仍按会话控制权限清理，不走回答路径。
50. 普通用户 run 释放 slot 并正常结束后仍可 reflection／consolidation；Stop 在模型调用前或返回前发生时，不开始新调用／记忆写入。另用数据库双向 barrier 验证校验后至提交之间的竞争：Stop 先提交则拒绝写入，记忆先持锁则先提交，不允许 Stop 提交后再写；事务回滚保留原记忆及容量清理前状态。覆盖 consolidation 整批操作和个人／workspace scope、topic 归档及参与者撤销；旧批次重开、完成凭据替换或消失、会话删除均不得恢复旧写入。终态后快速下一次发送不能丢失原结束回执，HITL 暂停及到期接管不受影响。
51. 自助 sandbox restart／delete 与 reservation、provider start、迟到句柄竞争时，全部原实例任务继承停止和通知取消；provider 失败／实例未决不假报完成、不先软删丢证据。重启可恢复、旧 token 不作用于新实例；共享实例范围明确，其他实例不受影响。前端刷新后仍能区分受理与完成。
52. workspace／account 父删除与已受理的成员、IM、topic participant、sandbox 子操作双向竞争时，不丢清理证据或终态回执；父关闭受理后新子请求不伪报已受理，子先完成不重复删除。父子终态失败整体回滚，重启继续，多父重叠不产生重复清理或依赖环；所有已受理 token 仍可查询真实终态。
53. A 删除账号时，仍供 B 使用的共享 conversation／topic sandbox 原子转移归属，原实例、volume、共享文件／附件／历史及 B 的执行身份不变，A 可最终硬删且无遗留用户 FK。接任者并发撤权／删除、rekey／revive、旧缓存和 worker 重启不恢复 A 的归属；不转让双方私人凭据，A 的 egress 失效，B 的独立授权仍有效。依赖 A 个人 user-scope sandbox 的共享关系在受理前明确阻止账号删除，解决依赖后才可继续。
54. 最后一位 topic owner 离开后，即使远端清理失败或 worker 重启，初始撤权事务已选出最早的有效接任者，后者能执行 owner 操作。并发离开／上级撤权、候选失效、已有其他 owner、无剩余成员及旧成员重新加入不产生重复晋升或权限复活。主动退出可拒绝未解决的个人 sandbox 依赖；管理员 topic／workspace／org 撤权必须立即生效，停用相关个人共享路由并清理其工作，不能被共享资源或无接任者否决。其他独立工作保留，环境不可用与权限已撤销在 UI 分别说明。
55. 共享会话已有索引及 pending／running／done／dead embedding job 时，删除原创建者仍能硬删用户且 B 可搜索原历史；入队、领取、provider 等待、写入和失败回调的双向竞争不写回旧 creator、不丢新 claim 或索引。独占删除不被旧 worker 复活；索引 owner 变化不扩大其他用户的搜索权限。
56. A 签发的 Redis 分享／预览令牌在原授权撤销或账号删除受理后即不可用，公开页面和文件入口一致；共享 artifact 保留给 B、Redis key 尚存、签发与删除竞争及账号硬删均不能绕过。重新加入和无签发证明的旧令牌不恢复权限，B 独立有效的链接保持可用；IM 以原 actor 签发，不借新 connector 身份。
57. 删除账号 A 创建或接管全部 acting_user 为 A 的 connector 清理，包括子删除尚未受理的情形；入队／claim／handoff 响应丢失、worker 重启、同时 workspace 删除及并发修改 acting_user 均不丢恢复证明、不重复派发，真实硬删 A 不被 connector FK 阻塞。其他 actor 的 connector 和已交付会话历史不误删。
58. 在账号清理 pending、org／workspace／topic 撤权及管理员降级后，旧 org 邀请、ConversationShare 正文／artifact、面板新请求和 OAuth 最终写入均拒绝失效授权；已连 WebSocket、run replay／live tail 等按第 59 项的最长 5 秒租约传播上限关闭。用接受邀请、复制后激活、双向 relay、SSE 缓冲及 token exchange／最终写事务的竞争验证；其他 actor 的分享／run／合法共享凭据保留，原授权失效后重新加入不复活旧授权。空闲连接、漏通知、到期及数据库不可用也可有界关闭。
59. 万帧终端／浏览器与密集 SSE 输出不产生逐帧数据库查询，两向连接共用有界续租；通知丢失时撤权传播不超过 5 秒，过期／验证故障关闭，慢查询不把租期顺延到返回后，业务写入不享受缓存宽限。
60. 删除前开始的 Google／SSO 登录在 provider 等待后、账号硬删后或清理恢复时返回，不能重建用户、身份链接或 bootstrap 数据；邮箱自动绑定和已有 external identity 两路都覆盖。短期隔离到期后合法新流程可重新注册，原 state 最终期限仍拒绝迟到旧流程，隔离记录按期回收。
61. DELETE 返回 pending 后立即关闭客户端／停止 request worker，独立删除恢复 worker 仍能在启动扫描或定期扫描中完成对账和硬删除；双 worker、claim 到期、崩溃重启、父子操作及长期 unknown 不丢证据、不重复终态，不靠状态 GET 的隐式写入推进。

公共契约用 command 首个实现验证登记、停止、恢复、事件、Todo 与 UI；能力限制和状态映射保护语义，不靠伪造未来 MCP／subagent 适配器宣称集成已完成。涉及真实 Postgres／Redis／FastAPI 的用例放 e2e，只在最外层执行方注入故障，公共 service／repository／投递层用真实实现。前端业务流覆盖“用户 steering 与后台结果同时到达 → 重试／刷新仍分开 → 主 Stop → 迟到完成不再续跑”，以及单任务停止、失败反馈、事件展开不触发取消；不以静态元素计数代替契约验证。长等待用可控时钟推进与重启验证，不真等数小时；`real_llm` nightly 另查模型是否仍主动用 ps/sleep 忙等。设计文档通过检查不等于这些运行时验收已通过，实施需记录实际验证证据。
