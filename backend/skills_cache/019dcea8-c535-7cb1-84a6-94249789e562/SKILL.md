---
name: automate-excel
description: Automates reading, writing, merging, transforming, and validating Excel (.xlsx/.xls) files. Use when the user works with spreadsheets, .xlsx files, Excel data, CSV-to-Excel conversion, batch Excel processing, or report generation from tables.
---

# Excel 自动化处理

在用户需要处理 Excel 文件、表格数据、批量转换或报表生成时应用本 skill。


| 你想做的事 | 调用的脚本 | 典型参数 |
|------------|------------|----------|
| 多个 Excel 或多个 sheet 合成一张表 | **merge_sheets.py** | `--inputs 文件或目录 --output out.xlsx` |
| 把某 sheet 导出成 CSV | **excel_to_csv.py** | `--input file.xlsx --output file.csv` |
| 把 CSV 转成 Excel | **csv_to_excel.py** | `--input a.csv --output a.xlsx` 或 `--inputs a.csv b.csv` |
| 按条件筛选行（等于/大于/包含） | **filter_excel.py** | `--where "列名=值"` 或 `"列名>100"`、`"列名~北京"` |
| 按行数拆成多个文件，或按某列取值拆分 | **split_excel.py** | `--by-rows 5000` 或 `--by-column 地区` |
| 按某列去重 | **deduplicate_excel.py** | `--keys 编号 --keep first/last` |
| 按列分组并求和/计数/平均 | **aggregate_excel.py** | `--group-by 地区 --agg "销售额:sum"` |
| 检查必须列、重复键、空行 | **validate_excel.py** | `--require-cols 列名 --key-cols 列名` |
| 只保留/重命名部分列 | **select_columns.py** | `--columns 列1,列2 --rename "旧:新"` |
| 两个表按一列对齐合并（VLOOKUP） | **merge_tables.py** | `--left a.xlsx --right b.xlsx --on 键列` |
| 主表依次跟多个表做 VLOOKUP | **vlookup_multi.py** | `--main 主.xlsx --lookups "表1.xlsx:键列" "表2.xlsx:键列"` |
| 行列转置 | **transpose_excel.py** | `--input in.xlsx --output out.xlsx` |
| 用数据表按行填模板里的 {{列名}} | **template_fill.py** | `--template t.xlsx --data d.csv --output out.xlsx` |
| 重命名工作表 | **rename_sheets.py** | `--rename "Sheet1:新名"` 或 `--prefix "2024_"` |
| 条件格式（大于/小于/重复值/色阶） | **format_conditional.py** | `--column C --rule gt --value 100 --fill red` |
| 某列当文本显示，避免科学计数法 | **format_columns_as_text.py** | `--columns 身份证号,订单号` |

**本地直接运行**：进入本 skill 所在目录（或把 `scripts/` 加入路径），先 `pip install -r scripts/requirements.txt`，再执行 `python scripts/脚本名.py --help` 看参数，或按上表传参运行。

## 技术栈

- **读写 .xlsx**：`openpyxl`（保留格式、公式、多工作表）
- **数据分析/透视**：`pandas` + `openpyxl` 引擎
- **旧格式 .xls**：`xlrd`（只读）

优先用 `openpyxl` 做单元格级操作和格式保留；需要筛选、聚合、合并多表时用 `pandas`。

## 依赖

```bash
pip install openpyxl pandas xlrd
```

或使用 skill 自带：`pip install -r scripts/requirements.txt`

## 通用流程

1. **确认输入**：文件路径、工作表名或索引、是否有表头、编码（CSV 时）。
2. **读取**：用 openpyxl 或 pandas 按需读取整表/区域。
3. **处理**：按用户需求转换、过滤、合并、计算。
4. **写出**：指定输出路径与格式（.xlsx/.csv）；必要时保留原格式或新建工作簿。
5. **校验**：检查行数、关键列、重复值或业务规则。

## 读取 Excel

**整表为 list of dict（保留表头）：**

```python
import openpyxl

wb = openpyxl.load_workbook("input.xlsx", read_only=True, data_only=True)
ws = wb.active  # 或 wb["Sheet1"]
rows = list(ws.iter_rows(min_row=1, values_only=True))
header = rows[0]
data = [dict(zip(header, row)) for row in rows[1:]]
wb.close()
```

**用 pandas（适合分析、过滤、合并）：**

```python
import pandas as pd

df = pd.read_excel("input.xlsx", sheet_name=0, engine="openpyxl")
# sheet_name 可为 0、"Sheet1" 或 [0, 1] 多表
```

**指定区域：**

```python
# openpyxl
for row in ws["A1:D10"]:
    ...

# pandas
df = pd.read_excel("input.xlsx", usecols="A:D", header=0, nrows=100)
```

## 写入 Excel

**新建并写入（openpyxl）：**

