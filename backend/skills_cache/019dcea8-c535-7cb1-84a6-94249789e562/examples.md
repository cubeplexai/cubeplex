# Excel 自动化 — 示例

## 示例 1：多文件合并为一个 Excel

需求：`data/` 下多个 .xlsx，表结构相同，合并为一个表并写到一个新 Excel。

```python
import pandas as pd
from pathlib import Path

files = list(Path("data").glob("*.xlsx"))
dfs = [pd.read_excel(f, engine="openpyxl") for f in files]
merged = pd.concat(dfs, ignore_index=True)
merged.to_excel("data/merged.xlsx", index=False)
```

## 示例 2：按条件筛选并导出 CSV

需求：从 `report.xlsx` 的「订单」表里筛选「状态」为「已完成」，导出为 CSV。

```python
import pandas as pd

df = pd.read_excel("report.xlsx", sheet_name="订单", engine="openpyxl")
done = df[df["状态"] == "已完成"]
done.to_csv("orders_done.csv", index=False, encoding="utf-8-sig")
```

## 示例 3：保留原表格式并写入新数据

需求：在已有模板 `template.xlsx` 的「汇总」表从第 2 行起写入新数据，不动表头和格式。

```python
import openpyxl

wb = openpyxl.load_workbook("template.xlsx")
ws = wb["汇总"]
for i, row in enumerate(new_data_rows, start=2):
    for j, val in enumerate(row, start=1):
        ws.cell(row=i, column=j, value=val)
wb.save("output.xlsx")
```

## 示例 4：使用脚本合并多表

```bash
python scripts/merge_sheets.py --inputs file1.xlsx file2.xlsx --output merged.xlsx
python scripts/merge_sheets.py --inputs ./data --output merged.xlsx
```

## 示例 5：校验并检查重复键

```bash
python scripts/validate_excel.py --input data.xlsx --key-cols 编号,日期 --require-cols 名称,金额
```

## 示例 6：CSV 转 Excel（多 CSV 多 sheet）

```bash
python scripts/csv_to_excel.py --inputs a.csv b.csv --output report.xlsx
python scripts/csv_to_excel.py --input data.csv --output data.xlsx --sheet-name 原始数据
```

## 示例 7：按条件筛选并输出

```bash
python scripts/filter_excel.py --input orders.xlsx --where "状态=已完成" --where "金额>100" --output done.xlsx
python scripts/filter_excel.py --input users.xlsx --where "城市~北京" --output beijing.csv
```

## 示例 8：按行数或按列拆分

```bash
python scripts/split_excel.py --input large.xlsx --by-rows 5000 --output-dir ./chunks
python scripts/split_excel.py --input sales.xlsx --by-column 地区 --output-dir ./by_region
```

## 示例 9：去重与聚合

```bash
python scripts/deduplicate_excel.py --input data.xlsx --keys 编号 --keep last --output unique.xlsx
python scripts/aggregate_excel.py --input sales.xlsx --group-by 地区 --agg "销售额:sum,数量:sum" --output summary.xlsx
python scripts/aggregate_excel.py --input orders.xlsx --group-by 部门,月份 --agg "金额:sum" "订单数:count"
```

## 示例 10：选择列与重命名

```bash
python scripts/select_columns.py --input data.xlsx --columns 姓名,部门,金额 --output out.xlsx
python scripts/select_columns.py --input data.xlsx --columns 姓名,部门,金额 --rename "部门:dept,金额:amount" --output out.csv
```

## 示例 11：两表按键合并（VLOOKUP）

```bash
python scripts/merge_tables.py --left 订单.xlsx --right 客户.xlsx --on 客户ID --output 订单带客户.xlsx
python scripts/merge_tables.py --left a.xlsx --right b.xlsx --on 编号 --how inner --output merged.xlsx
```

## 示例 12：行列转置

```bash
python scripts/transpose_excel.py --input data.xlsx --output transposed.xlsx
python scripts/transpose_excel.py --input data.xlsx --output transposed.xlsx --first-col-as-header
```

## 示例 13：模板填充

模板中某行单元格写 `{{姓名}}`、`{{金额}}` 等，数据表有对应列；每行数据生成一行填充结果。

```bash
python scripts/template_fill.py --template report_template.xlsx --data items.csv --output report.xlsx
python scripts/template_fill.py --template t.xlsx --data d.xlsx --pattern-row 2 --output out.xlsx
```

## 示例 14：重命名 sheet

```bash
python scripts/rename_sheets.py --input file.xlsx --rename "Sheet1:汇总" "Sheet2:明细" --output out.xlsx
python scripts/rename_sheets.py --input file.xlsx --rename "0:首页" "1:数据"
python scripts/rename_sheets.py --input file.xlsx --prefix "2024_" --output out.xlsx
```

## 示例 15：多表 VLOOKUP

主表依次与多个查找表按指定键列左连接，每次追加查找表中的列。

```bash
python scripts/vlookup_multi.py --main 订单.xlsx --lookups "客户.xlsx:客户ID" "产品.xlsx:产品ID" --output 订单完整.xlsx
python scripts/vlookup_multi.py --main main.xlsx --lookups "a.xlsx:编号" "b.xlsx:ID" --output merged.xlsx
```

## 示例 16：条件格式

对指定区域设置大于/小于/介于/重复值高亮或双色色阶。

```bash
python scripts/format_conditional.py --input data.xlsx --range "C2:C100" --rule gt --value 100 --fill red --output out.xlsx
python scripts/format_conditional.py --input data.xlsx --column D --rule duplicates --fill yellow
python scripts/format_conditional.py --input data.xlsx --range "E2:E50" --rule between --min 0 --max 1 --fill green
python scripts/format_conditional.py --input data.xlsx --range "F2:F100" --color-scale --output out.xlsx
```

## 示例 17：列设为文本格式（避免科学计数法）

长数字列（身份证号、订单号、条码等）在 Excel 中易被显示为科学计数法，将列设为文本格式并写为字符串即可完整显示。

```bash
python scripts/format_columns_as_text.py --input data.xlsx --columns 身份证号,订单号 --output out.xlsx
python scripts/format_columns_as_text.py --input data.xlsx --columns A,B,C --output out.xlsx
python scripts/format_columns_as_text.py --input data.xlsx --columns 编号 --sheet 明细 --output out.xlsx
```
