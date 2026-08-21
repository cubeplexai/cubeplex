# `edit_file` 多处修改设计

## 背景

当前 `edit_file` 一次只接受一个 `old_string/new_string`，模型需要连续调用工具才能修改同一文件的多个位置。这会增加工具往返次数，也让每次调用都重复下载和上传文件。前端的编辑预览同样只展示一处修改，无法完整反映一次批量编辑。

本次设计把一次工具调用扩展为一组编辑，并保持现有精确匹配、必要时模糊匹配和 unified diff 返回能力。

## 目标

- 支持模型在一次调用中提交同一文件的多个编辑。
- 所有编辑都基于同一份原始文件匹配，避免前一个替换改变后一个匹配位置。
- 在任一编辑失败或编辑范围重叠时，不上传文件。
- 成功时只上传一次，并返回完整 unified diff 及可供前端展示的摘要信息。
- 前端待执行状态展示全部编辑，执行完成后继续使用 unified diff 展示结果。
- 通过工具描述明确引导模型把同一文件的相关修改合并到一次调用中。

## 非目标

- 不修改 sandbox 接口，不实现原子 compare-and-write/CAS。
- 不实现跨文件 patch 或 `apply_patch` 风格的多文件事务。
- 不引入新的数据库表、API endpoint 或持久化格式。
- 不在本次 PR 中实现完整的文件操作审计、回滚或跨进程锁。

## 方案选择

### 方案 A：`edits[]` 批量编辑（采用）

工具参数增加 `edits` 数组，每项包含 `old_string` 和 `new_string`。服务端先在原始内容上解析所有编辑，校验成功后按原始偏移量倒序应用，再单次上传。现有单编辑参数可规范化为一个元素，降低已有调用的迁移风险。

优点是模型可以表达多个不连续修改，契约清晰，且可以在上传前完成完整校验。代价是需要处理重叠范围和前端多段预览。

### 方案 B：保留单编辑并增加 `replace_all`

改动较小，但只能批量替换同一个字符串，不能表达同一文件的多个不同位置修改，不满足减少连续编辑调用的主要目标。

### 方案 C：引入完整 patch

表达能力最强，但会扩大工具解析、错误恢复和前端展示范围，超出本次单文件编辑优化的边界。

## 契约

推荐的模型调用形状为：

```json
{
  "file_path": "src/example.ts",
  "edits": [
    {"old_string": "const a = 1;", "new_string": "const a = 2;"},
    {"old_string": "return a;", "new_string": "return a + 1;"}
  ]
}
```

`edits` 至少包含一项，并限制合理的最大项数。每项默认要求 `old_string` 在原始文件中恰好出现一次；保持现有 fuzzy matching 作为精确匹配失败后的 fallback。所有项均解析成功后，若范围重叠则返回明确错误并拒绝写入。

成功结果至少包含：

```json
{
  "file_path": "src/example.ts",
  "unified_diff": "...",
  "edit_count": 2,
  "match_mode": "exact",
  "fuzzy_matched": false,
  "first_changed_line": 1
}
```

`match_mode` 在存在 fuzzy 匹配时反映为 `fuzzy`；`fuzzy_matched` 保留用于现有前端兼容展示。失败时应指出具体编辑序号和原因（缺失、重复或重叠），并保证 upload 未被调用。

## 执行算法

1. 下载文件并保留原始文本的 BOM 和换行风格信息。
2. 将单编辑旧参数或 `edits[]` 规范化为内部编辑列表。
3. 对每项在同一份原始内容上执行精确匹配，必要时执行现有 fuzzy matching。
4. 校验每项唯一命中，并按原始字符区间检查重叠。
5. 按原始起始偏移量从后往前替换，生成新内容和 unified diff。
6. 仅在所有校验完成后调用一次 `sandbox.upload`。

没有 CAS 时，仍然存在下载和上传之间的外部并发写入窗口；这是已知限制，不在本次范围内。

## 前端行为

`EditFilePreviewView` 解析 `args.edits` 并在工具仍处于 pending 时按顺序展示每个 old/new 区块，同时显示编辑数量。为兼容旧事件，若没有 `edits` 则继续读取顶层 `old_string/new_string`。完成后优先显示后端返回的 unified diff，并根据 `edit_count`、`match_mode` 和 `first_changed_line` 展示摘要；不改变现有 diff viewer 的渲染协议。

## 验证标准

- 两个及以上不连续编辑可一次成功，且 sandbox upload 恰好调用一次。
- 任一编辑缺失、重复或与另一编辑重叠时，文件内容不变且不发生 upload。
- 精确和 fuzzy 编辑可混合处理，结果摘要准确。
- BOM、CRLF 和未修改内容保持不变。
- 前端 pending 预览完整展示多处编辑，完成态展示统一 diff。
- 现有单编辑调用和既有 sandbox 编辑测试继续通过。