```python
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment

wb = Workbook()
ws = wb.active
ws.title = "结果"
ws.append(["列A", "列B", "列C"])
for row in data_rows:
    ws.append(row)
ws["A1"].font = Font(bold=True)
wb.save("output.xlsx")
```

**用 pandas 写出多表：**

```python
with pd.ExcelWriter("output.xlsx", engine="openpyxl") as writer:
    df1.to_excel(writer, sheet_name="汇总", index=False)
    df2.to_excel(writer, sheet_name="明细", index=False)
```

**追加到已有文件（先加载再写）：**

```python
wb = openpyxl.load_workbook("existing.xlsx")
ws = wb["Sheet1"]
for row in new_rows:
    ws.append(row)
wb.save("existing.xlsx")
```

## 常见任务速查

| 任务           | 做法 |
|----------------|------|
| 多文件合并     | **merge_sheets.py** 或 pandas `read_excel` + `pd.concat` + `to_excel`。 |
| CSV ↔ Excel    | **excel_to_csv.py** / **csv_to_excel.py** 或 pandas 读写。 |
| 按条件筛选     | **filter_excel.py** 或 `df[df["列"] == 值]` / `df.query()`。 |
| 按行/按列拆分  | **split_excel.py**（按行数或按某列取值分文件）。 |
| 去重           | **deduplicate_excel.py** 或 `df.drop_duplicates(subset=["列"], keep="first")`。 |
| 按列聚合       | **aggregate_excel.py** 或 `df.groupby("列").agg({"数值列": "sum"})`。 |
| 校验           | **validate_excel.py**。 |
| 选择/重命名列  | **select_columns.py**。 |
| 两表按键合并   | **merge_tables.py**（left/inner/outer）。 |
| 行列转置       | **transpose_excel.py**。 |
| 模板填充       | **template_fill.py**（{{列名}} 占位符）。 |
| 重命名 sheet   | **rename_sheets.py**。 |
| 多表 VLOOKUP  | **vlookup_multi.py**（主表 + 多个查找表依次左连接）。 |
| 条件格式       | **format_conditional.py**（大于/小于/介于/重复值/色阶）。 |
| 列格式为文本   | **format_columns_as_text.py**（避免长数字科学计数法）。 |
| 保留原格式写数据 | 用 openpyxl 加载原文件，只改写目标单元格，再 save。 |

## 批量处理

当用户需要处理目录下多个 Excel 时：

1. 用 `pathlib.Path("目录").glob("*.xlsx")` 枚举文件。
2. 对每个文件读取 → 处理 → 可汇总到一个 DataFrame 或分别写出。
3. 输出可为一个合并文件，或 `原名_out.xlsx` 等约定；在 SKILL 中明确输出命名规则。
4. 出错时记录文件名和异常，继续处理其余文件，最后汇总报错列表。

## 校验与错误处理

- 读取前用 `Path(file).exists()` 检查文件存在。
- 表为空或缺少预期列时给出明确提示（列名/行数）。
- 写入前若目标文件已存在，按用户要求覆盖或换名；大文件考虑 `write_only=True` 或分块。
- 捕获 `openpyxl.utils.exceptions.InvalidFileException`、`KeyError`（工作表名）等并返回可读错误信息。

## 工具脚本

skill 目录下 `scripts/` 提供可执行脚本，优先在用户环境中运行脚本而非临时写长代码。

| 脚本 | 功能 |
|------|------|
| **merge_sheets.py** | 多 Excel 或同文件多 sheet 合并为一张表 |
| **excel_to_csv.py** | 指定 sheet 导出为 CSV |
| **csv_to_excel.py** | CSV 转 Excel（单/多 CSV → 多 sheet） |
| **filter_excel.py** | 按列条件筛选（=、>、<、~ 包含）输出 Excel/CSV |
| **split_excel.py** | 按行数分片或按某列取值拆成多个文件 |
| **deduplicate_excel.py** | 按指定列去重，保留 first/last |
| **aggregate_excel.py** | 按列分组聚合（sum/count/mean/min/max） |
| **validate_excel.py** | 校验必须列、重复键、空行 |
| **select_columns.py** | 选择/重命名/排序列 |
| **merge_tables.py** | 两表按键列合并（VLOOKUP 式，left/inner/outer） |
| **transpose_excel.py** | 行列转置 |
| **template_fill.py** | 用数据表按行填充模板中的 {{列名}} 占位符 |
| **rename_sheets.py** | 重命名工作表（原名:新名、索引:新名、前缀/后缀） |
| **vlookup_multi.py** | 多表 VLOOKUP：主表依次与多个查找表左连接 |
| **format_conditional.py** | 按条件格式化（gt/lt/介于/重复值/色阶） |
| **format_columns_as_text.py** | 将指定列设为文本格式，避免纯数字显示为科学计数法 |

用法见各脚本 `--help` 或 [reference.md](reference.md)。

## 更多说明

- 详细 API 与参数见 [reference.md](reference.md)。
- 更多场景示例见 [examples.md](examples.md)。
