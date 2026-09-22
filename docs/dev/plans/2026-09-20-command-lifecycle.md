# 后台任务生命周期实施计划

- 状态：实施中，遵循已确认的 [spec](../specs/2026-09-20-command-lifecycle-design.md) 和已收缩的生命周期范围。#635 已补齐 C1 的一次性 monitor 与公共结果就绪契约；#636 已拆开 run Stop／批次关闭的持久权限，并修复取消后重复补交接。上述增量已通过回归及本地 Codex 复审；C2a 的公共接口和持久恢复仍在接入，不代表整个 C2 已完成。新 coordinator 仍未启用，部署与线上数据操作需独立授权。
- 日期：2026-09-20；更新：2026-09-22（America/Phoenix）。保留原路径供已有链接引用。
- Goal：让 command／monitor 跨 run 运行、可恢复观察并可靠停止，结果按 conversation 投递，用户能区分模型运行、后台执行与通知处理。
- Architecture：公共 task service 保存生命周期，command 适配器处理原实例、进程和日志，并将一次性 monitor 的最终结果交给公共事件通道。conversation 控制服务区分按 run_id 的聊天 Stop 与按 generation 的全部停止；已后台交接的工作不继承发起 run 的单独停止。通知按 conversation 内部输入／新 run 投递，前台输出仍走 on_update／SSE；CubeLoop 仅增加 Todo 收尾扩展，不保持小时级 Session。
- Tech stack：FastAPI、SQLModel／PostgreSQL、Redis、CubeLoop ExecutionSession、现有 Sandbox driver、React 19／Next.js／`@cubeplex/core`。
- 核对基线：CubePlex `f622d97e`；本地 `../cubepi` 的 CubeLoop `65ff7096ca2e78e707bdd1d96f8dbca38ef5a0cc`。

## 阅读顺序

