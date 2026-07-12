# usage→done 间隔 5–20s：根因与修复（trace flush 阻塞）

日期：2026-07-06。承接 run-latency 优化（PR #304 / plan
`docs/dev/plans/2026-07-06-run-latency-optimization.md`）后用户追问：
`done` 事件总在 `usage` 事件后再等 5–10s 才到，且不像是 LLM 慢。

## 定位过程

1. Redis run_events 流的毫秒时间戳确认：usage→done 间隔 19.6s（高 RTT
   开发机）；后端日志在该窗口内完全静默。
2. py-spy dump 显示事件循环空转（`select`）——run task 在 await 外部 I/O
   或定时器，不是 CPU。
3. 在 done 路径插 10 个计时日志，一轮跑出分解：**退出
   `async with trace(tracer, agent)` 占 8.65s**；其余为 HITL pending 读
   0.9s、session-usage 聚合 1.7s、杂项 <0.3s。
4. `CUBEPLEX_TRACING__ENABLED=false` A/B：trace 退出 8.65s→0ms，tail 中位
   17s→8.2s。证据闭合。

## 根因（三层叠加）

- `cubepi.tracing.trace()` 退出时 **await span flush**（设计如此：
  "exit means exported"）——遥测导出 gate 了用户可见的 done。
- `Tracer.force_flush` 是 `async def` 却内联调用 OTel provider 的
  **同步** `force_flush()`——flush 期间整个事件循环被卡住，进程内所有
  并发请求同步停摆。
- 每 turn 仅 3 个 span 但 JSONL ~190KB（span 属性携带完整 prompt/响应
  内容），经隧道 POST 到 collector 要数秒。**span 内容瘦身经讨论明确
  不做**——保留完整内容对排查有价值，修复只针对"挡路"本身。

## 修复

- cubepi PR #199（rev `9d4b11b6`）：`Tracer.force_flush` /
  `Meter.force_flush` 改 `asyncio.to_thread`（await 不再卡 loop）；
  `trace()` 增加 `flush="await"|"background"`，background 模式同步
  detach、导出转后台受监督任务，tracer 持强引用、`shutdown()` 兜底
  settle——干净退出不丢 span。
- cubeplex（本分支）：bump cubepi rev（0.12.0→0.13.0），
  `run_manager` 两处 `trace(...)` 调用点改 `flush="background"`；
  lifespan 已有 `await tracer.shutdown()`，链条闭合。

## 结果（开发机，210ms RTT 到 collector，tracing 开启）

| | tail（末 token→done）中位 |
|---|---|
| 修复前 | 14–21s |
| 修复后 | **9.4s**（与 tracing 完全关闭时的 8.2s 基本一致） |

spans 照常落盘/导出（每 run 一个 JSONL，无 flush 失败日志）。

## 剩余尾巴（未做，量化过）

- `agent.prompt()` 返回前的 cubepi 收尾 ~4–8s（checkpointer 终写 + 提供
  商流关闭，RTT 放大；215 同机环境小得多）。
- session-usage 聚合 1.3–2s（已与 drain 并行，是聚合查询本身的耗时）。
- HITL pending 读 0.5–0.9s。
