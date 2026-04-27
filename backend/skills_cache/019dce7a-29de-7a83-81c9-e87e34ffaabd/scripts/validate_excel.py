#!/usr/bin/env python3
"""
校验 Excel 表：检查必须列、空行、重复键等。
用法:
  python validate_excel.py --input data.xlsx
  python validate_excel.py --input data.xlsx --require-cols 名称,金额 --key-cols 编号
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="校验 Excel 表结构与数据")
    parser.add_argument("--input", "-i", required=True, help="输入 .xlsx 文件")
    parser.add_argument("--sheet", default=0, help="工作表名或索引")
    parser.add_argument("--key-cols", default="", help="用于检查重复的列名，逗号分隔")
    parser.add_argument("--require-cols", default="", help="必须存在的列名，逗号分隔")
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

    errors = []

    if args.require_cols:
        required = [c.strip() for c in args.require_cols.split(",") if c.strip()]
        missing = [c for c in required if c not in df.columns]
        if missing:
            errors.append(f"缺少必须列: {missing}")

    if args.key_cols:
        keys = [c.strip() for c in args.key_cols.split(",") if c.strip()]
        key_missing = [c for c in keys if c not in df.columns]
        if key_missing:
            errors.append(f"键列不存在: {key_missing}")
        else:
            dup = df[df.duplicated(subset=keys, keep=False)]
            if not dup.empty:
                errors.append(f"存在重复键，共 {len(dup)} 行")

    # 全空行
    empty = df.dropna(how="all")
    if len(empty) < len(df):
        errors.append(f"存在 {len(df) - len(empty)} 行全空行")

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    print("校验通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
