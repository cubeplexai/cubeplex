# Excel 自动化 — API 与参数参考

## openpyxl 常用

- `openpyxl.load_workbook(path, read_only=False, data_only=False, keep_vba=False)`
  - `read_only`：大文件时 True 节省内存，此时不可写。
  - `data_only=True`：读单元格时取计算后的值而非公式。
- `wb.sheetnames`：工作表名列表。
- `wb.active`：当前活动表；`wb["Sheet1"]` 按名取表。
- `ws.iter_rows(min_row, max_row, min_col, max_col, values_only=True)`：按行迭代。
- `ws["A1"].value`、`ws.cell(row=1, column=1).value`：读单元格。
- `ws.append([a, b, c])`：在最后一行后追加一行。
- 写出：`wb.save(path)`；若为 read_only 打开的，需用另一 Workbook 写入。

## pandas read_excel

- `pd.read_excel(io, sheet_name=0, header=0, names=None, usecols=None, index_col=None, engine="openpyxl")`
  - `sheet_name`：0、"Sheet1"、或 [0, 1] 多表（返回 dict）。
  - `header`：表头行索引。
  - `usecols`：如 "A:D" 或 [0, 1, 2]。
  - `nrows`：只读前 n 行。
- 引擎：.xlsx 用 `openpyxl`，.xls 用 `xlrd`。

## pandas to_excel / ExcelWriter

- `df.to_excel(excel_writer, sheet_name="Sheet1", index=True, startrow=0, startcol=0)`
- 多表：`with pd.ExcelWriter(path, engine="openpyxl") as w: df1.to_excel(w, sheet_name="A"); df2.to_excel(w, sheet_name="B")`

## scripts 参数速查

**merge_sheets.py**

- `--inputs`：输入文件或目录（多个用空格）。
- `--output`：输出 .xlsx 路径。
- `--sheet`：若输入为单文件，可指定要合并的 sheet 名（默认全部）。
- `--header-row`：表头行索引，默认 0。

**excel_to_csv.py**

- `--input`：.xlsx 文件。
- `--output`：输出 .csv 路径。
- `--sheet`：工作表名或索引，默认第一个。
- `--encoding`：如 utf-8-sig。
- `--sep`：分隔符，默认 `,`。

**validate_excel.py**

- `--input`：.xlsx 文件。
- `--sheet`：工作表名或索引。
- `--key-cols`：用于检查重复的列名，逗号分隔。
- `--require-cols`：必须存在的列名，逗号分隔。
- 退出码：0 通过，非 0 校验失败。

**csv_to_excel.py**

- `--input`：单个 CSV（与 `--inputs` 二选一）。
- `--inputs`：多个 CSV，每个转为一个 sheet。
- `--output`：输出 .xlsx。
- `--encoding`、`--sep`：CSV 编码与分隔符。
- `--sheet-name`：单文件时的 sheet 名。

**filter_excel.py**

- `--input`、`--output`：输入 .xlsx，输出 .xlsx 或 .csv。
- `--sheet`：工作表名或索引。
- `--where`：条件，可多次（AND）。格式：`列名=值`、`列名>值`、`列名~子串`（包含）等。

**split_excel.py**

- `--input`、`--sheet`：输入 .xlsx 与工作表。
- `--by-rows`：每 N 行一个文件。
- `--by-column`：按该列取值拆分，每个取值一个文件。
- `--output-dir`、`--prefix`：输出目录与行分片时的文件名前缀。

**deduplicate_excel.py**

- `--input`、`--output`：输入/输出 .xlsx 或 .csv。
- `--sheet`：工作表名或索引。
- `--keys`：去重键列名，逗号分隔。
- `--keep`：first 或 last。

**aggregate_excel.py**

- `--input`、`--output`：输入 .xlsx，输出可选（默认输入名_agg.xlsx）。
- `--sheet`：工作表名或索引。
- `--group-by`：分组列，逗号分隔。
- `--agg`：聚合，如 `列名:sum`、`列名:count`，可多次或逗号分隔。支持 sum/count/mean/min/max。

**select_columns.py**

- `--input`、`--output`：输入 .xlsx，输出 .xlsx 或 .csv。
- `--columns`：保留的列名，按此顺序，逗号分隔。
- `--rename`：重命名 旧名:新名，逗号分隔。

**merge_tables.py**

- `--left`、`--right`：左表/右表 .xlsx。
- `--on`：键列名（可逗号分隔多键）。
- `--output`：输出 .xlsx 或 .csv。
- `--how`：left（默认）/inner/outer。
- `--sheet-left`、`--sheet-right`：左右表 sheet。
- `--suffixes`：重名列后缀，默认 _left,_right。

**transpose_excel.py**

- `--input`、`--output`：输入/输出 .xlsx 或 .csv。
- `--sheet`：工作表名或索引。
- `--first-col-as-header`：转置后用第一列作为列名。

**template_fill.py**

- `--template`、`--data`：模板 .xlsx、数据 .xlsx 或 .csv。
- `--output`：输出 .xlsx。
- `--pattern-row`：模板中占位符所在行号（1-based），默认 2。
- `--sheet-template`、`--sheet-data`：模板/数据 sheet。模板单元格内 {{列名}} 会被替换为数据行对应列的值。

**rename_sheets.py**

- `--input`、`--output`：输入 .xlsx，输出可选（默认覆盖）。
- `--rename`：原名:新名 或 索引:新名（索引从 0 起），可多次。
- `--prefix`、`--suffix`：为所有 sheet 名加前缀/后缀。

**vlookup_multi.py**

- `--main`：主表 .xlsx。
- `--lookups`：查找表 文件:键列名，如 "ref.xlsx:ID"，可多个，依次左连接。
- `--output`：输出 .xlsx 或 .csv。
- `--sheet-main`：主表 sheet。查找表默认每个文件的第一个 sheet。
- `--suffix`：与主表重名列在查找表中的后缀，默认 _lookup。

**format_conditional.py**

- `--input`、`--output`：输入 .xlsx，输出可选（默认覆盖）。
- `--sheet`：工作表名或索引。
- `--range` 或 `--column`：应用区域（如 A2:A100）或列字母（自动从第 2 行到最大行）。
- `--rule`：gt/gte/lt/lte/eq/between/duplicates。
- `--value`：gt/lt/eq 等的比较值。
- `--min`、`--max`：between 时的下限、上限。
- `--fill`：满足条件时的填充色（red、yellow 或六位十六进制如 FF6B6B）。
- `--color-scale`：使用双色色阶（红-绿），忽略 --rule/--value。

**format_columns_as_text.py**

- `--input`、`--output`：输入 .xlsx，输出可选（默认覆盖）。
- `--sheet`：工作表名或索引。
- `--columns`：要设为文本的列，逗号分隔；可为**列名**（与表头一致，如 身份证号,订单号）或**列字母**（如 A,B,C）。
- `--header-row`：表头行号（1-based），默认 1。脚本会将该列所有单元格设为数字格式 "@" 并将值转为字符串存储，避免长数字显示为科学计数法。
