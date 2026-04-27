#!/usr/bin/env python3
"""生成测试用 Excel/CSV，供 run_all_tests 使用。"""
import os
from pathlib import Path

import pandas as pd

TEST_DIR = Path(__file__).resolve().parent / "_test_out"
TEST_DIR.mkdir(parents=True, exist_ok=True)
os.chdir(TEST_DIR)

# 主表：编号, 姓名, 地区, 金额, 销售额, 数量, 状态, 日期
df_main = pd.DataFrame({
    "编号": ["A001", "A002", "A003", "A004", "A005"],
    "姓名": ["张三", "李四", "王五", "赵六", "钱七"],
    "地区": ["北京", "上海", "北京", "广州", "上海"],
    "金额": [100, 200, 150, 300, 250],
    "销售额": [1000, 2000, 1500, 3000, 2500],
    "数量": [10, 20, 15, 30, 25],
    "状态": ["已完成", "进行中", "已完成", "已完成", "进行中"],
    "日期": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
})
df_main.to_excel("sample.xlsx", index=False, sheet_name="Sheet1")

# 第二个 Excel（多 sheet 合并用）
df_extra = pd.DataFrame({
    "编号": ["B001", "B002"],
    "姓名": ["孙八", "周九"],
    "地区": ["深圳", "杭州"],
    "金额": [180, 220],
    "销售额": [1800, 2200],
    "数量": [18, 22],
    "状态": ["已完成", "进行中"],
    "日期": ["2024-01-06", "2024-01-07"],
})
df_extra.to_excel("sample2.xlsx", index=False, sheet_name="Sheet1")

# 右表（merge_tables / vlookup）：编号 + 补充列
df_lookup = pd.DataFrame({
    "编号": ["A001", "A002", "A003", "A004", "A005"],
    "部门": ["销售", "技术", "销售", "市场", "技术"],
    "备注": ["备注1", "备注2", "备注3", "备注4", "备注5"],
})
df_lookup.to_excel("lookup.xlsx", index=False, sheet_name="Sheet1")

# CSV
df_main.to_csv("sample.csv", index=False, encoding="utf-8-sig")

# 模板：第一行表头，第二行占位符 {{姓名}} {{金额}}
wb_tpl = __import__("openpyxl").Workbook()
ws = wb_tpl.active
ws["A1"], ws["B1"], ws["C1"] = "姓名", "金额", "地区"
ws["A2"], ws["B2"], ws["C2"] = "{{姓名}}", "{{金额}}", "{{地区}}"
wb_tpl.save("template.xlsx")

print("Test data written to", TEST_DIR)
