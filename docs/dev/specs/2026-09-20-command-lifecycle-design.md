# 后台任务生命周期：公共管理，command 作为首个实现

- 状态：按用户确认收缩范围，并修订停止交互与 monitor：聊天 Stop 只停止当前执行，全部停止为独立操作；monitor 一次等待只产生一个最终结果。本次只改 spec／plan，不改 #634–#636 代码或声称新契约已实现。原实施授权保留，部署和线上数据操作仍需独立授权。
- 日期：2026-09-20；更新：2026-09-21（America/Phoenix）。保留原文件路径供已有链接引用。
- 核对基线：CubePlex `f622d97e`，CubeLoop `65ff7096ca2e78e707bdd1d96f8dbca38ef5a0cc`。
- 关联：[原 managed commands 设计](2026-09-18-sandbox-background-execute-design.md)、[lifecycle 实施计划](../plans/2026-09-20-command-lifecycle.md)。
- 本版取代本草案之前的 CubeLoop live-run 等待建议，该提案不再是本方案的依赖；配套 plan 已按本版重排并获用户确认。

## 阅读导航

先看第 4 节的用户流程，再看 [5.2 停止操作](#stop-actions)：这里集中解释用户会看到什么、点击后会发生什么。

第 6–10 节是组件、投递、存储和 API 的实现设计；第 11 节说明切换与排除范围，第 12 节列出验收项。原 5.2 中的请求幂等、调度恢复、删除与权限细节统一放到[附录 A](#stop-implementation)，按问题查阅，不必为了理解停止按钮一次读完。

## 1. 核心决定

**后台任务属于 conversation，run 只是某一轮模型执行。等待几小时不需要让发起它的 run 一直活着。**

后台任务没有结果时，模型做完当前能做的事，说明进度，然后正常结束 run。CubePlex 的后台任务管理层继续观察和控制具体执行。任务有结果且未被用户停止时，向所属 conversation 投递后台事件：有可接收输入的 run 就通过内部 steering 注入；没有活跃 run 就发起新一轮 automated run。

**复用输入机制，不复用用户发言的身份与界面。** 后台事件必须有明确来源和稳定事件 ID，不进入输入框上方的用户 steering 列表。聊天“停止”只停止当前回复／前台执行；已经交给后台的任务不因这次操作被取消。任务卡片“停止任务”控制单项，后台任务区域“全部停止”才停止本会话当前所有执行及其自动后续处理。停止不隐藏真实状态或历史，只有执行方确认后才显示底层工作已停止。

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

### 4.2 monitor 是一次性条件等待，不是日志订阅

monitor 的产品契约是：后台持续检查 → 条件满足 → 保存一个最终结果并结束监听 → 将该结果可靠投递一次。等待期间不调用模型，不按输出行反复唤醒。

- 本轮继续使用 command 脚本表达等待条件，不新增条件 DSL、匹配器或持续订阅模式。脚本在条件未满足时保持运行并自行检查，满足时输出结果摘要、以 exit/0 结束；失败以非零退出结束。stdout／stderr 是日志，不是独立触发条件；只打印一行而继续运行不算条件满足。
- 成功、失败、超时共用一个最终结果位置及稳定 notice ID。非零退出报告失败；deadline 到期先持久记录 timeout 和停止意图，再清理监听进程。结果裁决与自然退出／取消在同一 task 上串行：已保存的结果不被迟到回执改写，原始 exit code 仍如实保留。暂时 poll 失败只表示观察未知，不直接报告监听失败。
- 结果及待发事件持久保存后，不再检查条件或重新执行监听脚本；尚未确认退出的进程只继续清理。投递失败重试同一事件，不重启监听，不产生额外 line／exit 通知。依赖输出的结果仍须满足第 9.3 节的可读性门槛。用户主动停止任务或全部停止取消未送达的自动处理，但保留已取得的结果与真实状态。
- 通知已提交后，即使处理它的回复被聊天 Stop 中断，也不重新投递该结果。停止发生在首条输入提交前的规则见 [A.1](#stop-current-run) 和第 7.3 节；不为一次性 monitor 增加“暂停未来通知”的控制。
- `persistent` 仅控制等待期限：即使没有 deadline，条件一旦满足仍结束，只通知一次。结束或取消监听只控制监听脚本，不默认停止被观察的构建、服务或其他独立任务；工具说明要求等待脚本不承担被观察工作的启动或销毁。
- 安装、构建自身的结束由 execute completion 通知，不额外用 monitor 轮询同一个受管进程。`notify_on_complete=false` 的 server 继续后台运行，退出只更新状态，不自动调用模型。
- 活跃 monitor 在后台任务区域占一条记录，结束后有至多一个最终结果事件；轮询和日志输出不生成额外卡片或待发送消息。旧多次通知记录的切换见第 11 节。

前台 stream 不依赖上述通知策略：execute 的输出仍走输出回调 → `on_update` → SSE `tool_result` → 工具卡片更新；后台日志仍由执行适配器采集、保存并供详情查看。删除按行 wake 不删除输出采集、日志 cursor／确认或前台增量显示。

## 5. 后台任务归属、停止和时间

### 5.1 生命周期与通知分离

所有接入的后台任务都归 conversation 管理，本轮为 managed execute 和 monitor。`originating_run_id`、`tool_call_id`、`agent_id` 保留发起来源和 UI 关联，不再决定正常 run 结束时是否结束任务。公共层保存结果通知策略；execute 的 `notify_on_complete` 映射为完成通知开关，不决定执行归属。

持久 task reservation 是系统受理执行的边界，包括 starting 状态；`backgrounded_at` 是独立后台归属的边界。run 正常完成或非用户原因失败，不自动撤销已受理工作。聊天 Stop 只停止目标 run 及尚未完成后台交接的前台任务；已交接任务保留执行和通知权限，单任务停止或全部停止才撤销它们。未受理的新工具调用仍受原 attempt ownership 和取消约束；本版不承诺 Stop 与远端启动存在跨系统原子事务。

<a id="stop-actions"></a>

### 5.2 聊天停止、停止任务与全部停止分开

一句话：**输入框停止当前执行，任务卡片停止一项后台工作，「全部停止」才停止这个对话里当前的全部工作。**

| 入口 | 会停止什么 | 不会影响什么 |
| --- | --- | --- |
| 输入框「停止」 | 当前回复，以及还没交给后台的工具、同步子代理和等待确认 | 已交给后台的任务、其他独立排队的工作 |
| 任务卡片「停止任务」 | 这项任务及其受管后代，取消它尚未送达的自动通知 | 其他任务；已经开始处理结果的共享回复不因此整体取消 |
| 后台任务区域「全部停止」 | 本对话当前执行、后台任务、待确认请求和当前待处理输入；旧结果不能再自动续跑 | 其他对话、整个 sandbox、之后的新请求或下一次定时触发 |

#### 用三个场景理解

- **构建在后台，agent 正在解释代码**：输入框点停止，只停解释；构建继续，完成后仍可通知。
- **命令还在当前回复里执行**：尚未交给后台时，输入框停止也会停止它；已经交给后台后，改用任务卡片停止。
- **monitor 已通知，agent 正在处理结果**：停止这条回复后，不会再次投递同一个结果；其他独立 monitor 不受影响。

#### 停止之后会看到什么

- 先显示“正在停止”；执行方确认后才显示“已停止”。未确认或不支持远端取消时明确提示，不能假报成功。
- 停止当前执行后，如果后台任务仍在运行，就明确显示这一事实，不一直把当前回复显示成“正在停止”。
- 发给当前执行、但它尚未接收的追加消息会撤回，可以恢复编辑；已接收的内容保留历史。
- 已有结果、日志和历史保留，停止不回滚已发生的操作。折叠结果行只改变展示。

只有后台任务、没有当前执行或待确认请求时，输入框不显示停止；从任务区域控制。新的合法后台结果或定时触发仍可能开启新回复，但必须标明来源，不能冒充被停止的回复恢复。

以上是产品行为。请求重试、后台交接、通知竞争等实现约束移至[附录 A](#stop-implementation)；删除／撤权的区别见 [A.6](#delete-and-revoke)。

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
| Conversation 控制服务／RunManager | 分别持久受理指定 run 的停止与整批全部停止；协调相应 run／HITL／输入取消及后台清理，不把两种权限混为一体 |
| CubeLoop Session | 一轮正常模型执行、已有输入协议和 HITL；不为外部任务保持运行，不保存平台任务表或决定产品停止范围 |
| UI | 分别展示用户 steering、后台任务和结果事件，从同一持久事实恢复，不展示内部投递尝试列表 |

前台 owner 交接后不再观察同一执行；middleware 本地 map 只可做关联缓存，不是 cleanup、deadline 或存活的依据。移除 `on_run_end` 中的等待及重复 provider polling。普通 finalize 只结束本轮资源并交出 task owner，不能把后台任务当作 orphan 终止；两种停止的持久清理不能依赖 finalize 是否成功执行。

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
| command（execute／monitor） | sandbox 控制、进程事实、日志、资源 cap／保活；monitor 一次性等待结果及监听清理 | 首个完整实现，共用公共任务与事件协议 |
| MCP 外部长任务 | connector／授权、远端 job ID、状态／结果查询和取消协议 | 仅确定接入条件；普通 `tools/call` 保持现状，不自动 detach |
| detached subagent | 独立执行上下文、checkpoint、模型预算、工具权限、审批及子任务控制 | 仅确定归属与控制边界；不改当前同步 subagent |

未来 MCP 接入需确认目标服务确实提供可观察的异步任务或专用 job 接口，并按实际协议版本适配。没有远端任务句柄和恢复方式的普通 RPC，不因超时或调用耗时就宣称可持久接管。本轮不预先实现 MCP Tasks 客户端、不承诺任意 MCP 工具可后台化。

未来 subagent 的 detach 是脱离父 run 的等待，不是脱离会话控制。持久交接后不随父 run 的聊天 Stop 结束，仍继承 conversation 执行批次和可选父任务约束；全部停止或停止父 task 禁止后代继续创建工作。它使用独立执行上下文与 checkpoint，不争用父会话前台 active-run slot；仍受并发、预算与真实子执行审批约束。最终结果经公共事件返回，不逐条唤醒主模型。本轮不实现这些机制，也不改变同步子代理随当前 run 取消的行为。

## 7. 会话通知协议

### 7.1 一个持久出口

把现有 `sandbox_command_wakes` 通用化为按 task ID 关联的 `background_task_events` outbox，迁移既有记录和去重标识，沿用投递、claim 与 checkpoint 对账机制，不另开消息管线。本轮 execute completion 和 monitor 唯一最终结果都走这里；不再保留 monitor 按输出行或额外退出唤醒，也不同时保留 on_run_end 注入与 orphan scanner 双重通知。

- execute completion 用 task ID + completion 类型去重；新 monitor 用 task ID + monitor_result 去重，成功／失败／超时不分别建事件。重试始终使用同一 notice ID；历史多次事件保留原标识供对账，但不因此恢复持续通知模式。
- 保存公共终态、具体结果引用与创建待发事件在同一数据库事务内完成；已取消通知的 task 不再生成待发事件，但仍保存迟到结果供查询。
- `notice_state` 若保留，只是完成通知的摘要，不能成为另一个独立消费者。wake 增加 discarded，不能把权限撤销或用户取消记成 delivered。
- 通知包含 `source=background_task`、`notice_id, task_id, task_kind, originating_run_id, execution_generation, reason` 及必要结果摘要／结果引用；可选父任务关联来自 task。command ID、execute／monitor 子类型和日志位置属于命令详情，不是所有事件的必填字段。发起 run 只是来源；目的地是 conversation。原发起用户是权限校验主体，不是这条事件的发言者。
- 进度和日志只更新任务快照／日志，不产生模型输入。monitor 的最终 outcome 为 matched／failed／timed_out，保存在命令详情并随唯一 monitor_result 事件提供；取消未提交通知则标 discarded，真实执行／结果事实不删除。

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

同一次调度中，同一 conversation、有效批次及 actor 的不同任务最终结果可按稳定顺序投向同一 run，减少重复建 run；不把多个 notice 合成一条物理输入。每个 notice 有独立 InputEnvelope／投递 ID 和 checkpoint 确认，空闲时一个作首条，其余在输入入口开放后分别投递。不保证只有一次模型调用，不为凑批长等；同一个新 monitor 只能提供一个最终 notice，不存在输出行的合并／限流投递。

因此 A、B 同时排队时，单独停止 A 只撤回 A 的输入，B 保持可投递。撤回返回 `closed`、响应丢失或提交状态未知，都不能当作“肯定未送达”；先按第 7.3 节对账，已提交的历史保留，未确认失去提交资格前不重投。不增加 Session 内修改已排队输入内容或拆批的协议，UI 的聚合展示也不改变输入边界。

新 run 的首条 notice 单独处理：它通过 PromptExecutionRequest 进入，不在 `cancel_input` 的可撤回队列中。宿主持久绑定首条 notice 与投递 attempt，准备阶段和调用 Session 前都检查源 task 的通知权限，不能只检查 conversation generation。初始输入的持久提交尚未确认时，B 等其他 notice 与用户追加输入保留在各自持久队列中，不向这个 Session 提交。确认初始输入已提交后，才允许其他输入进入；开放这一入口与首条 notice 的取消裁决必须串行，不能让取消 handler 读到旧状态后误停一个已经接收 B 的 attempt。

停止首条 A 的 task 时，若仍在准备／初始提交未决阶段，先关闭该 attempt 的其他输入入口，再取消对应 attempt，并按 checkpoint 对账，不把 `closed` 当作取消成功。A 已提交保留 delivered 历史，未提交且 attempt 已失权则 discarded；B 等此时尚未进入 Session 的输入仍可重新路由。若初始提交已确认并已开放其他输入，单 task Stop 不再取消整个共享 attempt，只撤销 A 后续执行／未提交通知；尤其不能让已提交 B 因停掉共享 attempt 而失去处理机会。全部停止仍可取消整个批次。这里不新增 delivered 通知的自动业务重试，也不新增 CubeLoop Session 输入 API。

### 7.3 排队成功不等于结果已送达

内部输入只有收到 `InputCommitted(durability="checkpoint")` 或恢复时查到相同 notice ID，才能标记 delivered。启动新 run 返回成功也不算送达，仍需检查其初始输入已经持久保存。delivered 表示输入已入历史，不承诺模型已成功处理业务结果；run 失败不能靠重复同一通知盲目重做任务。

如果原 run 在投递途中结束，先对账 checkpoint：已提交就确认；未提交且原投递已确定不能再提交，才取消旧 steering、重新路由。旧 attempt 的 fencing 必须生效；不能仅因一次“没查到”就向另一个 run 重发。worker 崩溃、claim 过期、start_run 响应丢失都沿用同一 notice ID 对账。

聊天 Stop 与 notice claim 的竞争按 [A.1](#stop-current-run) 只取消目标 run 的处理：初始 notice 不因这次中断重投，独立未提交 notice 经 checkpoint 对账后可继续；不改源任务的整体通知权限。单任务停止／全部停止／删除才按 task／父任务取消标记或关闭的 generation 阻断源事件，生产、投递、新 run admission 均检查。deadline 到期不吞超时报告。提交在途以实际 checkpoint 为准，已提交保留 delivered 历史，不能重写为 discarded；失权来源不得换批次重新输入。不声称 Postgres、Redis、Session 之间存在跨系统事务。

### 7.4 输入机制与用户消息分离

- 可以复用 durable steering 的存储／投递能力和 `ExecutionSession.submit_input`，不另建通用消息队列；内部记录必须保存来源、notice ID 和批次，不能只靠内容或 `scmw-` 前缀推断。
- checkpoint、输入确认、实时事件和历史投影保留同一来源。面向模型可使用 provider 支持的消息 role，并显式说明这是后台执行结果；面向用户则按后台事件呈现，不冒用用户头像、姓名或气泡。后台输出是任务数据，不是用户新指令或审批答案。
- 不修改稳定 system prompt，不回写旧 tool result，不伪造旧 tool call 的第二个结果。来源元数据随新输入持久保存，刷新和重放不能把它还原成用户发言。
- `pending_steers` 与用户 steering 实时事件只包含 `source=user` 的主动输入。`/steer`、`/steer/cancel` 只处理用户消息；source 由服务端入口确定，客户端不能通过自填 source 获得内部通知身份，保留内部 ID 的防伪校验。
- 后台结果的产品状态来自源 wake 及 checkpoint 对账；一次投递尝试失败不是一条新的产品消息。同一 notice 换 run 重试、重新 claim 或 bootstrap 后仍是同一条事件，旧 failed／queued steering 不得重新进入用户列表。
- 来源分类也约束个人记忆提炼：首条输入或本轮已提交追加输入含 background_task notice 时，不触发本轮自动个人记忆 reflection，避免将日志／自动结果当成用户偏好或纠正。普通纯用户输入的 reflection 保持原行为；不回写历史或修改 prompt 缓存前缀。本轮采用保守跳过混合轮次，不新增一套自动结果记忆系统。
- 普通用户轮次的 reflection 是正常结束后的可选后处理，不为它保留 live run 或 active slot。它要求原 attempt 正常完成及持久清理证明，并在模型／工具边界检查原 actor、generation、run_stop_requested_at 和完成凭据；目标 run 的聊天 Stop、全部停止、删除或失权都禁止继续，停止另一个 run 不影响它。取消、失败、HITL 暂停或未完成清理不能授权后处理；正常结束本身不误禁用自动记忆。已保存记忆不回滚，失败提炼不自动重放。
- reflection 的模型／工具检查不能代替写入事务检查。每次 memory_save／memory_update 将原受理身份带入数据库事务，先锁定并复查当前权限、原 generation 与目标 run 停止标记，再读取／修改记忆，直到整次操作提交才释放锁；去重更新时间、容量清理和最终写入不能中途自行提交。目标 run 的聊天 Stop 或全部停止先提交则写入被拒绝；记忆事务先取得权限锁则先完成，Stop 随后提交，之后不再有旧权限下的记忆写入。事务失败整体回滚，不保留部分容量清理结果；不在持锁期间等待模型。
- 自动 memory consolidation 同样排除后台来源：读取持久历史时，先按输入来源与 run 关联识别并排除含后台 notice 的整个轮次，再裁剪窗口及转成 role／text。仅跳过后台 run 的即时调度不够，后续普通用户 run 再次合并历史也必须使用相同过滤；旧通知按可靠 wake 关联分类，不靠正文猜测。窗口边界和缺失关联不能把孤立的后台回复误算为纯用户轮次，无法可靠分类的片段不用于自动记忆。无合格历史时不调用提炼模型、不写个人或 workspace memory，但按现有 cutoff／consumed 规则完成本次扫描，保留扫描期间新增 run 的计数，避免反复扫描同一批排除内容。正常纯用户历史的合并保持原行为，不回写 checkpoint。
- 自动 memory consolidation 还必须绑定触发它的原 admission／attempt／generation，不因来源是纯用户就跳过权限校验。正常结束回执完成后才开始，模型调用前及应用结果的事务中复查原身份；整批 extract／merge／archive、去重和容量清理在同一受保护事务内提交，不允许逐项 commit 或吞掉写失败后提交部分结果。目标 run 的聊天 Stop、全部停止、删除或权限撤销在模型等待期间生效时，整批结果不得写入个人或 workspace memory；失败不自动重放旧执行，后续合法 run 仍按原 cutoff／consumed 规则决定新的扫描。
- 两条自动记忆路径的权限锁均覆盖 topic 的归档状态及授予访问权的 topic／conversation participant 行；不能只锁 workspace membership。检查期间会话移动到另一 topic，或只能依靠尚未锁定的新参与者记录时，重新受理而非沿用旧检查。topic 归档／移除参与者与写入事务必须串行。
- 结束状态对外可见时，原 worker 仍需持有有界清理租约，直到结束回执落库和 slot 释放；不能让快速下一次发送抢走清理权并导致正常后处理失去结束证明。HITL 暂停必须及时解除清理租约以便回答，worker 崩溃后租约到期仍可恢复。
- UI 的折叠不改变投递。需要阻止后续处理时，停止源 task 或使用全部停止，由服务端取消源事件和未提交输入；停止当前回复则使用聊天 Stop，不撤销其他独立结果。不提供“把内部通知恢复到输入框”或仅取消某次内部 steering 的入口。

## 8. Todo 如何允许本轮结束

只改通知路由还不够：现有 Todo 会因 unfinished + 纯文本要求模型继续。因此需要一个明确的“剩余工作等待后台结果”的收尾例外，而不是移除全部 Todo guard。

建议扩展现有 `write_todos`，增加可选 `wait_for_tasks: list[str]`，默认空列表，引用公共 task ID。不新增 wait 工具，也不增加 Todo 的 completed 变体：

1. 模型显式声明：当前没有可独立执行的剩余步骤，它们在等这些后台任务的结果。保留原 Todo 列表和 pending／in_progress 状态。
2. CubePlex 经公共 task 查询校验 ID 属于当前可访问 conversation、当前批次仍有效、确实由后台管理、尚有待交付的结果、会产生自动通知且自身／父任务未被用户撤销继续处理和通知权限。仅 deadline 到期、仍有待交付超时结果时不据此拒绝等待。notify=false 的 server、不可恢复且已失去执行方的任务不能成为自动续办的等待凭据。
3. 等待声明和对应 Todo 快照随 Session extra 一起 checkpoint。普通 Todo 更新未携带等待声明时清空它，不把旧声明套在新计划上。
4. 自然收尾时重新校验；对有效等待声明跳过“未完成所以必须再调用模型”的 finalization guard，保留 payload 校验、错误处理和显式 stop。任务已终态但结果尚未送达，也允许结束本轮，由通知接续。
5. 用户新输入或相关结果输入提交后，旧收尾许可失效，模型先处理新输入、更新计划；若仍需等待，再显式声明。不能因为会话里任意一个 monitor 还活着，就放行所有未完成任务。
6. 声明创建时就做宿主校验并持久保存成功校验的绑定；若自然收尾前其中依赖被用户单独停止，宿主可返回 cancelled 收尾结果，而不是强制模型忙等。仅适用于同一 Todo／输入边界上先前有效的声明，且每个依赖仍有效或有后来发生的用户取消事实；任意无效 ID、权限不明、查询故障不获得此许可。保留未完成 Todo，记录“等待已由用户取消”的收尾原因后正常结束当前 run，不发新的结果通知、不唤醒空闲 run，也不终止已接收其他输入的共享 attempt。新输入仍使该绑定失效，聊天 Stop／全部停止按目标取消机制优先处理；不能把已取消任务作为新的等待依据。

无 Todo 的简单命令不需要补一次 write_todos；正常给出进度回复并结束即可。后续 run 通过现有 `load_checkpoint` 同时恢复消息和 extra，不自行拼接私有 Agent 状态。业务步骤完成仍由模型验证，不因命令 exit/0 自动把整个任务勾完。

这可能需要 CubeLoop Todo 的小范围公共扩展：等待元数据和可选收尾校验策略，默认行为不变；task 查询和类型能力判断留在 CubePlex，CubeLoop 不依赖 command 或 MCP 模型。它不是修改 Session／agent loop 生命周期，也不依赖旧 IdlePolicy 提案。工具 schema／说明统一发布，动态 task ID 只进工具结果和输入，不随运行状态改系统 prompt 或工具集合。本轮只有 command task 作为实际等待来源，不为未来类型增加伪适配器。

## 9. 持久事实与 command 首个实现

### 9.1 公共生命周期只有一个写入来源

采用公共 task 主记录和类型详情，不能让通用表与 command 表各保存一份可独立更新的运行／停止／投递状态：

| 持久记录 | 权威内容 |
| --- | --- |
| `background_tasks` | 第 6.2 节的公共身份、状态、owner、控制与结果引用；统一 `deadline_at`、`stop_requested_at/stop_reason`、`notifications_cancelled_at`、`last_observed_at`、`revision`、`backgrounded_at` |
| `sandbox_commands` | 与 task 一对一、`task_id` 唯一的命令详情；保留 command ID、稳定 `user_sandbox_id`，另存不可变 `sandbox_instance_id`，以及命令参数、provider_ref、原始进程观察／exit code、日志路径／cursor／log_state、monitor 的一次性 outcome／结果引用 |
| `background_task_events` | 从原 wake 表迁移的单一通知 outbox；关联 task ID，保存 notice ID、事件事实、去重键与投递／claim 状态 |
| `conversation_execution_admissions` | 首次受理的来源身份、目标 conversation、generation、run 关联、不可变请求摘要／有效执行设置快照，以及启动 run 的 run_stop_requested_at；同一 scope 内 `(source_kind, source_id)` 唯一。只用于停止边界与重试幂等，不保存另一份任务运行状态或调度计划 |

公共状态由 task service 根据适配器证据统一更新，命令原始 exit code 是证据，不是另一套业务状态机。类型详情和任务记录的关联必须满足同一 org／workspace／conversation，读取和控制均不能绕过 scope。具体结果和公共终态／事件需要一起提交时使用同一数据库事务；不依赖双写后异步补齐两套事实。

conversation 保存用于全部停止的执行批次与关闭标记；run 的单独停止另存于原 run 的启动 admission 控制记录，不能共用会撤销后台权限的字段。run／task／事件／输入关联原 generation，输入另存 source／notice ID。受理先于持久排队或启动，旧 occurrence 重试读取原记录。`backgrounded_at` 在后台交接事务中写入，显式 background 同样遵循该边界；不能从 notify、本地 map 或浏览器收包推断。所有时间 tz-aware；新增业务 ID 在 public_id 注册。本次不新增业务任务分组、run command scope 表或未来适配器详情表。

每次 owner claim 使用唯一 token；状态、cursor 和 lease 写入都校验 token。失去 owner 后不得继续提交观察结果。数据库锁不能撤销已发出的远端操作，新 owner 必须重新观察，不直接重启进程。

### 9.2 Command reservation、进程观察与停止

保留既有 sandbox scope 和 8-command cap，不把它扩成所有后台任务的并发上限。按权限行 → conversation → admission → sandbox → task 的锁序检查原执行资格和 cap，并在同一事务创建 task／command reservation。reservation 与聊天 Stop 的 run 停止、全部停止的批次关闭及 handoff 均串行；不能先产生独立执行再补公共记录。provider I/O 在锁外，迟到句柄只登记并按已提交控制意图清理。

先登记再启动。无论取消或连接中断，已知 provider_ref 都必须持久保存。回调迟到时合并 provider_ref，但不能清除已有停止／通知取消标记。启动结果未知的 reservation 继续占 cap 并显示状态未确认，不能删行释放名额后重复启动。新批次开始后旧进程仍未确认退出时，仍计入资源占用并展示清理进度。

`user_sandbox_id` 是可以原地重建的稳定行，不是执行环境身份。外部启动前，将实际 attachment 的 provider sandbox ID 持久写入 `sandbox_instance_id`，并与之后返回的 provider_ref 绑定；该绑定终生不改指向新容器。reconnect、poll、kill、日志收集均定位并校验这一个实例，不能用当前 UserSandbox 行上的新 ID 替换它，也不能为了观察旧任务调用会创建新容器的恢复路径。替换后的旧实例有可靠销毁证据才收敛为环境失效并释放 cap；只有不匹配、无可靠退出证据时保留 unknown，若仍能安全访问旧实例则只对原实例执行清理。实例确认和启动之间仍可能遇到销毁，按失败／未知启动处理，不重新执行命令。

命令 tool、HTTP、deadline 共用公共停止入口，再由 command 适配器执行 poll → 仍运行则请求 kill → 再观察；reason 决定是否取消后续通知。kill 返回或抛错都不是终态证据。已确认退出则同时保存真实进程结果并收敛公共 task 状态；仍不确定则保留停止中并重试。

同 scope 已终态 command 的重复 kill 返回原事实，不报虚假 not found；跨 org/workspace/conversation 或不属于当前 sandbox 的停止请求仍不泄露。provider 的 not-running 字符串不能代替观察，也不能把已知 exited/236 改为 guessed killed/None。

现有 sandbox restart／delete 仅补受管任务清理：先在原 sandbox 行持久关闭新 reservation／revive／保活，再按 conversation → sandbox → task 锁序给该实例任务登记停止和通知取消；不持 sandbox 锁反向锁 conversation。已在途的 start 回执仍保存到原实例并继承停止。保持既有权限、共享实例影响范围和 PVC 策略，不顺带停止其他实例。

只有可靠的退出／原实例销毁证明才能报告清理完成；provider 失败或实例身份未决时不假报成功、不提前隐藏恢复记录，也不创建替代容器来核对旧任务。保留原实例上的清理标记和任务事实，coordinator 重启后继续清理；管理请求通过现有入口重试，不能把旧实例的任务句柄用于新实例。前端沿用现有错误／状态展示区分未确认与完成；不新增 teardown token、独立状态 API 或父子删除操作系统。

### 9.3 命令日志确认独立于执行状态

command log writer 区分写入成功和删除临时分片成功。它不是所有 task 必须实现的日志服务。专用目录在运行用户权限下可写；限定路径、检查非目录／symlink，不递归 chown 工作区，也不以 root 跟随代理可控制的路径。

poll 提供候选 cursor，确认日志数据写入后才持久 ack。cleanup-only 失败不重放已确认输出；write 失败保留旧 cursor，记录 retrying。进程可先进入终态，但依赖尾部输出的 completion／monitor 最终结果事件保持 pending，直到最终日志可读，或有可靠证据证明不可恢复并明确标为 unavailable；临时失败不能直接当作不可恢复。结果就绪由适配器报告给公共投递层，投递重试先检查它，不能用截断结果触发一次无人续办的模型处理。coordinator 在无活跃 run 时继续收集，UI 仍可立即显示真实退出状态及日志恢复中，Todo 可等待尚未交付的最终结果。

不承诺文件与数据库之间 exactly-once：写入成功但 cursor 未提交可能导致重复片段，不能为去重而静默跳过未知数据。日志重试不重发完成通知。现场 orphan 的删除是另需批准的运维动作。

## 10. API 与界面

公共控制与快照使用 `/api/v1/ws/{workspace_id}/conversations/{conversation_id}/background-tasks`，不要求 UI 为不同类型复制列表、Stop 或恢复逻辑。复用现有后台任务区域与 Terminal／command 详情，不新增独立任务中心。

### 10.1 控制与快照

- `GET .../background-tasks` 默认查询 inflight，支持有界 task IDs 查询；`GET .../background-tasks/{task_id}` 返回单条只读快照。查询不顺便 poll 执行方、抢 owner 或消费日志；具体详情按 task kind 返回，不强制存在日志／exit code。
- 同一 conversation 下新增只读 `GET .../background-task-events?delivery=pending|all&cursor=...&limit=...`，返回 `items, next_cursor, has_more`；默认 pending 与 summary.has_pending 仅计算 state ∈ {pending, claimed}，包括这些状态下待重试／对账的源事件。delivered 和 discarded 明确排除，只进入 delivery=all／历史。按不可变 `(created_at, notice_id)` 稳定分页，limit 有上限。每项带 task ID，可用任务详情接口查找并停止已经终态但通知未送达的任务；不依赖历史消息或默认 inflight 列表发现它。跨 scope 的 cursor／ID 不泄露记录，查询不消费事件。
- 现有 `POST .../conversations/{conversation_id}/cancel` 只执行聊天 Stop，请求必须带目标 `run_id`；202 返回 `{run_id, accepted, cleanup_pending}`，已终态时保留原结果并停止尚未完成的关联清理／后处理，全部完成的重复调用返回原事实，不重新选择最新 run。按原 scope／会话控制权限检查，paused HITL 不依赖 Redis active key；仅有后台任务不构成这个接口的目标。
- 新增 `POST .../conversations/{conversation_id}/stop-all`，请求带 `execution_generation`，执行全部停止；202 返回 `{execution_generation, accepted, cleanup_pending}`，无 active run 仍可停止后台任务和待发结果。重复请求只针对原批次；与聊天 Stop 分开 schema／handler，复用底层控制服务。
- `POST .../background-tasks/{task_id}/stop`：持久受理但底层尚未确认结束返回 202；已确认终态返回 200 + 原事实。响应分别表达本地停止已受理、远端取消能力和确认状态，不把 HTTP 成功当作远端取消成功。无法持久受理返回真实错误。终态任务的待发事件也按用户停止规则取消。
- 当前 `sandbox-commands` 列表／快照／kill 调用方在实施时一并切换到公共接口，不保留只做转发的旧控制 API。命令专属工具仍可以接收 command ID，由其一对一关联找到 task 后调用同一 service；不新增一套泛化工具替代全部领域工具。
- 快照提供 task ID、类型、来源／父任务、执行状态、结果引用、deadline、停止意图、通知状态、能力与 revision；command 详情额外提供 exit code 和日志状态。所有类型都不向客户端暴露内部执行句柄、凭据、owner token 或 cursor。
- 历史读取可展示该会话旧 sandbox 的记录；单命令远端停止仍限可授权的原 sandbox。全部停止取消本会话目标批次的未提交通知，但不向其他 sandbox 发未授权停止请求；原环境未知时保留未知，不报已停止。聊天 Stop 不取消先前独立后台任务的历史结果或待发通知；不可见 ID 不泄露存在性。
- bootstrap 返回当前 `execution_generation`、全部停止进度，以及现有 active／paused run 的 `run_id` 和该 run 停止进度、用户 `pending_steers`；两种停止状态分别表达。另返 `background_summary {has_inflight, has_pending, has_cleanup, can_stop}`，从完整可访问 conversation 的持久记录计算，不受分页截断影响。`has_pending` 只计 pending／claimed；`has_cleanup` 含旧停止清理和日志收尾；`can_stop` 只供后台任务区域判断可撤销的后台执行／通知，不能据此显示输入框 Stop。全部停止入口另外结合当前 run／HITL，输入框 Stop 只看目标 run 控制状态；不把后台摘要当 run 的第二个权威。事件投影与分页接口相同，含 notice ID、task ID／kind、reason、结果摘要／引用、投递状态、revision、next_cursor／has_more；命令日志位置只是类型详情。

### 10.2 三类信息各有位置

| 信息 | 显示与操作 |
| --- | --- |
| 用户运行中追加的话 | 输入框上方的 steering 列表；可撤回，失败后可恢复编辑；不混入后台事件 |
| 活跃后台任务 | 现有后台任务区域；每个 task 一条状态，按能力提供详情和停止。command 可进入 Terminal 看日志；monitor 输出不新增任务卡 |
| 最终结果事件 | 对话中的紧凑事件行，标注后台来源和结果，默认折叠、点击展开结果或日志；不是用户气泡，不带撤回或恢复草稿按钮 |

事件创建后可以显示“待处理”，checkpoint 确认后显示“已送达”，取消则显示“已取消”；这些是该事件的状态，不是用户待发送消息。已送达不等于模型已成功处理。按 notice ID 合并源事件与输入历史投影，不能既显示事件行又显示同内容的 user bubble。日志按原始文本渲染，不把输出里的 Markdown／HTML 当作可信交互内容。

新 monitor 等待期间只更新任务状态和日志，结束后只有一条最终结果事件；执行输出不转换为通知列表。通知重试只更新同一行的状态，不新增行或抢焦点。暂时失败显示重试中，不可恢复错误就地显示并提供有效控制；用户不需要删除一排内部消息。停止当前回复不清空任务区域，其他后台任务仍可运行并报告结果。

输入框停止图标明确标为“停止当前执行”，只在有 preparing／running run 或持久 HITL 时可用；后台任务区域单独提供“全部停止”，说明会影响本会话当前执行和所有后台任务。只剩后台工作时输入框不假装模型仍在思考，任务区域仍可控制。各操作受理前显示“正在提交停止”，受理后只对其目标显示停止进度；聊天 Stop 完成而后台仍在时提示“当前执行已停止，后台任务仍在运行”。未知／取消不支持就地展示，不能先移除记录再悄悄恢复。前端不能把 HTTP 成功当远端已退出，全部停止的确认也不得由某一页空列表推断。

### 10.3 恢复与后续回复

- run 正常结束后聊天不再显示模型仍在思考，task 卡片继续显示真实后台状态；未完成 Todo 不显示成功。聊天 Stop 取消当前执行及其等待声明，不取消所等待的独立 task；全部停止才同时取消本批次后台工作。后续输入仍使旧等待声明失效。
- 删除上一版新增 run waiting 事件的要求；任务状态以数据库快照更新，旧 revision 不覆盖新事实，不重写历史 tool result。
- 原 run 的 SSE 结束后仍发现后续 automated run。页面可见且 background_summary 任一 has_* 为真时，做有界低频快照和 bootstrap 刷新；发现新 run 接入现有 SSE，两次查询之间已完成则从历史显示。聊天 Stop 的可用性只依据 run／HITL；后台 can_stop 用于任务区域。保留前台 execute 的增量 tool_result，monitor 不参与这条输出链路。
- 不在 task 刚变终态或某一页事件为空时立即停掉后台工作刷新：要确认权威 summary 无剩余工作并完成最后一次历史／active-run 对账。之后，可见会话仍每 30 秒执行一次 bootstrap 基线发现，覆盖未来 schedule／trigger 才产生的新 run 和已经完成的回复；summary 全 false 不是永远不会有自动工作的证明。隐藏页面暂停基线，重新可见立即 bootstrap；每会话最多一个在途请求，失败指数退避至最多 120 秒。聊天 Stop 后继续观察仍有效的后台任务及目标 run 清理；全部停止后也要观察旧批次的清理，但只读刷新不得启动模型。冷刷新、重连或回到页面时重新 bootstrap；has_pending=true 时展示待处理入口并可分页查找、停止单项，不需要先把全部历史加载到内存。翻页期间源事件可能送达或新增，按 notice ID／revision 合并并刷新 summary，不能以最后一页为空代替全局对账；未知／查询失败不当作全 false，不依赖旧 SSE 长连接。
- 用户 steering 与后台事件都从服务端事实重建；旧客户端缓存中的内部 steering 按权威快照移除，不转成可编辑草稿。源 notice 已送达／取消时，旧投递尝试不能在刷新后复活。
- 中英文文案同步、复用现有组件与主题；实施时在同一 PR 更新 `docs/site/docs/guides/conversations/sandboxes.md`。

## 11. 切换边界与排除项

配套 plan 按公共 task／事件持久契约与 command 首个适配 → 会话控制与通知 → Todo 收尾与公共 UI／恢复组织；命令日志确认作为独立关注点交付。实施前审阅本 spec／plan，不再有“先实现 CubeLoop live wait 才能集成”的依赖，也不把 MCP 或 detached subagent 的实现纳入这一轮。

本轮只实现当前 command 真正需要的公共能力，不为未来类型添加空适配器、额外详情表或调度框架。后续 MCP 与 detached subagent 各自形成独立 spec／plan／实现 PR，接入同一任务、Stop 和事件协议，而不是再次复制公共生命周期。

旧数据不能仅因新代码上线获得新的执行权限。旧 run-lifetime command／wake 保持原契约：原 run 关闭后不自动启动新 run；未送达记录按证据作废，已写历史保留。仍在活动 run 内的旧命令先完成或经明确操作停止，不能直接改成 conversation lifetime。原 conversation 后台 execute 仅在有合法通知权时继续；历史停止不因新增 run 停止字段或初始化 generation 被清除。缺少可靠历史停止证明时不自动重放。

旧 monitor 的 line／exit 多次订阅不能静默解释成新的单次条件等待。切换清单单独列出：活跃旧 monitor 必须自然结束或经明确授权停止后才能启用新写入者；不能仅凭一次 stdout 猜测条件已满足。旧通知按 checkpoint 保留 delivered 历史，其余保留原 ID／结果及停止投递原因，切换后不自动重放或合并成新 monitor_result。新 monitor 只按第 4.2 节契约创建，不保留持续订阅兼容模式；实际切换和取消仍需独立授权。

旧内部 steering 按源 wake 关联在读取层归类并退出用户列表；source wake 已 delivered／discarded 时不重新投递，旧失败尝试只作为诊断记录。已经进入 checkpoint 的内容与 metadata 不回写，历史展示根据可靠关联分类；不能仅凭消息正文像系统通知就隐藏一条真实用户消息。

存储切换为每个受管 command 建立唯一 task 关联，将公共控制、owner 与状态迁移到 task，保留命令专属事实；原 wake 记录迁移到统一事件 outbox 并保留 notice ID、dedupe key 和已投递证明。不能因为换了表或 task ID 就重发结果。旧运行数据清点与切换在新旧写入者隔离后完成；最终只有公共 task service 写生命周期，旧 command 状态列和旧 wake 消费路径退出，不长期双写或靠后台同步维持一致。

migration 使用 autogenerate；无可靠历史 deadline 时不猜造过去期限并立即 kill。旧新 coordinator 不同时写同一批记录；保留 provider handles/cursors，部署、数据处理和环境清理需另获授权。

结构升级必须有数据回填门槛：隔离旧写入者后，仅升级到新增结构的指定 revision，保留旧字段／表；完成可重跑的回填及完整性核对后，才允许执行删除旧结构的 revision 并启动新生命周期写入者。Helm init container 与 Compose 的 backend-migrate 都必须使用同一个有门槛的升级入口，不再无条件 `alembic upgrade head`。已有数据的切换是独立维护阶段：先停旧 API／worker／coordinator／排队入口并确认退出，再由持有数据库迁移锁的单一执行者回填和升级；不能把停旧写入者寄托于 RollingUpdate 新 pod 的 init container，也不能让多个副本分别迁移。普通启动只接受已经核对完成的结构／数据；空库在同一迁移锁内通过无旧记录检查后可安装。删除旧字段的 revision 不与尚未具备门槛的版本一起启用。已有库、回填中断重跑、并发升级和绕过回填的启动均需验证。新增 task_id 等回填字段在新增结构阶段允许未绑定；回填核对通过后才收紧约束，历史实例未知仍按 unknown 契约保留，不因收紧约束伪造实例身份。

回填还覆盖旧终态与 outbox 分事务造成的空档：command 已 `notice_state=pending`、但 completion wake 尚未创建。先检查旧 wake 去重键和 checkpoint 的 notice／command 证明；已送达不重放，有明确合法通知权的 conversation-lifetime 工作幂等补一条稳定 completion 事件。缺少继续执行权限、旧 run 已关闭的 run-lifetime 工作或历史停止证据不明时，不提升为新授权，保留 discarded 原因供核对。重复回填或中断恢复不能补出第二条通知。

旧 command 的 sandbox_instance_id 只按可验证的原启动／运行记录回填，不能从当前 UserSandbox.sandbox_id 猜填。无法证明实例归属的 inflight 行保留 unknown 和原句柄，进入明确的人工核对清单，不自动向当前容器 poll／kill、不自动释放 cap。旧会话删除标记必须转换为持久清理意图；已删除会话不会因初始化 generation 而恢复。受理记录的历史来源与批次同样按证据迁移，旧调度／IM 重试不能作为首次新受理越过全部停止。

本轮明确排除共享资源归属／owner 接任／个人 sandbox 路由重构、搜索索引迁移、完整账号外键治理、全站分享／邀请／长连接撤权、OAuth／SSO 登录与凭据治理、provider 配置／密钥版本、账单保留及 CSV 导出改造，以及通用删除回执／恢复 worker／父子删除编排。这些不作为 C2 或其他实施单元的前置条件；若以后要做，需独立确认需求，不在本计划中暗留待办。现有授权检查仍保留，不将缩小范围解释为允许越权执行。

其他排除：通用工作流引擎／任意依赖图、任意工具自动后台化、MCP 长任务适配、detached subagent 实现、恢复旧 live Session、跨会话移动任务、业务任务分组／按分组取消、暂停所有未来自动化的全局开关、monitor 持续订阅／多次唤醒模式、新 provider、自动删除 orphan、trace 检索改造、全站 UI 重做。聊天 Stop 只控制目标 run；独立全部停止关闭当前批次，不暂停未来定时任务或用户新请求。父任务关系仅用于执行归属和停止传播，不扩成依赖调度。

已确认的产品方向：后台任务属于 conversation；聊天 Stop、单任务停止、全部停止分开；monitor 一次等待只产生一个最终结果，日志／前台 stream 与模型唤醒独立；后台事件不显示为用户 steering。execute 默认总期限可配置，默认 1 小时。实施授权不等于新契约已经实现，部署及线上迁移仍需单独授权。

## 12. 验收不变量

1. 小时级后台任务存在时，原 run 能发 Done、释放 active slot；无新输入／事件时不产生模型调用，不依赖原 run heartbeat。本轮以 command 证明跨 worker 恢复，不把这一承诺自动扩展到没有恢复能力的执行方。
2. 原 run 结束后命令仍可观察；worker 重启后从同一 provider_ref 接管，不重新执行 shell command；sandbox 保活不依赖原 run。
3. 同一后台事件在活跃 run 中经内部输入通道接收，无活跃 run 时启动新 run；两条路径都保留来源与 notice ID，不伪装成用户发言；新 run 恢复原会话消息和 Todo，HITL 不被绕过。
4. run 收尾、用户新消息、wake claim 和 checkpoint 的竞争不导致并行活跃 run、丢通知或重复已提交的通知。
5. 前台已交付最终结果的命令不再发 completion；后台交接后即使原工具结果投影失败，命令与结果也可恢复查询。进程终态但输出仍在恢复时明确 result_pending，最终结果就绪前不触发 completion 消费。
6. Todo 的 `wait_for_tasks` 只接受可访问的公共 task ID，经公共查询校验；不存在／已被用户停止／无自动通知的任务、无恢复来源的执行和过期等待声明不能建立新等待许可，command ID 不能冒充 task ID。合法等待不强制续跑、不伪造完成，新输入使旧收尾许可失效；先前有效等待随后被取消按第 30 项收尾，deadline 到期但结果待交付不等于通知已被用户取消。
7. 聊天 Stop 只绑定目标 run，覆盖 preparing／running／paused HITL，停止前台工具／同步 child、未交接 reservation 及目标用户 steering，不关闭 generation。先前已后台化的构建和 monitor 仍运行并可通知；无当前 run 时输入框无 Stop，任务区可单独／全部停止。全部停止才关闭批次并覆盖其 run、task、未提交输入和待发结果，重启及旧通知不能恢复这一批执行。
8. 旧 run-lifetime notice 在原 run 关闭后不能重开会话；不能把现场的取消复活漏洞通过更名当作修好。
9. 停止受理与实际退出分开，重复 kill 幂等；真实 exit code、deadline、8-command cap、scope 和 owner fencing 不回归。execute 未指定 timeout 默认 3600 秒，可配置、可显式覆盖；非法配置拒绝，后台／notify=false 不绕过期限，配置变化／worker 接管不改 deadline。monitor 的 persistent 仅决定期限，不决定通知次数。
10. 日志写入未确认不推进 cursor；cleanup-only 失败不重放，日志错误不把已退出进程显示为运行中。结果依赖日志恢复时只解锁原最终事件，不追加第二次通知；可靠不可恢复时报告不完整。单任务停止／全部停止取消通知后，日志恢复不唤醒；仅停止无关前台 run 不取消独立后台结果。
11. UI 在没有旧 run SSE 的情况下发现自动回复；刷新、乱序响应、快速完成的新 run 不造成假成功或漏回复。
12. prompt-cache 稳定、每 run 唯一 Done、required event consumer、durable checkpoint 与 attempt fencing 不被绕过。
13. 聊天 Stop 与 reservation／handoff 双向竞争：Stop 先提交则未交接任务及迟到句柄继承停止，handoff 先提交则后台任务保留。指定旧 run 的重试不误停后来 run；全部停止与新受理竞争只关闭目标 generation。checkpoint 在途保留真实历史，202 与远端确认分开，无可靠退出证据不假报成功。
14. 单独停止 task 取消其执行权限及未提交通知；已有／迟到受管后代继承停止约束，同级任务不受影响。折叠事件行不触发停止或取消 API。用户 steering 的撤回／恢复功能保留，内部事件不进入这些入口。
15. 实时投影、bootstrap、历史恢复、刷新与跨 run 重试均不将后台事件放入 `pending_steers`，也不生成同内容用户气泡；同一 notice 只有一个产品事件，旧 failed 尝试及已 delivered／discarded 的源通知不在输入框复活。
16. 不同 task 的已就绪事件可投递同一 run，各自独立输入／确认／撤回；单独停止 A 不撤回 B，提交未知先对账。一个新 monitor 至多一个最终事件，日志不变成通知；重试只更新原行，结果按原始文本安全展示。
17. 任务列表、Stop、事件路由和 Todo 校验只依赖公共 task 身份／状态／能力，不要求 shell、sandbox、exit code 或日志字段；命令详情仍保留真实进程证据。公共状态只有一个写入流程，无双写漂移，原 notice ID 在存储迁移后仍能对账。
18. 本地停止、远端取消请求和确认结束分别表达；不支持取消时仍阻断自动唤醒并显示限制，不伪造 cancelled、不重试不存在的接口。观察超时不等于执行失败，启动回执丢失不触发无幂等保证的重做；deadline 通知与用户停止后的通知丢弃不混淆。
19. 本轮不改变普通 MCP 调用或同步 subagent 的执行方式，不为“预留接入”发布可用的 detach 承诺。未来类型需另行验证恢复、权限、取消和结果契约；父任务／批次归属不能作为绕过审批或并发限制的手段。
20. 删除会话与关闭批次、登记停止意图原子受理；删除后 API 不可见仍能清理原实例，迟到 reservation／句柄不漏管、不重开会话，不影响其他会话。worker 在删除提交后崩溃，恢复仍能继续清理。
21. 聊天 Stop 不取消其他独立待执行 occurrence／后台结果。全部停止前已受理的 occurrence、busy／IM 重试与旧 notice 不能越过关闭批次；之后独立新 occurrence 可开启新批次。受理与全部停止串行、旧控制请求不误停新工作，权限或目标失效仍拒绝。
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
34. 现有 workspace／账号删除、topic 归档和成员／参与者移除只对相关原执行登记停止、取消未提交通知并保留未决证明；迟到句柄、重启及重新加入不复活旧 admission，其他 actor／scope 的工作不被本次清理误停。硬删前安全处理新增生命周期 FK，失败保持可重试且 UI 不假报完成；不要求通用删除回执或重构原数据归属。
35. 用户 run 启动成功后丢失响应，原 run 结束及 Redis active slot 消失后再次提交相同来源，仍返回预先绑定的原 run，不产生第二次模型／工具执行；启动前崩溃保持可恢复。
36. A 的后台结果遇到 B 的 active run 时不进入 B 的 Session；slot 释放后只以 A 的当前权限运行。A 权限撤销则不投递，同 actor 通知仍可共享 run。
37. `install ...` 的安装或合成消息提交后丢失响应，重试沿用同一 admission／结果，只出现一次安装及一组历史，不创建额外模型 run；执行前全部停止／权限撤销仍阻止副作用。
38. schedule 删除分别与 occurrence claim、busy／IM 排队、启动及回执丢失竞争，未执行来源不再启动，未决证明保留并可恢复；已执行 run 的历史和同会话其他工作不受影响。
39. B 的用户 steering 到达 A 的自动 run 时不进入 A 的 Session，也不借其凭据执行；释放 slot 后只以 B 的当前权限执行一次，准备期／HITL 排队／pub-sub 不能绕过身份检查。
40. 配置和显式超时同时覆盖默认、合法较长值、技术上限及上限加一；超大值在配置加载／参数验证时拒绝，不产生半条 reservation 或未处理的日期溢出。
41. 可见页面已经完成空 summary 对账后，仍能发现后来触发的 schedule／trigger run 及其回复；隐藏恢复、查询之间已完成和请求失败退避均覆盖。
42. 其他参与者不能回答／批准原 actor 的 HITL 后借其凭据执行；原发起者失权同样拒绝。聊天 Stop／全部停止均按会话控制权限清理各自目标，不走回答路径。
43. 纯用户 run 正常结束后仍可 reflection／consolidation；目标 run 的聊天 Stop 或全部停止在模型调用前／返回前提交则禁止后续调用与写入，停止其他 run 不误伤。数据库双向 barrier 验证停止先提交拒绝写，记忆先持锁先提交；整批修改及容量清理回滚完整。覆盖个人／workspace scope、topic 归档、participant 撤销、generation 失效及完成凭据替换；快速新消息不夺走旧清理证明，HITL 暂停和到期接管不回归。
44. 现有 sandbox restart／delete 与 reservation、provider start、迟到句柄竞争时，原实例任务继承停止和通知取消；失败／实例未知不假报完成、不丢句柄，coordinator 重启继续清理，旧任务不作用于后来新建的实例。保留既有权限及 PVC 策略，不增加通用删除操作或状态 API。

45. monitor 多次检查和任意日志输出不唤醒；条件脚本 exit/0、非零失败、deadline 超时分别只形成一个最终结果及 notice。成功／失败／超时／主动取消的并发裁决、结果落库后崩溃、监听进程退出与日志恢复均不生成第二条 line／exit 事件；持续等待不占 live Session，persistent 也只通知一次。取消／结束监听不杀被观察的独立任务。旧多次 monitor 的切换有明确清点与授权门槛。
46. 前台 execute 多段输出在命令结束前仍通过 on_update／SSE tool_result 更新同一工具卡，模型文本流和后台日志读取不依赖 monitor wake。monitor 结果触发回复后聊天 Stop 不重放已提交结果；首条提交前停止则对账后取消该次处理，源结果保留。其他未提交的独立结果仍可继续投递，不全量暂停通知。

公共契约用 command 首个实现验证登记、停止、恢复、事件、Todo 与 UI，不靠伪造未来适配器宣称集成完成。涉及真实 Postgres／Redis／FastAPI 的用例放 e2e，只在最外层执行方注入故障。前端业务流覆盖“长任务 A 在后台 → 当前回复 B 流式输出 → 聊天 Stop B → A 仍完成并通知一次”，以及“全部停止 → 旧结果不续办”、单任务停止、通知去重和刷新恢复；不以静态元素计数代替契约。长等待用可控时钟及重启验证，不真等数小时；real_llm nightly 另查模型是否忙等。文档检查不代表运行时验收通过。

<a id="stop-implementation"></a>

## 附录 A. 停止与请求受理的实现约束

本附录保留原 5.2 的实现要求，只按问题重新组织，不增加产品功能。确认按钮行为先看 [5.2](#stop-actions)；实现和验收时再按下表查阅。

| 要回答的问题 | 位置 |
| --- | --- |
| 怎样只停当前执行，不误停后台任务？ | [A.1 聊天 Stop](#stop-current-run) |
| 怎样防止全部停止后的旧工作复活？ | [A.2 全部停止](#stop-all-generation) |
| 单项停止怎样确认成功？ | [A.3 单任务与停止回执](#stop-task-receipt) |
| 消息重试怎样避免重复执行？ | [A.4 请求受理](#request-admission) |
| 定时任务／trigger 怎样排队和恢复？ | [A.5 自动来源](#automatic-sources) |
| 删除或撤权怎样清理已有任务？ | [A.6 删除与撤权](#delete-and-revoke) |
| 多人会话使用谁的执行权限？ | [A.7 执行身份](#execution-identity) |

本节常用词：run 是一轮模型执行；task 是受管任务；admission 是已保存的请求受理记录；notice 是后台结果事件；checkpoint 是已持久保存的对话状态；generation 是「全部停止」使用的会话执行批次，不是一个 run。

<a id="stop-current-run"></a>

### A.1 怎样只停当前执行，不误停后台任务？

#### 请求准确指向一个 run

- 请求携带界面正在显示的 `run_id`，覆盖 preparing、running 和持久 paused HITL；不能在重试时重新解释为“停止此刻最新 run”。
- 只有后台任务而没有当前 run／HITL 时，输入框不显示停止；用户从任务区域停止单项或全部停止。
- 目标 run 已终态时不改写原结果；若仍有该 run 的清理／自动后处理，停止意图仍生效，无剩余工作则返回原事实。
- 任何情况都不影响后来 run。

#### run 的停止与后台任务权限分开

- 先在该 run 的持久启动 admission 上记录 `run_stop_requested_at` 并关闭其继续执行和接收输入的资格，再取消 Session、同步子代理、HITL 和尚未交接的前台任务。
- run 停止与 admission 因失权／全部停止而撤销分开：前者禁止目标 run 的模型、工具、HITL resume 和自动后处理，不撤销已交接 task 的独立后台权限；后者仍约束该来源的相关任务。
- 后台执行与投递不因 originating_run_id 被停止就丧失资格，仍检查自身／父任务、原 actor 和 conversation generation。

#### 停止与后台交接同时发生时

- 前台 task 以 `originating_run_id` 与 `backgrounded_at` 判定，不推断业务任务分组；同步 child 使用所属前台 run 的控制关联。
- 聊天 Stop、reservation 和 handoff 在同一 conversation／admission／task 锁序下裁决：Stop 先提交时，尚未交接的 reservation 继承停止，迟到句柄保存后清理，不能再交接逃过取消；handoff 先提交时任务已独立，不被聊天 Stop 取消。
- 显式 background 与 monitor 同样以持久交接为界，不以工具响应是否已到浏览器判断。
- 后台继续存在时，界面明确提示“当前执行已停止，后台任务仍在运行”。

#### 用户追加输入和后台结果分别处理

- 属于目标 run 且尚未提交的用户 steering 撤销并保留恢复编辑入口；其他独立排队消息／来源不被全量取消。
- 已提交输入保留历史，不自动重放业务处理。
- 若后台 notice 是目标 run 的首条输入，聊天 Stop 同时终止这次结果处理：已 checkpoint 的仍为 delivered，尚未提交的在确认旧 attempt 失权后记 discarded，不能靠重试另开 run；源任务的结果仍可查看。
- 其他仅尝试追加但尚未提交的独立 notice，先对账并撤销旧投递 attempt，再按原事件身份重新路由；已提交的则不重投。
- 不会因为停止某条回复，就暂停所有 monitor 或取消所有待发结果。

#### 旧执行不恢复，独立新工作仍可继续

- 停止目标 run 的旧重试与 HITL 恢复永远不能重开它；仍有效的独立后台结果、新用户消息或调度可以在旧 slot 安全释放后正常产生新的执行。
- run 在正常 prompt／HITL answer 路径进入终态时，先把终态和时间按当前 attempt 写进持久 admission，再清理 Redis slot 和临时事件。恢复只能使用同一 run 的这份终态：Redis 终态存在时必须一致，Redis 元数据已过期时可重建 cleanup-only 状态；不能只看 completed checkpoint 猜测成功或失败。
- HITL answer 已经产生终态、但原 pending question 因收尾中断仍存在时，恢复者取得清理权后再次核对 question／run，只删除这条遗留问题并完成清理，不恢复 Session、不再调用模型。缺少持久终态或状态冲突时保留待对账。
- 界面标明新执行的来源，不把它呈现为被停止的回复自行恢复。

<a id="stop-all-generation"></a>

### A.2 怎样防止全部停止后的旧工作复活？

#### 全部停止的范围

- 全部停止不以有无 active run 为前提，包含之前 run 留下的后台工作、starting reservation 和已终态但结果未送达的任务；不是销毁 sandbox，不影响其他 conversation，也不承诺发现和杀掉非受管进程。
- 它是明确标注范围的独立入口，不复用输入框停止图标。

#### 用会话执行批次记录停止边界

- 为覆盖全部停止与新启动／通知重试的竞争，conversation 持久保存 `execution_generation` 和停止标记。
- generation 表示一批仍有执行权限的工作，不等于 run ID；多个正常结束的 run 及其后台工作可以属于同一批。
- run、task、任务事件与待提交输入都关联受理时的 generation。
- 只有 generation 与当前批次相同且未停止才有继续执行资格；停止后新开批次时单调递增，不能只清空停止时间使旧工作重新有效。

#### 停止与后续恢复顺序

1. 先持久关闭本批次的执行与自动唤醒权限，再发送取消信号和做进程清理。不能只扫描一次当前 running 行后逐个 kill；否则扫描后的 reservation 或迟到 completion 会漏过。
2. 关闭标记提交后，新 reservation、内部输入投递、automated run admission、后续模型／工具执行都检查批次仍有效。已在途的远端请求无法瞬间撤回：保留其真实回执；迟到启动回执必须登记执行句柄，随后按已有停止意图和适配器能力处理，不能丢句柄或清除停止标记。
3. 持久取消该批次的未提交 wake／内部输入及用户 steering，撤销该批次的运行和 HITL 继续执行资格。尚未完成的清理可由 coordinator 重试；worker 重启、owner 过期、Redis key 消失不能重新授予执行权限。已有更早停止批次的未完成清理也继续进行。
4. 全部停止之后明确受理的新用户消息，或独立授权的下一次调度执行，可以开启新批次；旧请求重试、旧任务结果和 HITL resume 不可以。重复点击或重试同一次全部停止只作用于原目标批次，不能误停后来的新任务。新请求仍等待旧 active-run slot 安全释放，不能通过取消中的旧 run 接收。旧通知不会因用户恢复聊天或新调度到期而获得新批次身份。

#### 与新的定时触发竞争时

- 首次受理与全部停止使用同一 conversation 锁串行：固定会话的 occurrence 被领取并登记待执行时就绑定批次，不等 worker 真正开始调用模型；先受理则属于全部停止关闭的旧批次，后受理且来源有独立授权才可进入新批次。
- 全部停止后不得把旧 occurrence 的 busy 重试、IM 队列重试或恢复任务解释为下一次触发。
- 经 IM 转交的调度仍保留原 occurrence 身份。
- 这里只补入口分类、幂等绑定和停止边界，不改变调度计划、missed／busy 策略或引入新调度引擎。

<a id="stop-task-receipt"></a>

### A.3 单项停止怎样确认成功？

#### 停止一个 task

- 单独停止 task 同样先持久写入停止意图与通知取消标记，再异步联系适配器；不关闭整个会话批次。
- 即使任务刚刚自然完成，也可以取消其尚未送达的通知，保留真实结果和 exit code。
- 已提交的输入及副作用无法撤回，单任务停止不取消已经接收其他输入的共享 run；要停止当前回复用聊天 Stop，要停止本会话当前全部执行用全部停止。
- 停止 task 的后代约束在受理新子任务时也检查，不能只扫描已有孩子；本轮 command 没有受管子任务，不改变当前同步 subagent 实现。

#### 请求受理不等于远端已经停止

- 两种停止接口都在持久受理后返回 202，分别携目标 run_id 或 execution_generation；表示目标执行权限已撤销，不表示远端已结束。
- UI 按操作目标显示“正在停止”，不能因无关后台任务仍在运行就让聊天 Stop 一直显示未完成。
- 支持远端取消但尚未确认的目标显示“停止未确认／正在重试”；明确不支持时显示“已停止后续处理，远端任务无法取消”，不假报取消成功、不无限重试不存在的接口。
- 状态、错误和已取得结果仍可见；持久写入失败不能返回成功，取消范围内的迟到结果不能产生新的自动续办。

<a id="request-admission"></a>

### A.4 消息重试怎样避免重复执行？

#### 先固定请求来源

- 受理来源必须由服务端区分，不能仅凭 `trigger="automated"` 决定能否开启新批次。
- 现有 fixed-target schedule 会重复进入同一 conversation；它的新 occurrence 保留独立授权，但旧 command completion 没有这个权限。
- 持久受理记录保存 `source_kind`、稳定 `source_id`、目标 conversation 和受理时 generation；同一来源重试只读取原绑定，不能重新分配批次。
- 用户消息、schedule occurrence、既有 trigger occurrence、后台 notice 分别来自已鉴权的入口，客户端不能自报为内部调度。

#### 同一请求必须使用相同内容与首次执行设置

- 用户消息的受理身份还绑定不可变请求摘要：正文、有序 attachment IDs、请求的 model_key 与规范化 reasoning 等影响执行的提交字段。
- 相同 client_message_id／receipt／steer_id 携带不同请求时拒绝复用，不能把改过的消息当作原 run 的成功重试。
- 摘要按规范化请求计算，不包含发送时间、临时 URL 或重试时重新解析的默认模型。
- 首次解析出的模型／provider 选择、有效 reasoning 等执行设置，以不含凭据的不可变快照与受理记录在同一事务持久保存，先于排队及 run 创建；不能只存在内存或尚未创建的 run 上。
- 受理提交后崩溃，即使默认配置改变，重试仍读取原快照而不重新选默认值；权限和凭据仍按当前状态校验，原选择不可用则明确失败，不静默换模型。
- 旧记录若没有可靠请求证明，不猜造摘要或执行快照。

#### 会话设置和附件一起受理

- 首次用户受理的事务同时保存其会话 model_key／reasoning 选择，并校验、锁定附件后将其从 pending 标记为 attached；任一部分失败则整体回滚。
- 会话选择按新的受理顺序更新，相同来源重试只读取原受理，不覆盖后来消息的选择；后台新 run 因而能看到最后一次已受理的会话配置。
- 附件孤儿清理与受理按同一行的锁／删除资格串行裁决：受理先成功的附件不能被先前扫描结果删除，清理先获得删除权时受理明确拒绝，不能接受即将消失的对象。
- 持久附件引用不保存临时签名 URL。

#### 先保存 run ID，再启动

- 创建 run 的入口先持久预分配并绑定稳定 run ID，再调用 RunManager；不能在 start_run 成功返回之后才补 admission.run_id。
- 同一来源并发、启动回执丢失或原 run 已终态后重试都返回原绑定；run 的持久启动／结束证明参与对账，Redis active slot 消失不代表尚未执行。
- 没有可靠失败前未启动证明时不重放模型／工具，既不能改用新 run ID，也不能用同一 ID 重新执行已完成的 run。
- 只返回已经启动／结束或被撤销的原绑定时，仍检查当前访问权限，但不因原模型已移除而拒绝读取回执；模型及凭据可用性是实际执行的门槛，不是读取幂等结果的门槛。

#### 不启动模型的 install 快捷操作也要去重

- 不创建模型 run 的用户快捷操作也受同一来源幂等保护。
- `install ...` 首次受理固定为快捷操作分支，保存安装目标／结果及稳定的合成消息 ID；安装变更与操作回执原子提交，checkpoint append 使用这些 ID 对账。
- 安装后或消息写入后响应丢失，只补尚未提交的消息并返回原 SSE 结果，不重复安装、追加第二组历史或改走模型 run。
- 全部停止／删除与执行前权限复查仍生效；已提交的安装副作用不假装回滚。

<a id="automatic-sources"></a>

### A.5 定时任务／trigger 怎样排队和恢复？

这里沿用既有 schedule、trigger 和 IM 入口，只补后台生命周期需要的受理与恢复边界，不增加调度产品。

#### 重试使用首次保存的内容与执行身份

- schedule occurrence／trigger event 在首次持久领取时保存已渲染内容及影响执行的非凭据参数，包括当次目标策略和模型选择；目标 conversation 尚未确定时先随源记录保存，目标确定后将该快照绑定到 admission，再排队。
- 忙碌重试、IM 转交和 worker 恢复均使用同一快照，不重新渲染可变模板或读取修改后的 prompt；编辑定义影响之后的新 occurrence／event。
- 当前停用／撤销授权、目标删除等控制仍重新检查，快照不授予永久执行权限，也不重开旧 generation。

- 快照还固定原执行 actor；trigger 在持久受理事件时，在定义行锁内保存 run_as_user_id，后续修改只影响新事件。
- worker／IM 重试校验原 actor 当前的账户、成员身份和目标权限，不替换成定义的新 actor，也不借编辑获得不同的个人上下文。

#### trigger 返回 accepted 后，重启仍能继续交接

- trigger 的 202 受理还必须有持久的后续消费者，不能只依赖进程内 create_task。
- 完成入口校验后，将可执行事件、冻结内容及排队状态持久提交，再返回 accepted；用于去重／审计但尚未通过入口校验的记录不自动获得执行资格，重复请求不能把未完成受理误报为已排队成功。
- 专用 trigger worker 启动和运行中均领取可执行事件，用有期限的 claim 接管崩溃遗留工作；重试时间和尝试次数持久保存。
- conversation 目标一旦创建就与事件在同一事务绑定，重试不另建会话；run／IM 转交沿用稳定来源和已绑定 admission，回执丢失先对账，不重复执行。
- 不绕过过滤、限流、停用或当前权限，也不改变调度计划或建立新通用工作流引擎。

#### 删除 trigger：先停止派发，再清理记录

- 删除 trigger 与事件受理／claim 串行：先持久停用并标记删除，取消未交接事件，普通接口隐藏它；不能立即硬删 TriggerEvent。
- 已绑定目标、admission、run 或 IM handoff 的事件保留取消及对账记录，消费者只做取消／确认，不再启动新工作。
- 已经交接的 run 保留历史，不因删除 trigger 误停同 conversation 的其他工作；需要停止当前会话仍走全部停止。
- 只有没有未决交接及清理时才能按依赖顺序清理源记录。

#### 删除 schedule：取消未开始的 occurrence

- 删除 schedule 使用同一源定义／occurrence 裁决：定义行锁内持久删除标记、清空下次触发，并取消未开始执行的 occurrence，包括已 claimed、busy 重试和已进入 IM 队列的项。
- 领取、目标绑定、执行启动资格与删除串行，不能只在首次扫描时检查 deleted_at；冻结的 prompt 不授予删除后的启动权。
- 已有 admission、预分配 run ID 或交接回执未决时保留来源证明并对账，不重新派发。
- 已确认开始执行的 run 保留历史，删除定义不等于全部停止，不取消同会话的其他工作；仅完成队列交接还不算开始执行。
- 清理完成前不物理删除 occurrence。

<a id="delete-and-revoke"></a>

### A.6 删除或撤权怎样清理已有任务？

这里只把已有删除／撤权入口接入相关任务清理，不做全站资源归属或授权治理。sandbox Restart/Delete 仍沿用既有权限与确认，作用于原实例；具体执行门槛在第 9.2 节。

#### 删除会话或归档 topic

- 删除 conversation 复用全部停止：在软删除事务中关闭批次、登记任务停止并取消未提交通知；失败不报告删除成功。
- 隐藏后 coordinator 仍按原 scope／实例清理，迟到启动句柄保留，任何来源不得重新打开已删除会话。
- topic 归档同样关闭所属会话的执行；不改变原归档、资源归属或 owner 接任策略。

#### 删除 workspace 或账号

- workspace／账号硬删除前，只补本次生命周期数据的安全处理：先停止实际删除范围内的 run／task，取消其未提交输入和通知，再按依赖清理新增 event／command／task／admission 引用。
- 账号路径按原 actor 定位，不能以停止 A 为由关闭共享会话整个批次或取消 B 的独立工作。
- 未确认退出或交接时保留句柄与记录，返回 cleanup_pending，不把清理请求当作硬删除成功；用户可通过原删除入口重试。
- 最终删除与新受理使用同一权限／目标锁复查，发现新的受管工作就先清理，不能让新 FK 或活进程被提前级联删除。
- coordinator 负责恢复任务清理，不自动代替用户完成账号／workspace 硬删除。

#### 移除成员或参与者

- 成员／参与者移除沿用现有权限判断，只撤销因本次操作失去执行权限的原 admission，并登记其 task 停止和未提交通知取消。
- 仍有有效访问来源或属于其他 actor／scope 的工作保留；重新加入也不能清除旧 admission 的撤销事实。
- 执行、工具和投递边界重查当前资格；必要的清理只观察／取消原句柄，不能借清理权限启动新工作。
- 不新增全站授权版本、成员回执或分享／登录撤权协议。

#### 停用或删除自动来源

- schedule／trigger／IM 来源被删除或停用后，其尚未开始的受理不得继续启动；存在启动或队列交接未决时，保留原来源身份及核对证明，不能提前级联删除。
- 已合法启动的工作按既有控制语义处理，不以删除来源误停其他会话工作。
- 这里仅接通 lifecycle 的取消与对账，不重构 IM connector 的删除 UI 或通用删除流程。

#### 失败时保留什么，不增加什么

- 清理错误由现有接口明确返回，相关界面不能把 cleanup_pending、断网或 401 当成删除完成。
- 本轮不增加跨硬删除查询的删除凭证、通用删除操作表／恢复 worker、父子删除编排，也不承诺解决原有共享资源和用户外键的全部删除问题。
- 原系统的数据保留与删除政策不因本次任务清理而改变。

<a id="execution-identity"></a>

### A.7 多人会话使用谁的执行权限？

#### 输入不能借用另一参与者的身份

- 用户 steering 与后台通知均不能借另一参与者的凭据执行。
- A 的 run 只接收 A 的新增输入；B 的消息以原身份持久排队，slot 释放后按 B 当前权限受理，准备缓冲、pub/sub、DB 领取及 Session 提交都遵守此规则。
- 停止或删除 A 的工作不丢弃 B 的独立排队项；已提交但处理中断的输入保留历史，不自动重放可能已有副作用的操作。

#### 确认问题与停止执行是两种操作

- HITL 回答／审批只接受原 admission 的 actor，并重查当前权限；其他参与者只能看到“等待发起者回答”。
- 按会话控制权限执行聊天 Stop 或全部停止都不是回答，不调用模型或产生新的审批授权。