先看[交付顺序与当前待办](#delivery-order)，判断还差什么；实现时查[公共契约](#shared-contracts)和对应单元。C2 已按停止、消息入口、自动来源、清理挂接拆开；[C7](#c7-cutover)集中说明最终切换门槛。[实施历史](#implementation-history)只保留已有提交和验证记录，不作为新设计已完成的证明。

## 0. 开工条件与交付边界

- 设计 worktree 没有 `.worktree.env`、服务或数据库分配；按 [worktrees](../../worktrees.md) 建立隔离实现环境，读取分配，不用主库验证。
- 设计已获用户审阅确认；按 feature workflow 先形成设计 PR，再实施。代码按下述关注点拆分，不将设计提交与代码混成一个 PR；部署与数据切换不属于本次实现授权。
- execute 缺省总期限已确认：后端配置 `sandbox.command_default_timeout_seconds` 默认 3600 秒（1 小时），显式 `timeout_seconds` 优先；C1 实现配置校验和持久 deadline，C4 更新工具说明，C5 展示实际期限。15 秒前台预算、monitor 期限及平台上限保持各自语义。
- 本计划取代旧 live-run 计划；旧 CubeLoop awaiting-work spec／plan 不再是依赖，也不在本轮修改。没有 IdlePolicy、waiting ExecutionResult、run waiting 事件或 run command scope 表。
- MCP 普通调用与当前同步 subagent 保持现状；不实现未来适配器、工作流 DAG、业务重试、跨会话迁移或独立任务中心。
- 范围以 spec 第 11 节为准：共享资源归属、全站分享／长连接撤权、OAuth／SSO、provider 配置版本、账单／CSV，以及通用删除回执／父子删除系统均不在本计划，不作为任何单元的完成前提。保留现有鉴权，只补受管任务直接需要的停止、恢复和新增数据清理。
- 每个单元按一个关注点组织提交／PR，依赖单元串行落地，不让多个实现者并行改同一生命周期状态机。中间版本不承诺可独立部署；完整链路通过后统一切换，不为阶段上线增加转发兼容 API 或双写状态。
- 按用户要求使用 local Codex review，不再触发 GitHub Codex 审核。每轮修订先验证、commit、push，再运行本地审核；独立核实发现的问题，修复后再次验证、commit、push，再在同一审核会话中复审。审核结论与测试证据直接向用户说明，不借审核扩大范围。

<a id="delivery-order"></a>

## 1. 交付顺序与当前待办

| 单元 | 交付关注点 | 依赖 |
| --- | --- | --- |
| C1 | 公共 task、command 管理及结果就绪契约 | 无 |
| C2 | 停止与恢复 → 消息入口 → 自动来源 → 最小清理挂接 | C1；小节分别验收 |
| C3 | 独立后台事件投递与 checkpoint 对账 | C1、C2；消费 C1 的结果就绪契约 |
| R | CubeLoop Todo 可校验等待声明 | 可独立开发，仅在上游仓库交付 |
| C4 | 工具交接、宿主 Todo 校验与正常结束 run | C1–C3、R；消费 C1 的结果就绪契约 |
| C5 | 任务／事件 API、bootstrap 与前端接入 | C1–C4；复用 C2 的消息和停止接口 |
| C6 | 命令日志写入确认与临时分片清理 | C1；与 C3/C4/C5 联调 |
| C7 | 回填、部署入口门槛与统一切换准备 | C1–C6、R 集成及相关回归全部完成 |

推荐实施与审阅顺序 C1 → C2 → C3 → C4 → C5 → C7；R 可先完成，C6 在 C1 接口稳定后独立实施。C3/C4 可以先按公共契约开发，但结果可读性必须与 C6 联调通过，不能把 C6 当作可选收尾。C7 是已有迁移要求的独立交付，不是新增功能；完成准备也不等于获准部署。

### 1.1 当前待办

以下合并历史基础与本轮新增验证；已核对 #634–#636 仍为开放 PR。C1 的新证据列在文末，不把旧测试或单元审核当作全链路验收。

| 类别 | 已有基础／剩余工作 | 对应单元 |
| --- | --- | --- |
| 可复用，保留回归 | #634 的基础模型、reservation 与默认期限；#635 的原实例／owner／停止事实；#636 的受理快照、run 回执和记忆事务保护 | C1、C2 |
| 本轮已补齐 | #635：一次性 monitor、冻结最终结果、公共 pending／ready／unavailable；本地审核发现的子任务旧缓存竞态已复现并修复 | C1 |
| 本轮已补齐的控制基础 | #636 已同步 C1，拆出 run Stop、封住新 reservation／迟到 handoff、保留已交接任务及结果；用户输入按来源／目标取消；已取消前台任务清理后不再反复补交接 | C2a，尚非完整交付 |
| 正在接入 | 明确目标的停止 HTTP 接口、preparing／HITL／终态收尾失败的持久恢复；随后接通真实输入来源写入 | C2a，之后 C2b |
| 尚未接通或完成 | Web／IM／steering／schedule／trigger 入口、停止恢复、最小删除挂接、结果投递、工具交接、UI 与日志收尾 | C2a–C2d、C3–C6 |
| 上游已有提交，宿主仍待集成 | CubeLoop #231 的 Todo 扩展；确认发布版本后接入依赖与宿主校验 | R、C4 |
| 尚未统一切换 | 数据回填、部署启动门槛、最终结构收缩与完整链路验证 | C7 |

继续完成 C2a，再按 C2b–C2d 接入。C2 的既有记忆／收尾保护列在[保留回归](#c2-regressions)，不重新包装成一组新增功能。C1 的通过只覆盖持久运行时；C3/C4 消费公共结果契约、C6 日志确认联调及 C7 切换仍须分别验证。

各单元中未带仓库前缀的 backend 路径均相对于 `backend/cubeplex/`；R 单元的路径相对于 CubeLoop 仓库。标“新”的文件由对应实施单元创建。

<a id="shared-contracts"></a>

### 1.2 身份、事实与写入者

| 记录 | 锁定契约 |
| --- | --- |
| `background_tasks` | 唯一公共执行状态；scope、conversation、task kind、发起身份／run／tool、可选 parent、generation、notify policy、deadline、停止原因、通知取消标记、owner token／lease、结果引用、revision、backgrounded_at |
| `sandbox_commands` | `task_id` 唯一一对一；command ID、user_sandbox_id、不可变 sandbox_instance_id、provider_ref、参数、真实进程观察／exit code、日志 cursor／state、monitor 唯一 outcome／结果引用 |
| `background_task_events` | 单一 outbox；稳定 notice ID、task ID、reason、摘要／结果引用、去重键、pending／claimed／delivered／discarded、投递 attempt 及 checkpoint 证明 |
| `conversation_execution_admissions` | 来源／稳定 source ID、actor、conversation、generation、run 关联、不可变用户请求摘要与有效执行设置快照；scope 内来源键唯一。C1 建基础关联，C2 在开放受理入口前补齐摘要／快照及事务契约。不是第二份 run 状态机或调度计划 |
| conversation／输入 | conversation 保存仅供全部停止的 generation／关闭标记；原 run 的启动 admission 记录独立的 run_stop_requested_at。用户／内部输入保存 generation、source 和目标 run／投递 attempt，task／事件 revision 分开 |

公共 task 与事件由同一 service 按适配器证据更新；command 详情不是第二个状态写入者。采用 spec 的 starting／running／waiting_input／succeeded／failed／cancelled／unknown，command 不产生 waiting_input。停止意图、执行事实、日志状态和通知状态不互相代替。

新增 task、事件、受理记录的 public ID 前缀拟为 `bgt`、`bge`、`cea`，在注册表声明并检测冲突；迁移来的 notice 保留原 ID，不因前缀旧而重发。业务时间一律 tz-aware。

### 1.3 内部接口

以下是实施要共同遵守的 typed 边界，不是逐行实现稿：

- `admit_execution(scope, actor, source_kind, source_id, request_fingerprint) -> admission`：首次受理绑定 generation，并在同一事务持久保存服务端首次解析的执行设置快照；重试读取原绑定和快照。冲突 actor／目标／用户请求摘要不可复用身份。内部事件身份仍由源记录确定，不接受客户端提供内部摘要或执行快照。
- `stop_run(scope, expected_run_id) -> run_stop_receipt`：聊天 Stop，持久停止目标 run 与未交接前台任务，不关闭 generation，不撤销已交接任务；重试只返回原目标。
- `close_execution(scope, expected_generation, reason) -> stop_receipt`：全部停止，关闭目标批次并停止其所有当前工作；删除在同一事务写 deleted_at，不复用聊天 Stop 的范围。
- `reserve_task(scope, admission, task_spec, execution_details) -> reservation`：事务内验证执行权、父任务约束、command cap 和环境身份；提交后才产生远端副作用。
- `handoff_task(task_id, owner_token, tool_result_evidence)`：原子选择前台结果或后台事件，并固化 backgrounded_at；与 run Stop／全部停止串行，不能在已停止 run 上补交接逃过取消。成功交接后不再以发起 run 的单独停止限制 task。
- `record_observation(task_id, owner_token, observation)`：按证据写公共状态、具体结果及需产生的事件，同一事务提交。
- `request_task_stop(scope, task_id, reason) -> stop_receipt`：持久受理，再联系执行适配器；deadline 不等于用户取消通知。
- `ack_notice(notice_id, checkpoint_evidence)`：只有持久输入证明才能 delivered；已提交与取消竞争按真实历史处理。
- `validate_task_wait(scope, admission, task_ids, prior_validation) -> valid | cancelled | invalid(reason)`：查询持久事实；cancelled 仅用于先前有效声明随后被用户取消的自然收尾，不能用来创建新等待声明。不将平台模型传进 CubeLoop。

适配器只暴露真实能力：启动、观察／重连、取结果及可选远端取消等。租约丢失后不可提交新观察；迟到的已知启动句柄仍须通过受约束的登记入口保存并交新 owner 清理，不能因 fencing 丢失它，也不能覆盖新事实。

<a id="result-readiness"></a>

### 1.4 结果是否已经可以交付

这是 spec 第 9.3 节已有的结果可读性规则，在 C1 先固定为公共类型契约，避免 C3/C4 依赖 command 的日志字段。

| 结果就绪状态 | 含义 | 消费规则 |
| --- | --- | --- |
| `pending` | 最终结果仍在收集或恢复 | C3 不 claim／提交该 notice；C4 可交后台并返回 `result_pending`，不当作最终结果已交付 |
| `ready` | 最终结果已可读 | 可按既有通知权限／交接规则交付 |
| `unavailable` | 有可靠证据证明结果无法完整恢复 | 可交付带缺失说明的最终结果，不伪装完整，不重做任务 |

- C1 的适配器观察／公共快照提供 `result_readiness`、结果引用及不可恢复原因；这是结果事实，不是新增执行状态或第二份通知状态机。
- C6 负责 command 日志写入确认和证据映射；临时读取失败仍为 pending，cleanup-only 失败不阻塞 ready。不要求所有类型有日志。
- C3 在 claim／提交前检查同一公共事实；C4 用它判断直接返回最终结果还是后台交接。前台增量 SSE 不等待最终结果 ready。
- 结果就绪不等于通知有权发送；单任务停止／全部停止／删除的通知取消仍优先。恢复只解锁原 notice，不产生第二条事件。
- C1 验证契约，C3/C4 验证消费规则，C6 联调验证“进程已退出、日志晚到／不可恢复”的完整路径；C7 将这一联调作为切换条件。

## C1. 公共 task 与 command 首个实现

### Files

以下 backend 路径以 `backend/cubeplex/` 为根；标“新”的是计划新增。

- `models/background_task.py`、`models/conversation_execution.py`、`repositories/background_task.py`、`repositories/conversation_execution.py`（新）：上述公共持久契约和有 scope 的查询／更新。
- `models/sandbox_command.py`、`repositories/sandbox_command.py`：command 改成类型详情，保存实例身份，退出公共状态／owner 的重复写入。
- `models/conversation.py`、`models/__init__.py`、`models/public_id.py`：generation、模型注册和 ID 前缀。
- `services/background_tasks.py`（新）：登记、交接、观察、停止和事件原子写入；提供第 1.4 节的公共结果就绪契约。
- `sandbox/command_adapter.py`（新）、`sandbox/base.py`、`sandbox/opensandbox.py`、`sandbox/local.py`、`sandbox/manager.py`：具体能力、按原实例重连、进程证据及 cap／保活，不负责模型唤醒。
- `sandbox/command_coordinator.py` 拆出公共调度到 `services/background_task_coordinator.py`（新）；C1 交付协调器，`api/app.py` 中的正式切换由 C7 负责，最终只注册一个 coordinator。
- `backend/config.yaml`、`config.py`／`api/app.py` 的配置加载与启动校验：增加 `default.sandbox.command_default_timeout_seconds: 3600`，沿用环境变量覆盖，不复用 provider HTTP timeout 或 sandbox TTL。
- `backend/alembic/versions/`：模型 metadata 生成结构 migration；数据迁移及最终结构收缩由 C7 交付。

### Core logic

- 固定锁顺序：权限行 → conversation → admission → sandbox → task；reserve、handoff、聊天 Stop、全部停止、删除和 owner 更新不能倒序取锁。短事务完成资格与 cap 检查，provider I/O 在锁外。
- 现有 restart／delete 不能绕过 reservation：C1 识别原 sandbox 的持久清理标记，C4 在现有 manager／HTTP 入口接通；先只锁 sandbox 关闭新启动并提交，再按 conversation → sandbox → task 登记该实例停止，不反向取锁。只补任务清理，不引入通用删除操作；接好前不启用新 coordinator。
- 持久 task／command reservation 和实际 sandbox_instance_id 后才 start；回执携 provider_ref 合并到原实例。UserSandbox 同一行 revive 不改变旧任务的实例绑定。
- coordinator 在原环境观察、取消、保活；观察路径不得隐式创建替代容器。实例不同不等于已证明旧环境销毁；无证据继续 unknown，占 cap。
- 停止采用 observe → 必要时请求取消 → 再观察，not-running 字符串／kill 异常不当作终态；重复停止已终态任务返回原 exit code。
- 15 秒仅是工具前台预算；execute 总期限优先显式 timeout_seconds，否则读取 sandbox.command_default_timeout_seconds，默认 3600 秒，环境变量为 CUBEPLEX_SANDBOX__COMMAND_DEFAULT_TIMEOUT_SECONDS。只接受正整数，0／负数／非整数／null 明确拒绝。受理时固定 deadline，配置变化／交接／接管不重置，background 和 notify=false 不能绕过。monitor 保留既有默认／最大期限及 persistent 的无 deadline 能力，但所有模式均只产生一次最终结果；notify=false 只关 execute 通知。
- terminal＋result＋事件原子提交。只有当前 owner 更新状态；前台崩溃先确认旧 attempt 失权再对账 checkpoint，不能盲目重启或双发 completion。正常失败恢复与已被聊天 Stop 明确取消的未交接任务分开，后者只清理、不补交接。
- monitor 使用等待脚本：条件未满足时保持运行并自行检查，满足后输出摘要并 exit/0，失败非零退出；stdout／stderr 只落日志。deadline 到期持久记录 timed_out 和停止意图，监听未退出则继续清理。matched／failed／timed_out 共用唯一 monitor_result notice 及冻结 outcome，结果与取消在 task 锁内裁决，不重开监听、不追加 line／exit 事件。暂时观察失败保持 unknown；主动取消不自动报告给模型，结果及执行事实仍可查。结束监听不取消被观察任务。
- `config.py` 定义共用 `MAX_COMMAND_TIMEOUT_SECONDS = 2_147_483_647`，配置启动校验、显式 deadline service 和 C4 工具 schema 使用同一上限。超出上限在计算前拒绝；保留日期溢出防御检查，不钳制。默认仍为一小时，合法显式值可更长。测试上限／上限加一及超大整数，配置站点页同 PR 说明技术边界。
- 现有同步 subagent 使用共享工具时保留发起 agent/tool 关联；恢复证明覆盖其父工具中持久的子事件，不能只搜索 root 顶层 ToolResult，也不把当前 child Agent 宣称为持久 detached task。

### Tests / docs

- 新 `backend/tests/unit/test_background_task_state.py`：状态映射、取消能力、deadline 与用户 stop 通知策略，monitor 一次性结果裁决及公共结果就绪映射；纯函数不碰 DB。
- 配置／期限用例覆盖默认 3600、YAML／环境变量覆盖、显式参数优先且可大于 3600、非法值拒绝；e2e 验证后台及 notify=false 同样固化期限，配置变化／worker 重启不改已有 deadline，monitor persistent 与旧无期限数据不被误改。
- 新 `backend/tests/e2e/test_background_tasks.py` 并调整 test_sandbox_commands.py：真实 PG 验证 scope、cap、owner fencing、reservation／handoff／两种 Stop、终态／事件原子性及迟到句柄。
- monitor 多次检查、多行日志与重复输出不产生 wake；成功、非零退出、超时各至多一个结果。结果落库前后崩溃、自然退出与超时／取消双向竞争、日志恢复不增事件；persistent 同样只通知一次，停止监听不杀被观察任务。移除旧“15 秒一次／最多 8 次”的产品断言，不删除日志采集／确认测试。
- 同一 UserSandbox 行由实例 A 换成 B，再运行恢复／kill／日志观察，执行方边界只可收到 A；无历史实例证据保持 unknown，明确销毁才能释放 cap。
- 故障注入位于外层执行方，service／repository 用真实实现。记录恢复期间零重复 start，不用“mock 方法被调用”代替业务结果。
- 更新现有站点 `docs/site/docs/guides/conversations/sandboxes.md` 的受管任务、默认 1 小时及显式覆盖、期限和取消事实；`docs/site/docs/deployment/backend-config.md` 同 PR 补配置键、环境变量、合法值与仅影响新命令的规则，不声称 UI 已完成。

## C2. 停止与受理入口，分四步接通

C2 的边界是准确停止用户选择的对象：聊天 Stop 只停当前 run 和未交接前台执行，独立后台任务保留；全部停止才关闭会话批次。失败／重启不能复活被取消的目标，新的合法独立工作仍可继续。它不是账号删除、共享资源或全站授权重构。

先看 spec [5.2 的按钮行为](../specs/2026-09-20-command-lifecycle-design.md#stop-actions)。实施按下表拆成可分别验收的提交／PR；它们复用同一 admission／控制服务，不各建一套状态机。小节通过不代表可以单独启用，完整链路仍由 C7 统一切换。

| 小节 | 要交付什么 | 通过标准 | Spec 依据 |
| --- | --- | --- | --- |
| [C2a 停止与恢复](#c2-controls) | 指定 run 停止、批次关闭及崩溃恢复 | 不误停后台，不因重试复活或取消后来 run | [A.1–A.3](../specs/2026-09-20-command-lifecycle-design.md#stop-current-run) |
| [C2b 消息入口](#c2-inputs) | Web／IM／steering 受理、幂等与执行身份 | 相同请求只执行一次，不借另一参与者凭据 | [A.4](../specs/2026-09-20-command-lifecycle-design.md#request-admission)、[A.7](../specs/2026-09-20-command-lifecycle-design.md#execution-identity) |
| [C2c 自动来源](#c2-sources) | schedule／trigger 固定来源、排队与恢复 | 旧来源不越过全部停止，崩溃不重复派发 | [A.5](../specs/2026-09-20-command-lifecycle-design.md#automatic-sources) |
| [C2d 最小清理挂接](#c2-cleanup) | 现有删除／撤权入口清理相关受管工作 | 句柄和证明不丢，不误停其他 actor | [A.6](../specs/2026-09-20-command-lifecycle-design.md#delete-and-revoke) |

C2a 先用 C1／#636 的已有持久受理基础验证控制；C2b 再接通用户入口，C2c 接自动来源，C2d 接清理入口。所需新增字段由对应小节通过 `backend/alembic/versions/` autogenerate；不新增全站 access_authority、deletion_operation 或登录隔离表。

### C2 与 C5 的接口分工

| 内容 | 后端交付 | 前端／读取接入 |
| --- | --- | --- |
| `POST /cancel`、`POST /stop-all` | C2a：schema、handler、持久控制和目标回执 | C5：typed 调用、按钮与停止进度 |
| 消息 `client_message_id`、重试及 HITL 身份检查 | C2b：受理入口与错误契约 | C5：稳定 ID、重试、恢复编辑及非原 actor 提示 |
| 任务／事件查询、单任务 Stop、bootstrap | C5：API、完整 summary 与历史投影；复用 C1/C2/C3 服务 | C5：任务区、结果行与刷新恢复 |
| 现有删除入口的 `cleanup_pending`／失败 | C2d：清理挂接和既有响应 | C2d：对应现有删除 UI，不重做会话界面 |

C5 不重复实现 C2 的消息／停止 handler，C2 也不提前建设 C5 的任务界面。中间 PR 不上线，因此不为前后端分步合入添加兼容入口。

<a id="c2-controls"></a>

### C2a. 停止目标 run／全部停止，并能恢复清理

#### 文件与交付

- `services/conversation_execution.py`、C1 的 admission model／repository、`middleware/execution_authority.py`：统一受理、目标 run 停止、批次关闭及原执行资格检查；在 ConversationExecutionAdmission 上增加 run_stop_requested_at，与 admission 的整体失权／撤销分开；复用原 run 启动回执的权威关联，不另建 run 状态表。
- `streams/run_manager.py`、`run_events.py`、`hitl_resume.py`、`recovery.py`：run 关联 admission、启动／结束回执、模型／工具／恢复边界校验和有界收尾；恢复不能只依赖 Redis active key。
- `api/routes/v1/conversations.py`、`api/schemas/conversations.py`：`cancel(run_id)` 与 `stop-all(execution_generation)` 的后端 handler、校验和停止回执；两者不共用请求范围。

#### 核心规则

- `cancel` 请求必须携目标 run_id，在该 run 的持久启动 admission 记录 run_stop_requested_at 后关闭该 run 的新执行、HITL resume、用户追加输入和后处理；不关闭 conversation generation，也不整体撤销发起 admission 下已交接 task 的资格。支持 preparing／running／持久 paused HITL，不仅查询 Redis；目标已终态不改原结果，但仍停止其未完成清理／后处理；无剩余工作返回原事实，旧请求不能取消后来 run。成功受理返回 202 `{run_id, accepted, cleanup_pending}`，进度只统计目标 run 及未交接任务。
- 聊天 Stop 与 reservation／handoff 按同一锁序裁决：Stop 先提交则拒绝新启动及 handoff，目标 run 下 backgrounded_at 为空的 task 继承停止，迟到句柄保存并清理；handoff 先提交则该 task 独立继续，包括显式 background 和 monitor。同步 child 以所属前台 run 关联判断，不另建业务任务分组。模型／前台工具／记忆写入检查 run 停止，已交接 task 和结果投递只检查其自身／父 task、generation 与原 actor／来源撤销，不以发起 run 结束或被单独停止为拒绝理由。
- 只撤销属于目标 run 的未提交用户 steering，保留恢复编辑能力及其他独立队列。首条后台 notice 的处理被聊天 Stop 取消时按 checkpoint 确认 delivered 或在旧 attempt 确定失权后 discarded，不新建 run 重放它；源结果仍可查。其他独立追加 notice 未提交时先对账并解除旧 attempt 绑定，再重新路由，已提交不重投；细节由 C3 实现。不增加 monitor 的通知暂停开关。
- 独立 `stop-all` 请求携 execution_generation，首次受理与批次关闭使用同一 conversation 锁。关闭批次、撤销相关 admission、登记所有 task 停止及未提交输入取消，持久成功返回 202 `{execution_generation, accepted, cleanup_pending}`；覆盖无 active run、starting、HITL、只剩后台任务或待发结果。之后的新用户消息／独立新 occurrence 可开新批次，旧来源重试不可；旧 Stop-all 不误停新批次。
- 两者均提交后才取消 RunManager／HITL／输入及联系执行方，provider I/O 在锁外。恢复从持久 run 停止、关闭批次、未完成 admission 和 task 停止意图核对，不只扫描 Redis。终态提交失败、控制丢失、owner 到期和远端不可达可有界重试；无可靠证据显示未确认。恢复只清理被取消的目标，不重做模型／命令、不误停正常后台任务，也不另建通用删除 worker。

#### 验收

- `backend/tests/e2e/test_conversation_execution_control.py`：真实 DB／Redis／应用验证按 run_id 的聊天 Stop 与按 generation 的全部停止、持久失败、旧控制请求不误停新目标、preparing／HITL 恢复。双向 barrier 覆盖 reservation／handoff：聊天 Stop 不漏前台迟到句柄，也不取消先完成后台交接的任务。
- run／HITL 清理在终态提交前失败及重启后恢复；终态后立即发送、连续 HITL、结束响应丢失和租约到期不丢结束证明，不重新调用模型。
- 后台 notice 的逐项对账由 C3 实现；本节先固定 run／批次控制边界，C3 联调时再验证首条及追加 notice 的不同处理。

<a id="c2-inputs"></a>

### C2b. 接通用户消息、追加输入与 HITL

#### 文件与交付

- `api/routes/v1/conversations.py`、`api/schemas/conversations.py`、conversation repository：Web 消息身份、设置快照及 install 快捷操作的持久结果／checkpoint 幂等追加，调用 C2a 共用的 admission 服务。
- `models/steering_message.py`、对应 repository、`streams/steering_delivery.py` 及 IM 用户入口：稳定来源、actor／generation 绑定、不同 actor 排队及未提交输入取消。
- `repositories/attachment.py`、`services/attachments.py`：附件保护进入受理事务，与 orphan 清理串行；不改变共享附件归属或 uploader 策略。

#### 核心规则

- 来源由服务端固定为 `user_message | schedule_occurrence | trigger_occurrence | background_task`；source ID 分别取 Web client_message_id／steer_id／IM receipt、既有 occurrence／event ID 和 notice ID。用户入口使用独立名称空间，不能由客户端自报内部来源或 generation。首次受理在排队或取得 active slot 前持久绑定目标、原 actor 和批次；同一来源的重试不取得新授权。
- 用户受理保存规范化请求摘要（正文、有序附件、请求的 model_key／reasoning 等）和首次解析的非凭据执行设置快照；相同 ID 的内容、actor 或目标不同则冲突。会话选择更新、附件鉴权与保护、admission 创建在同一事务，repository 只 flush。重试只读取原快照，不覆盖后来的会话选择；需要实际执行时仍检查当前权限／模型／凭据，不能因配置变化静默换模型。只返回原已启动／结束的回执不要求旧模型仍可用。
- run ID 在调用 RunManager 前预分配并与 admission 同事务绑定。启动意图、真正取得执行权、正常结束各有可对账证明；启动前崩溃与已启动但响应丢失分开处理。不能仅凭 Redis slot 消失换 ID 重跑，也不能用同一 ID 重做已完成模型／工具；无法证明原 attempt 已失权及未执行时保持待对账。
- install 快捷操作首次受理固定分支，安装变更与结果回执同事务，合成用户／助手消息有稳定 ID；响应丢失只补缺失的 checkpoint 消息并重放原结果，不重复安装或转成模型 run。全部停止／删除阻止尚未执行的副作用，不伪造对已完成安装的回滚。
- 用户 steering 在 HTTP、DB 领取、准备缓冲、pub/sub 和最终 Session 提交均检查原 actor；B 不能进入 A 的 run 借用其凭据。B 的原输入保持排队，slot 释放后以 B 当前权限执行一次。用户撤回、停止及提交在途沿用 checkpoint 对账；已提交但处理被中断的输入保留历史，不自动重放业务副作用。HITL 回答／审批仅接受原 actor；聊天 Stop／全部停止均沿用会话控制权限，不通过模型回答实现。

#### 验收

- 用户／IM／steering 相同来源重试不重复执行，改正文／附件／模型设置冲突；admission 提交后崩溃不丢首次快照，启动响应丢失且 Redis slot 消失仍返回原 run。install 事务／checkpoint 提交后断开也不重复副作用。
- 附件受理与 orphan 清理双向竞争、事务回滚、后台读取会话选择和旧重试不覆盖新选择；只保护本次受理，不测试共享 uploader／归属迁移。
- Web／IM／内部投递的跨 actor 排队、原 actor HITL、后台与用户输入竞争均验证；模型／工具不使用其他参与者凭据，已提交历史不被当成可安全重放的工作。

<a id="c2-sources"></a>

### C2c. 接通 schedule／trigger／IM 自动来源

#### 文件与交付

- `schedules/{poller,dispatch}.py`、`triggers/{ingest,pipeline}.py`、源记录 model／repository、`im/{worker,run_handoff}.py`：首次排队绑定原来源和不可变内容／actor，重试和交接读取同一受理。
- `triggers/worker.py`（新）、`api/app.py`：已持久受理 trigger 的有界领取、重试和启动恢复；不新增调度引擎。
- schedule／trigger／IM 来源删除使用本节的来源取消和交接对账，不与 C2d 重复实现。

#### 核心规则

- schedule／trigger 首次持久领取时冻结 prompt、目标策略、模型选择和 actor，尚未确定 conversation 时先随源记录保存；确定后在首次排队前绑定 admission。编辑只影响新的 occurrence／event，busy／IM 重试不重新渲染或替换身份。固定目标的新 occurrence 可独立开启新批次；旧 occurrence／notice 不能借重试越过全部停止，当前停用／权限／目标检查保留。
- trigger 的 accepted 必须对应已校验且持久可消费的事件，审计／去重行不等于执行授权。worker 使用有期限 claim 和持久退避，启动及运行中有界扫描；创建 conversation 与源目标绑定同事务，崩溃接管不重复建目标或执行。领取先释放源锁，再按权限／conversation → 源定义／事件的顺序受理；不改变原过滤、限流、调度或 missed／busy 策略。
- schedule／trigger／IM 来源删除与领取／交接串行，取消未开始的工作，保留未决的 admission、来源键及 handoff 证明供对账；停用后的 worker 不得继续新建目标或执行。已确认启动的 run 保留历史，不误停同会话其他工作，不把队列交接当作已开始执行。这里不引入通用来源删除回执或重做 IM 管理 UI。

#### 验收

- 自动来源编辑、busy／IM 重试、trigger 多 worker 领取／过期接管、目标创建后崩溃、交接未决与来源删除：原身份和批次不变，不重复创建目标、启动或越过全部停止。
- 本节只补生命周期需要的受理／恢复边界，不改变原调度计划、missed／busy 策略或新建通用调度引擎。

<a id="c2-cleanup"></a>

### C2d. 把现有删除／撤权接到任务清理

#### 文件与交付

- `api/routes/v1/conversations.py`、conversation repository，以及现有 `auth.py`、`workspaces.py`、成员管理、`ws_topics.py`、`ws_im.py`：只挂接删除／撤权相关 admission／task 的停止、取消、对账和新增 FK 清理，不改变资源归属、成员接任或凭据政策。
- 对应现有删除 UI：识别 `cleanup_pending`／失败，保留原入口重试；不新增删除凭证或状态页面。自动来源的删除／停用规则复用 C2c。

#### 核心规则

- 会话 soft-delete 与关闭批次、停止／通知取消同事务；topic 归档对所属会话使用同一入口。普通查询隐藏后，恢复仍可按原 scope／实例清理已受理工作；不修改原资源归属和 owner 接任规则。
- workspace／账号硬删前，停止实际删除范围内的原 run／task 并取消其未提交输入／通知。账号按 actor 定位，不关闭其他参与者的整个共享批次；仅处理本次新增生命周期依赖。清理未确认就保留句柄和恢复证明、返回 cleanup_pending，原入口可重试；coordinator 继续清理任务，不负责自动完成账号或 workspace 硬删。最终删除在现有权限／目标锁下复查新受理；仍有工作则回到清理，确认后按 event → command 详情 → task（后代先于父）→ admission 清理新增 FK，再交回原删除逻辑。
- 成员／参与者移除按原访问规则，只持久撤销失去资格的 admission 并停止相关工作，仍有会话访问来源的 actor 和其他 scope 不受影响。保留当前权限查询与执行／写入事务校验，重新加入不恢复已撤销的 admission；不新增全站 grant 版本、账号状态机或私有资源转移。已有删除／撤权若遇到与 lifecycle 无关的数据政策或 FK 问题，保持原错误／限制，不在这里自动迁移、增权或级联扩大删除。
- 清理挂接与新受理遵守权限行 → conversation → sandbox → task 的固定锁序；源领取事务先释放源锁再进入这个顺序。失败不能提前删本次新增记录或未决来源证明。相关现有 UI 识别 cleanup_pending／失败，不以任意 2xx、401、404 或断网当删除完成；不提供跨硬删除的独立查询凭证或自动删除编排。

#### 验收

- 会话删除、topic 归档、workspace／账号最终删除及成员移除只验证任务清理挂接：迟到句柄不丢、重启能继续停止、未知不硬删、新生命周期 FK 不阻塞已完成清理、其他 actor／scope 不误停。不要求共享资源转移、全站登录撤权、账单或删除回执系统。

<a id="c2-regressions"></a>

### C2 保留回归：已有 run 收尾与记忆保护

这些是 #636 已有保护，不作为新增记忆功能重做；只接入新的 run Stop 判定并保留回归，未完成的停止恢复仍归 C2a。

- `repositories/memory.py`、`services/memory_consolidation.py` 与 RunManager：正常结束后的 reflection／consolidation 继续受原 admission 和 Stop 约束，整次记忆修改同事务。
- reflection／consolidation 不保持 live run 或 active slot，先等原 attempt 的正常完成和持久结束回执，再检查原 actor、generation、run_stop_requested_at。目标 run 的聊天 Stop、全部停止、失败、HITL 暂停或证明丢失禁止后处理；取消其他 run 不误伤。模型／工具及写事务重复校验，C3 的后台来源过滤另行叠加。
- 记忆写事务按权限行（含 topic 归档和 topic／conversation participant）→ conversation → admission → memory 持锁复查，整次 save／update 或整批 extract／merge／archive、去重及容量清理只统一提交，仓库只 flush。Stop 先提交则拒绝迟到写入；记忆先持锁则先完成再让 Stop 提交。失败全部回滚，不持锁等待模型，不自动重放旧执行，不丢 source_run_id。
- 终态已对外可见时，原 worker 仍持有有界收尾租约，直到结束回执与 slot 释放完成；快速下一次发送不能夺走收尾权。HITL 暂停及时释放租约，崩溃到期仍能接管。此项保留 #636 的既有保护，不扩展为全站异步副作用治理。

- 保留 `test_admitted_run_execution.py`、`test_admitted_hitl_execution.py`、`test_admitted_reflection_execution.py`、`test_admitted_consolidation_execution.py` 的回归；真实 PG barrier 覆盖 Stop／topic 权限变化与整批记忆提交两个顺序，失败不部分提交。

### C2 文档交付

现有 basics.md、sandboxes.md、attachments.md、using-memory.md 随对应代码更新实际行为；C5 再补会话界面操作与截图。不扩写全站账号／SSO／成本管理文档，也不在 C2 宣称 C5 界面已经完成。

## C3. 每条 notice 独立投递与确认

### Files

- `services/background_task_delivery.py`（新）、C1 的 task/event repository：从现 command coordinator 提取现有 wake 路由，不新增第二条消息管线。
- `streams/steering_delivery.py`、`streams/execution_adapter.py`、`streams/run_manager.py`：输入来源、稳定 delivery ID、checkpoint ack、取消与 attempt fencing。
- `models/steering_message.py`、`repositories/steering_message.py`、`agents/schemas.py`：持久 source／notice／generation，后台事件与用户 steering 的实时投影分流。
- `api/routes/v1/conversations.py` 的 bootstrap／历史读取：旧内部输入关联源 notice 归类，不回写 checkpoint。
- `streams/run_manager.py` 的 reflection／consolidation 调度、`services/reflection_runner.py` 与 `services/memory_consolidation.py` 的来源契约：含后台 notice 的自动／混合轮次跳过 reflection；consolidation 在历史窗口裁剪与 `_render_history` 前过滤这些轮次及其回复，正常纯用户历史不变。

### Interfaces / core logic

投递先读取 C1 的[公共结果就绪契约](#result-readiness)：pending 不 claim／提交，ready 才交付可读结果，unavailable 交付明确不完整的结果。通知权限仍独立校验；不直接读取 command 日志字段来判断。C6 提供 command 的事实映射，联调验收必须包含日志晚到和不可恢复两条路径。

`BackgroundTaskNotice {source: background_task, notice_id, task_id, task_kind, originating_run_id, execution_generation, reason, summary, result_ref}`。command 详情可选；权限主体来自持久发起身份，不从消息角色推断。每条 notice 单独映射初始输入或 InputEnvelope，不跨 notice 拼接一条可撤回输入。

有 active run 且其 RunContext／持久 admission 的 actor 与源 task.started_by_user_id 相同，才在安全输入边界接收；其他 actor 的 run 或身份无法可靠对账时保持 pending，不能仅因 conversation／generation 相同就借用其凭据。准备／收尾不能接收时保留 pending；无 active run 且没有持久 HITL 才走 C2 的旧工作受理校验和现有 active-slot claim，并以源 actor 构建 RunContext。同一调度的多个同 actor 就绪 notice 可进入同一 run；不保证一次模型调用，buffer 满时保留未投递项，不丢弃也不忙等。

只凭 InputCommitted(checkpoint) 或同 notice ID 的持久历史证明确认 delivered。初始消息不能因 start_run 返回成功就 ACK。提交证明已存在时不因后续模型失败再次投递；事件显示“已送达”而非“业务已完成”。

A/B 独立输入时停止 A 的 task 只撤回 A，B 不变；取消返回 closed、响应丢失或 checkpoint 在途先对账。聊天 Stop 只停止目标 run：已提交 notice 不重投，未提交首条 notice 在旧 attempt 失权后 discarded，其他独立追加 notice 可在对账后重新路由。单任务停止／全部停止／删除才取消相应源通知，deadline 仍可报告超时；源通知已取消时只保存迟到事实。一次性 monitor 无后续订阅，不另加暂停来源规则。

初始 notice 不在 Session 的 cancel_input 队列中，需单独的宿主路径：投递记录绑定首条 notice／attempt；准备阶段及 Session 执行前检查源 task 的通知权限。初始输入持久提交未决时，其他 notice 和用户追加输入留在各自持久队列，不向 Session 提交。初始提交确认后开放其他输入，与首条取消裁决在同一 attempt 绑定上串行：取消先获权则保持入口关闭、取消 attempt 并对账 A，B 仍 pending 可重路由；提交确认先开放入口则单 task Stop 不再取消这个共享 attempt，避免中断已提交 B。已提交 A 保留历史，未提交且 attempt 已失权才 discard；聊天 Stop 可停目标 attempt 而不关闭批次；全部停止才关闭整个批次。复用 RunManager 准备任务取消与既有 attempt fencing，不扩充 CubeLoop Session API，也不引入 delivered 通知的自动业务重试。

`pending_steers` 和用户 steering SSE 只返回 source=user。后台输出作为有来源的任务数据，不作用户指令或审批答案；不改稳定系统 prompt、不补旧 ToolResult、不修改历史 metadata。

该来源规则贯穿副作用消费者：首条或本轮已提交输入含 background_task 时跳过自动个人记忆 reflection，不把命令日志当作用户偏好／纠正；本轮先保守跳过整个混合轮次，不建设新记忆系统。consolidation 不能只在当前 run 的调度点跳过：以后由普通用户 run 触发时，仍须先按持久输入来源及 run 关联排除含后台 notice 的完整轮次，再裁剪 HISTORY_MSG_CAP 和转成文本，避免留下孤立的后台回复。旧输入使用可靠 wake 关联，无法可靠分类的片段不用于自动记忆，不回写 checkpoint。全部被排除时不调用提炼模型、不写个人／workspace memory，仍按已有 cutoff／consumed 高水位规则结束扫描，不能重置或消费扫描期间新增 run 的计数。

### Tests / docs

- 新 `backend/tests/e2e/test_background_task_delivery.py`：真实 Session／Postgres／Redis，active／idle／preparing／finishing／HITL 路由，start_run 响应丢失、worker 重启、claim 接管。
- A、B 同 run 排队 → Stop A 与 InputCommitted 竞争 → B 恰有一份持久输入；A 已提交保留历史、未提交不续办，closed 不被当成取消成功。
- 首条 A 已 claim、run 尚在准备 → B 保留独立投递 → Stop A：用 barrier 验证 A 不进入模型且 B 最终只提交一次；另覆盖初始 checkpoint 在途、已提交、取消响应丢失和 worker 崩溃后的逐 notice 对账。
- A 初始提交／取消裁决双向 barrier：其他输入在未决期间不能提交；B 已提交后 Stop A 不结束共享 attempt，模型可继续处理 B。纯后台与用户＋后台混合轮次均不调度自动个人记忆 reflection，普通用户轮次仍正常。
- 真实 checkpoint 历史含纯用户、后台、混合轮次及窗口边界的后台回复：后续普通用户 run 触发 consolidation 时，自动结果及其回复不进入提炼输入；个人／workspace memory 不受其影响，纯用户历史正常合并。全部被过滤时不调用模型；用并发 barrier 验证只扣除本次已扫描计数，后到 run 仍能触发后续合并；失败重试沿用相同过滤。
- 已关闭 generation 的 completion／monitor 最终结果／调度重试不复活；多个投递 owner 不重复确认，权限失效 discarded。聊天 Stop 不撤销其他独立 task 的通知权；发起 run 已被停止但先完成后台交接的任务仍能交付结果，不借 system actor 绕过权限。
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
- `backend/cubeplex/api/routes/v1/ws_sandboxes.py`、`api/schemas/ws_sandbox.py`、`sandbox/manager.py`、UserSandbox model／repository 和 C1 task service：现有 restart／delete 只补原实例关闭、新启动阻断、任务停止和迟到句柄核对；必要字段经 autogenerate，不引入通用删除回执。HTTP handlers 保持 scope 分开。
- `frontend/packages/web/hooks/useMySandboxes.ts`、`components/workspace-settings/sandboxes/SandboxCard.tsx`、core 的 `MySandboxOut` 类型、en／zh 及 sandbox 站点页：现有管理入口准确展示原实例影响、失败与清理未确认，不将请求成功当作进程已停止；不新增 token／进度页。
- 通过 `uv add` 更新已发布的 CubeLoop 依赖与 `uv.lock`；不手改 pyproject 或 .venv。

### Core logic / tests

前台／后台选择使用 C1 的[公共结果就绪契约](#result-readiness)，C6 负责 command 证据；工具层不另建一份日志就绪判断。

前台最多等待 15 秒，已交付最终可读结果不发 completion；达到交互预算后持久交接再返回，原 run 给进度后 Done。进程终态但输出仍在恢复也可交后台，返回真实状态与 result_pending。聊天 Stop 与交接共用 C2 的 run 停止检查及锁序：交接前停止前台，交接后保留后台；正常结束／非用户失败不撤销已受理任务。finalize 不能按 originating_run_id 扫杀已交接 task；前台 owner 崩溃按 C1 对账，不复活旧 Session。

monitor 工具及静态说明统一改为一次性条件等待：脚本自行检查至满足后 exit/0，失败非零退出，输出只作日志；persistent 仅解除等待 deadline，不开启多次通知。工具返回 task ID、真实状态、deadline 和唯一最终通知约定，移除 line wake 订阅语义。execute 的 on_update／ToolExecutionUpdateEvent／SSE tool_result 增量路径与日志采集保留，不依赖 monitor_result 投递。

sandbox restart／delete 沿用现有权限与资源政策，只为受管任务补清理门槛：先持久绑定 UserSandbox 的原实例并关闭 reservation／revive／保活，提交后按 conversation → sandbox → task 顺序登记该实例任务停止和通知取消。已在途的 start 保存原句柄并继承停止，不把缺少 sandbox_id 或 provider 错误当销毁证据，不在未知时替换实例或丢弃记录。coordinator 只恢复原任务的观察／取消，不自动重发管理操作或创建新环境；管理请求走原入口重试。可靠退出／销毁证据才表示清理完成，现有 UI 展示失败／未确认，保留 PVC；不新增操作 token、teardown-status API 或父子删除编排。

wait_for_tasks 只接受本 conversation／有效批次、后台可观察、通知仍有效且有待交付结果的公共 task ID。notify=false server、失去恢复来源、用户取消的任务不能建立新的等待许可；deadline 待报告结果可以。先前有效声明随后被取消走 R 的 cancelled 收尾，不冒充仍有结果会来。工具 schema 和静态说明统一发布，动态 task 状态不改 prompt 前缀。

- 新 `backend/tests/e2e/test_background_task_run_flow.py`：脚本化最外层模型＋真实宿主，构建运行 → 保留 Todo → 原 run Done／slot 释放 → 模拟小时推进及 coordinator 重启 → 结果入新 run → 验证业务结果。
- 等待期没有输入／事件则模型调用数不增长；current run 可接收通知；用户新输入使旧等待失效；prompt 与 respond 均正确恢复 extra。
- 前台终态结果与交接只交付一次；聊天 Stop 在 handoff 两侧的双向 barrier 验证范围，同步 child 未交接命令随前台停止，已交接命令不被 finalize 误杀。背景 task A 运行时停止另一个回复 B，A 完成仍只通知一次。
- monitor 脚本多次检查不调用模型，满足／失败／超时各一个最终事件；回复被聊天 Stop 打断不重投已提交结果，未提交首条取消后不重开 run。保留前台 execute 多 chunk 在终态前更新的真实 SSE 契约，确认无 monitor 记录或 wake 也有流式输出。
- 现有 restart／delete 与 reservation 提交、provider start、迟到回执的双向 barrier：关闭门槛先提交则不开始，reservation 先提交则继承停止；provider 失败、可靠销毁证明、重启恢复和同一行后续新实例均验证，旧任务不能作用于新实例。共享实例范围明确，其他实例不受影响，cap 不提前释放。
- sandbox 现有 UI 的失败／未确认／完成展示与真实清理事实一致，重试和刷新不伪报成功；不新增独立删除状态机，用业务流而非按钮存在验证。
- 单 task Stop 与自然收尾复查的双向 barrier：先前有效的等待通过 cancelled 收尾，不增加模型调用、不完成 Todo、不杀共享 run 的其他输入／同级任务。Stop 后才声明的任务、无效 task ID、新输入失效及查询错误均不能得到这项许可。
- 运行现有 prompt-cache、唯一 Done、required consumer 故障和 HITL 回归；`real_llm` nightly 另检查模型不主动 ps/sleep 忙等，确定性测试不冒充真实模型行为。
- 更新站点 sandbox 页的“等待时本轮已结束、结果自动续办”，明确不等于 Todo 已成功完成。

## C5. 任务 API、会话界面与刷新恢复

本单元交付任务／事件 API、bootstrap 和前端接入；消息受理、聊天 Stop／全部停止的后端 handler 由 C2 交付。下表保留客户端需要的完整契约，并明确后端负责人，不重复实现控制逻辑。

### Files

- `backend/cubeplex/api/routes/v1/background_tasks.py`、`api/schemas/background_tasks.py`（新）、`api/routes/v1/__init__.py`、`api/app.py`：workspace conversation 路由；移除旧 sandbox-commands 控制路由及调用方。
- `backend/cubeplex/api/routes/v1/conversations.py`、`api/schemas/conversations.py`、公共 task repository：权威 summary、事件分页和历史分类。
- `frontend/packages/core/src/api/backgroundTasks.ts`、`types/background-task.ts`（新），`api/runStreams.ts`、`types/events.ts`、`stores/messageStore.ts`：typed API、source、独立任务／事件状态和后台刷新。
- `frontend/packages/web/components/layout/InputBar.tsx`、`PendingSteers.tsx`、`components/chat/MessageList.tsx`、`TaskProgressCard.tsx`、`components/panel/sandbox/SandboxTerminalView.tsx`：Stop 可用性、任务区域与紧凑结果行，复用现有详情组件。
- `frontend/packages/web/messages/en.json`、`zh.json`；站点 `guides/conversations/sandboxes.md` 和 `basics.md` 同 PR 更新。

### API 契约

路径前缀均为 `/api/v1/ws/{workspace_id}/conversations/{conversation_id}`。

| 请求 | 后端负责单元 | 结果／规则 |
| --- | --- | --- |
| GET `/background-tasks` | C5 | 默认 inflight；可按有界 task IDs 查询终态快照，不触发 provider I/O |
| GET `/background-tasks/{task_id}` | C5 | task 公共字段、类型详情、能力、通知／停止状态和 revision |
| POST `/background-tasks/{task_id}/stop` | C5 | 202 持久受理但未确认；200 已终态原事实；都执行通知取消。响应区分本地受理、远端可取消与执行确认 |
| GET `/background-task-events` | C5 | `delivery=pending\|all`，默认 pending；cursor 分页，limit 默认 50／上限 100；返回 items／next_cursor／has_more |
| 现有 bootstrap | C5 | execution_generation 与全部停止进度；现有 run_id／HITL 控制及该 run 停止进度；background_summary 和事件首页分别返回 |
| POST `/cancel` | C2a | 必填 run_id，仅停止该 run／未交接前台任务；202 为持久受理，终态／重试返回原目标事实 |
| POST `/stop-all` | C2a | 必填 execution_generation，关闭当前批次并停止其全部工作；202 区分受理和清理确认 |
| 现有消息请求 | C2b | 稳定 client_message_id；前后端同步，不接受缺字段绕过幂等受理 |

pending 与 has_pending 仅计算 state ∈ {pending, claimed}（包括这些状态下等待重试／对账的事件）；明确排除 delivered 和 discarded，二者只进入 delivery=all／历史。分页按不可变 created_at、notice_id。cursor 绑定 scope／conversation／过滤条件，拒绝跨上下文复用。summary 在同一 DB 读取快照中从完整可访问集合计算；当前页之外的待处理也计入，查询错误不能回 false。has_cleanup 包括停止未确认和日志收尾；can_stop 只表达后台工作仍可撤销的权限，不保存第二份 run 状态。Redis active-run 另按现有协议对账，不假装与 DB 原子。

### UI / core logic

- 用户追加输入仍在 steering 列表，后台 task 每项一条，结果按 notice ID 紧凑显示；同 notice 的历史输入与源事件合并，不能再生成用户气泡。
- Terminal 保留 command 详情，普通公共列表不要求 exit code／sandbox 字段。终态但通知 pending 可从“待处理”分页进入 task 详情并停止，不只靠 inflight 列表或历史 tool result。
- 每次冷刷新先 bootstrap，summary 任一 has_* 为真就保持有界低频刷新。输入框 Stop 只依据当前 run／持久 HITL，任务区的全部停止结合 run 状态与后台 can_stop；不能因后台仍在就显示模型仍思考。空页／失败／旧响应不清掉已知 pending，切换会话隔离请求和 scope。
- 可见会话即使 summary 全 false，仍保留每 30 秒一次的 bootstrap 基线发现；不能因没有当前 task 就永久停掉发现。隐藏页面暂停基线，重新可见立即 bootstrap；同一会话最多一个在途请求，失败指数退避至最多 120 秒。有待处理后台工作时沿用更及时的有界刷新。这样未来 schedule／trigger 新触发可被发现，包括两次查询之间已完成的 run。
- 原 SSE 已结束时仍发现后续 run；其在两次检查间快速结束也能从历史显示。summary 全清且最终历史／active-run 对账完成后才停止后台轮询；页隐藏降频，回到页面重建。
- 输入框明确“停止当前执行”，任务区明确“全部停止”；前者携 run_id，后者携 generation。受理前不移除卡片，202 按所选目标显示等待确认，聊天 Stop 不等待无关后台结束；必要时提示“当前执行已停止，后台任务仍在运行”。未知／取消不支持如实显示，不删除结果、不把 Todo 勾成功；折叠不发取消请求。
- 每条通知独立；新 monitor 一条任务记录、至多一个最终事件，stdout／stderr 只在日志／详情显示。前台 execute 仍按同一 tool_call_id 更新流式结果卡，不依赖 monitor 的事件数量。日志按原始文本处理，不作可信 HTML／Markdown 指令。

### Tests / docs

- 新 `backend/tests/e2e/test_background_task_api.py`：scope／actor、只读性、typed 200/202、全部 task 终态但 pending 超过两页、cursor 隔离、summary 与记录一致。
- 新 `frontend/packages/web/__tests__/e2e/background-tasks.spec.ts`：后台任务 A 持续运行 → 回复 B／execute 增量输出 → 聊天 Stop B → A 仍完成并只通知一次；另测单任务停止、全部停止后旧结果不续办、旧 run Stop 不影响新 run、无 active run 时后台控制可用。monitor 多段日志不新增通知卡，通知处理被中断不重放；冷刷新与重试保持用户 steering 和后台事件分离。
- 超过窗口的 pending 可分页发现和取消；查询失败不当无事可做；所有 task 终态仍能发现自动回复。A 停止后 B 继续，不以元素计数当验收。
- 扩展既有 steering／messageStore 业务流测试，保留 SSE ownership、窄屏、键盘、i18n 和主题。先构建 `@cubeplex/core` 再验证 web，不改压缩／CSRF／代理规则。
- 截图若暂缺，在匹配站点页面留明确 placeholder，不省略用户文档。

## C6. 命令日志确认独立于进程状态

消费 C1 已固定的[结果就绪契约](#result-readiness)，负责 command 的日志证据与恢复实现；不等到本单元才决定 C3 能否投递、C4 能否交付最终结果。完成时与 C3/C4/C5 联调，作为 C7 的必要前置。

### Files / interface

- `backend/cubeplex/sandbox/log_io.py`（新）、`base.py`、`opensandbox.py`、`local.py`：poll 返回候选 cursor，日志写入确认后才 ack；限定目录下区分 write 与 cleanup。
- C1 的 command adapter／task service：原实例绑定、fenced cursor、终态后的日志重试；coordinator 支持 terminal＋log retrying，不依赖 inflight 查询。
- `append_output(path, data) -> {data_written, cleanup_done}`；write 失败不 ack，cleanup-only 失败不重放已确认输出。

### Core logic / tests

固定内部目录以运行用户可写权限准备；拒绝 symlink／非目录，不递归 chown 工作区、不以 root 跟随代理可控路径。只清理本次明确生成的临时片段，不删除现场 orphan。

执行可先终态，completion／monitor 最终结果 源事件原子保存为 pending，但依赖尾部输出的事件在最终日志可读前不允许 claim／提交给模型。公共投递层读取适配器提供的结果就绪事实，不要求所有 task 都有日志。保留句柄与旧 cursor，在原实例继续收集；可靠不可恢复时标为 unavailable 并交付明确不完整的最终结果，临时错误继续重试。cleanup-only 失败不阻挡可读结果。日志恢复只解锁原事件，不生成第二次 completion；单任务停止／全部停止／删除后的通知取消仍优先；聊天 Stop 不吞独立后台结果。写入成功而 DB cursor 未提交可重复片段，不为去重跳过未知数据。

- unit 覆盖 write／cleanup 分类和路径校验；e2e 用真实 DB 加执行方外边界故障验证进程终态、重启后继续收集、cursor fencing。
- 进程退出、日志写入失败、原 run 结束、coordinator 重启后日志才成功：恢复前模型不消费 completion，恢复后只有一次通知且能读到完整输出。另测不可恢复的明确不完整结果、cleanup-only 不阻挡、日志恢复与单任务停止／全部停止竞争不唤醒，聊天 Stop 后独立任务仍可报告；前台 15 秒内终态但日志未就绪同样交后台。
- 真实 OpenSandbox 用唯一输出片段检查普通用户写入／清理和 symlink 拒绝；外部服务不具备条件时具名 skip，不用假服务冒充 E2E。
- 同 PR 更新 sandbox 用户文档中的日志不完整状态。AsyncSession 用例只放 e2e，现有误分类测试在实质改写时迁入正确目录。

<a id="c7-cutover"></a>

## C7. 数据回填、部署入口门槛与统一切换准备

这是原第 2 节迁移要求的独立交付单元，不新增治理系统。依赖 C1–C6 完成、R 已发布并在 C4 集成，以及对应业务回归通过；尤其不能跳过 C3/C4/C6 的结果可读性联调。

本单元交付可验证的切换代码和操作说明。生产停旧服务、取消旧任务、数据搬迁、部署与回退仍需单独授权，不因 plan 获准实施而自动执行。

### Files / 交付物

- `backend/scripts/dev/migrate_background_tasks.py`（新）：默认只读 dry-run，输出旧记录清单；经授权后可中断重跑地回填并核对。
- `backend/alembic/versions/`：回填核对后的最终结构收缩／约束 migration；C1 只交付新增结构阶段。
- Helm 的 `deploy/kubernetes/charts/cubeplex/templates/backend-deployment.yaml`、Compose 的 `deploy/docker-compose/compose.yaml` 及其共用升级入口：迁移锁、旧库回填门槛和空库安装。
- `backend/cubeplex/api/app.py`：拒绝未完成切换时启动新生命周期写入者；只在切换完成后启用唯一的新 coordinator。
- `backend/tests/e2e/test_background_task_migration.py`：旧数据、回填重试、启动拒绝和并发升级测试；现有 Kubernetes／Compose 部署文档同步维护步骤。

### 回填与切换顺序

1. 在隔离测试库验证结构和数据迁移。新增表／字段、旧字段退出分别由模型 metadata 经 `alembic revision --autogenerate` 产生；不手写／改写结构 migration。新增表承接原记录是数据复制，不依赖 autogenerate 自动识别 rename。新增结构阶段 task_id 等待回填字段允许为空，旧字段／表保留；回填验证后才生成／应用收紧约束和删除旧结构的阶段，未知历史实例不靠伪造值满足约束。
2. 数据搬迁工具计划放 backend/scripts/dev/migrate_background_tasks.py，默认只读 dry-run。列出旧 inflight、run／conversation lifetime、历史停止、notice／checkpoint、实例证据和未知 deadline；单列旧 monitor 多次 line／exit 记录，不能猜成新的单次匹配。实际执行需独立授权。
3. 经授权隔离旧 API／run worker／coordinator／排队入口，记录 checkpoint／provider 句柄及 ID 映射，再回填 task／事件／admission。旧 run-lifetime 命令先完成或明确停止，不直接授予跨 run 权限。活跃旧 monitor 同样先自然结束或经授权停止，才允许启用新一次性 monitor 写入者，不保留旧订阅兼容运行。
4. 每个 command 恰好一条 task；notice ID、去重键与 checkpoint 证明保留。旧 execute terminal＋notice_state=pending 但无 completion wake 时，只有明确合法的 conversation-lifetime 通知可幂等补齐；已送达／无权限／停止证据不明的不重放。旧 monitor 的已提交 line／exit 保留历史，其余保留原 ID、结果及取消投递原因，不重放、不合并成新 monitor_result。按可靠证据回填原实例、后台交接、generation 与 run 停止：未知不猜、不重跑、不释放 cap、不清除旧停止。已删除会话补清理意图，不恢复执行权。
5. 回填可中断重跑、重复执行不增任务／事件。核对行数、唯一性、scope、终态事实与 pending 分类，失败停止切换。唯一合法升级顺序为：停旧写入者 → 升到新增结构的指定 revision → 回填并核对 → 删除旧结构／收紧约束 → 启动新 service，最终只有一个生命周期写入者；无双写同步或旧控制路由转发层。
6. 新旧客户端／API 配套切换。中间 PR 不直接投产，不能在 Stop／通知校验尚未齐备时启用新的跨 run 行为。切换后若回退，不让旧 worker 对新 schema／新权限语义盲写；回退与备份恢复需单独审核。

### 部署入口与验收门槛

- `deploy/kubernetes/charts/cubeplex/templates/backend-deployment.yaml` 的 init 和 `deploy/docker-compose/compose.yaml` 的 backend-migrate 使用同一个有门槛的升级入口，禁止无条件 `alembic upgrade head`。
- 已有库必须通过独立、串行的维护步骤停旧 API／worker／coordinator／排队入口并确认退出，再由持有数据库迁移锁的唯一执行者升级到新增结构 revision、回填核对、升级最终 head；不能在 RollingUpdate 新 pod 的 init 内假定旧 pod 已停止，也不能让多副本各自回填。
- 普通启动只检查已完成的切换，空库可在迁移锁内完成安装；后端拒绝未完成回填的新生命周期写入者。
- C1 只交付新增结构阶段，不提前删除旧字段；最终删除阶段在 C2–C6 完整验证、门槛就绪后交付。
- 新增 `backend/tests/e2e/test_background_task_migration.py` 验证旧数据保全、pending completion 缺 wake 的幂等补齐、回填中断重跑、核对失败阻止收缩和启动、空库安装、多个升级者争锁；Helm／Compose 启动入口都覆盖拒绝未切换旧库的契约，现有 Kubernetes／Compose 部署文档同步维护步骤。
- 实际发布操作仍另行审批。

C7 验收要分别给出“切换准备已验证”和“实际部署状态”，不能把脚本／文档完成写成已上线；上线前还须确认旧 monitor 清点及必要停止获得授权。

## 2. 各单元验证纪律

1. 开发按 [testing](../../testing.md) 的 red→green 保护各单元契约，只运行改动模块；联调再跑相关 E2E。Postgres／Redis／FastAPI 用真实服务，只在最外层注入故障；小时级时间用可控时钟、barrier 和有界状态等待，不真等几小时。
2. C1–C6 完成且 R 集成后，统一验证唯一 Done、prompt cache、HITL、SSE 结束后新 run、原现场业务流程，并将结果交 C7 核对切换条件。保留每项命令与结果；不把文档检查当运行时验收。噪声测试输出写入 gitignored tmp 日志，失败先读 traceback，不重复盲跑。
3. 用户可见改动与匹配站点文档同 PR；常规 push 由 pre-push 运行对应 check-ci，不提前重复手跑。用户已批准实施；实际发布及线上数据操作仍需独立审批。

## 3. Spec 覆盖与完成定义

| Spec 验收编号 | 负责单元／关键验证 |
| --- | --- |
| 1、2 | C1、C4：run 可结束、小时级观察、原实例接管而非重做 |
| 3、4 | C2、C3：active／idle／HITL 路由与唯一 active slot |
| 5 | C1、C3、C4、C6：前台终态与后台交接唯一结果路径 |
| 6 | R、C4：合法等待与新输入失效，不强制续跑或伪造完成 |
| 7、8、13、14 | C1–C5：聊天 Stop／全部停止分离、handoff 竞争、旧请求不误停、单任务控制 |
| 9、17、18 | C1、C2、C3：单一事实、能力、scope、deadline 与停止证据 |
| 10 | C6，与 C3/C4/C5 联调：日志确认与清理分离、终态后重试 |
| 11、15 | C3、C5：来源分流、恢复、无旧 SSE 仍发现回复 |
| 12 | R、C3、C4、C5：checkpoint、cache、required consumer、Done |
| 16 | C1、C3、C5：不同任务独立 notice，同 monitor 不逐行通知；停止 A 不取消 B |
| 19 | C1、R、C4：不改普通 MCP／同步 subagent，不交付假 detach |
| 20 | C2d：删除与 reservation／迟到句柄竞争及崩溃后清理 |
| 21 | C2a、C2c、C3：聊天 Stop 保留独立排队工作，全部停止关闭旧 occurrence／notice，新 occurrence 可受理 |
| 22 | C1、C6：sandbox 原地 revive 后不误用新实例，未知不猜填 |
| 23 | C5：窗口外 pending 冷刷新发现、分页控制与最终对账 |
| 24 | C3：首条 notice 准备期取消、初始 checkpoint 竞争及 B 独立交付 |
| 25 | C7：新增结构／回填／删除门槛、旧 monitor 清点与停止授权、新写入者启动拒绝 |
| 26 | C3：后台／混合轮次不触发 reflection，后续 consolidation 也过滤这些历史及回复 |
| 27 | C2b：请求摘要冲突拒绝、受理事务持久保存首次执行快照及 run 创建前崩溃恢复 |
| 28 | C2b：会话选择／附件与受理原子提交，旧重试不覆盖新选择，reaper 竞争不删已受理附件 |
| 29 | C2c：自动来源首次领取固定内容，定义变更不改旧重试，当前权限仍重查 |
| 30 | R、C4：先前有效等待的用户取消收尾，不强制续跑或冒用旧许可 |
| 31 | C2c：trigger 持久消费与崩溃恢复，入口资格、唯一目标及转交幂等 |
| 32 | C2c：trigger 删除与 claim／handoff 串行，保留未决源证明 |
| 33 | C2c：自动来源冻结 actor，当前权限重查且不替换身份 |
| 34 | C2d：现有删除／撤权入口的最小任务清理，新增生命周期 FK、安全重试与其他 actor 隔离 |
| 35 | C2b：run ID 启动前持久绑定，成功响应丢失后重试无第二次执行 |
| 36 | C3：后台续办仅进入同 actor Session，否则等待原身份的新 run |
| 37 | C2b：install 快捷操作的持久结果、稳定消息 ID 和响应丢失恢复 |
| 38 | C2c：schedule 删除与已领取 occurrence／IM 交接串行并保留未决证明 |
| 39 | C2b、C3、C5：所有用户输入投递边界检查 actor，其他 actor 排队后独立受理 |
| 40 | C1、C4：配置／工具参数共用正整数及 32-bit 秒数上限，受理防御日期溢出 |
| 41 | C5：summary 全 false 后仍发现新自动 run；可见页基线、隐藏恢复及已完成回复 |
| 42 | C2b、C5：HITL 只接受原 actor 回答／审批，非发起者不能代用凭据 |
| 43 | C2 保留回归／C2a：reflection／consolidation 不占 run，目标 run Stop／全部停止与原身份保护整批写事务 |
| 44 | C1、C4：现有 sandbox 管理入口清理原实例任务，未知不假报完成，不新增删除操作系统 |
| 45 | C1、C3、C4、C6、C7：一次性 monitor 结果、崩溃／退出／超时竞争、仅停止监听及旧数据切换 |
| 46 | C2–C5：前台增量 SSE 独立、停止结果回复不重投、独立后台结果不被吞掉 |

完成定义是上述范围内的不变量及业务流有实际验证证据。范围外的跨系统治理不计入 C2 未完成项，也不能因 review 顺带发现问题就重新加入交付前提；需要时另行确认。文档精简不代表公共入口已切换，#636 仍只是已列明的底层增量。

<a id="implementation-history"></a>

## 附录 A. 已有实施与验证记录

以下先保留此前各轮的核对记录，再追加本轮 C1 验证。旧记录仅说明对应提交；不代表当前远端状态或新版契约已经完整实现。

实施记录（2026-09-21；以各 PR 的提交和检查结果为准）：

- #634 交付 C1 基础模型、增量结构、事务预留和默认期限配置；87 项本地回归及该 PR 的 CI 通过。
- #635（`6f3dcc7e2`）交付 C1 运行时的原实例接管、owner 隔离、停止事实、monitor 限流和期限恢复，CI 通过。新 coordinator 尚未注册到应用，不代表生产入口已切换；宿主交接和日志收尾分别继续由 C4、C6 完成。
- CubeLoop #231（`b488ab8584`）交付 R 的等待校验、输入失效、HITL 审批来源和 extra 持久化，CI 通过；尚未合并／发布，CubePlex 依赖与宿主校验尚未接入。
- #636 是 C2 的增量 draft，当前提交 `c5c3266f8`：已实现用户请求身份及设置快照、附件保护、关闭批次、内部 RunManager 绑定及启动／退出回执、模型／工具资格校验，以及 prompt／HITL 恢复 worker 的 Redis 写入隔离和安全收尾。HITL 回答仅接受原 actor，旧设计下暂停中的批次停止不调用模型；终态写入后丢响应仍可完成清理，连续暂停不记作结束。终态后的快速发送不能抢走回执写入权，清理租约到期仍可恢复。reflection 与 consolidation 等待正常结束证明，模型边界及写事务检查原身份；访问权锁覆盖 topic／conversation participant 和归档状态，去重、容量清理及整批记忆变更不拆分提交。联合回归 157 项通过，最后的 consolidation 专项 19 项通过，严格类型检查通过；Stop／撤权与写入、终态接管竞争均先复现再验证修复。上一提交 `01f3fffe8` 的远端 CI 已通过，但 review 指出的上述问题已由本次提交修复；最新提交的推送检查、远端 CI／复审以 PR 实际结果为准，尚未作为切换依据。
- C2 仍须接通 Web／IM／steering／schedule／trigger 的受理入口、覆盖相应状态的聊天 Stop／全部停止及持久恢复、删除／撤权清理、自动来源快照及恢复、启动回执未决的对账。暂停清理在终态提交前失败或进程退出时的恢复尚未完成；单个分支可无模型停止，不等于两种停止协议已落地。HITL 已能识别原 admission，但旧入口创建的无 admission run 仍走切换前路径，不能据此声称公共入口已受完整保护；非原 actor 的明确错误展示由 C5 配套。C3–C6、依赖集成、数据回填及统一切换也未完成；上述测试不是原始六项问题的全链路验收。

以上 PR 均不包含部署或线上数据切换授权。分 PR 审核不等于中间版本可独立启用。

本轮新增验证（2026-09-21）：

- #635 的 `7ddfc2368` 将 monitor 改为单次最终结果并增加公共结果就绪字段；结构迁移由 autogenerate 生成，保留旧句柄、cursor 和历史 notice。结果、执行事实和日志恢复分别保存，deadline 先冻结 timed_out，迟到退出不重判、不增发事件。新 coordinator 仍未启用。
- 定向 unit／E2E 95 项通过，覆盖 monitor、lifecycle、coordinator、reservation、迁移及状态映射；三个改动源文件的严格 mypy 通过。
- local Codex review 发现一个 HIGH：停止父任务时复用子 command 的旧缓存，可能覆盖已经冻结的结果并使 readiness 回退。两个真实数据库会话的回归先得到 2 failed；`85a502b22` 在持锁查询时刷新子 command，原复现得到 2 passed，相关 monitor／lifecycle／coordinator 回归 43 passed。
- 同一本地审核会话复审 `6f3dcc7e2..85a502b22`，确认上述修复有效，六项范围内未剩余 CRITICAL／HIGH 或可操作的 MEDIUM／LOW。审核为只读代码检查，测试由实施方实际运行；首次本地沙箱启动失败未计作审核通过。未触发 GitHub Codex 审核。

本轮 C2a 新增验证（2026-09-22）：

- #636 已 rebase 到 #635 的 `85a502b22`。`98b0fcbd1` 增加独立 run 停止标记、前台停止／后台交接的共同锁序、原身份检查及按来源／run／批次取消用户输入。run Stop 不关闭 generation，也不撤销已交接任务的 admission；在途启动回执仍保存原句柄。数据库仅在隔离 worktree 中升级；迁移由 autogenerate 生成，未操作线上数据。
- 真实 DB／Redis 与内部宿主联合回归 171 项通过；另补“Redis 已创建、持久启动回执未提交”用例，先复现错误的清理完成判断，再以控制／启动回归 32 项验证修复。严格 mypy 6 个源文件通过，迁移往返验证 1 项通过。提交后的 pre-push 后端检查通过；push 后的本地 R1 审核没有可操作发现。
- 后续恢复检查发现：run／单任务／全部停止后，已取消前台任务即使确认未启动且日志已完成，仍被 coordinator 反复领取并尝试已禁止的后台交接。三个真实数据库用例先失败；`6a8e57642` 对齐扫描、管理判定及恢复回调的取消条件，不伪造前台交付或后台交接记录。复现 3 项、相关回归 57 项及严格类型检查通过；commit／push 后在同一本地会话复审，未发现可操作问题。
- 以上只证明 C2a 的持久控制基础与前台任务清理修订。停止 HTTP 接口、run／HITL 重启恢复、实际输入来源写入仍需后续提交和独立证据；C2b–C2d、C3–C7、R 的宿主集成仍未完成。

本次实施不重开此前移除的通用删除协调与跨系统治理。#635 的旧 monitor 限流仅保留为历史记录；新一次性契约的 C1 证据如上。C2 继续接通控制入口与持久恢复，C3–C5 接通交接、通知和 UI，C6/C7 完成日志联调与切换门槛后才能启用。沿用既有原实例与记忆事务保护，不为这两项产品变化扩展其他子系统。
