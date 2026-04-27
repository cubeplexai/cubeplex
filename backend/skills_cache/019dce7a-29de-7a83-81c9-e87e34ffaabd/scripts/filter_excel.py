#!/usr/bin/env python3
"""
按列条件筛选 Excel 行，输出到新 Excel 或 CSV。
条件格式：列名=值（等于）、列名>值、列名>=、<、<=、!=、列名~子串（包含）。
用法:
  python filter_excel.py --input data.xlsx --where "状态=已完成" --output out.xlsx
  python filter_excel.py --input data.xlsx --where "金额>1000" "地区~北京" --output out.csv
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd


def _parse_where(expr):
    # 列名 运算符 值，值可带引号
    m = re.match(r"^(\w+)\s*(=|!=|>=|<=|>|<|~)\s*(.*)$", expr.strip())
    if not m:
        return None
    col, op, val = m.group(1), m.group(2), m.group(3).strip()
    if val.startswith('"') and val.endswith('"'):
        val = val[1:-1]
    elif val.startswith("'") and val.endswith("'"):
        val = val[1:-1]
    return col, op, val


def _apply_filter(df, col, op, val):
    if col not in df.columns:
        raise ValueError(f"列不存在: {col}")
    s = df[col].astype(str)
    if op == "=":
        return df[df[col].astype(str) == str(val)]
    if op == "!=":
        return df[df[col].astype(str) != str(val)]
    if op == "~":
        return df[s.str.contains(str(val), na=False, regex=False)]
    try:
        num = pd.to_numeric(df[col], errors="coerce")
        v = pd.to_numeric(val, errors="coerce")
    except Exception:
        v = val
        num = df[col]
    if op == ">":
        return df[num > v]
    if op == ">=":
        return df[num >= v]
    if op == "<":
        return df[num < v]
    if op == "<=":
        return df[num <= v]
    return df


def main():
    parser = argparse.ArgumentParser(description="按条件筛选 Excel 行")
    parser.add_argument("--input", "-i", required=True, help="输入 .xlsx 文件")
    parser.add_argument("--output", "-o", required=True, help="输出 .xlsx 或 .csv")
    parser.add_argument("--sheet", default=0, help="工作表名或索引")
    parser.add_argument("--where", "-w", action="append", required=True, help="条件，可多次指定（AND）")
    parser.add_argument("--encoding", default="utf-8-sig", help="输出为 CSV 时的编码")
    args = parser.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        df = pd.read_excel(path, sheet_name=args.sheet, engine="openpyxl")
    except Exception as e:
        print(f"读取失败: {e}", file=sys.stderr)
        sys.exit(1)

    for expr in args.where:
        parsed = _parse_where(expr)
        if not parsed:
            print(f"无效条件: {expr}", file=sys.stderr)
            sys.exit(1)
        col, op, val = parsed
        try:
            df = _apply_filter(df, col, op, val)
        except ValueError as e:
            print(e, file=sys.stderr)
            sys.exit(1)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".csv":
        df.to_csv(out, index=False, encoding=args.encoding)
    else:
        df.to_excel(out, index=False, engine="openpyxl")
    print(f"筛选后 {len(df)} 行 -> {out}")


if __name__ == "__main__":
    main()
