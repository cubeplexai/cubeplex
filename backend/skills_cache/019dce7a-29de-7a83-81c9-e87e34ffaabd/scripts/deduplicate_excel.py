#!/usr/bin/env python3
"""
按指定列去重，保留第一条或最后一条，输出到新文件。
用法:
  python deduplicate_excel.py --input data.xlsx --keys 编号 --output out.xlsx
  python deduplicate_excel.py --input data.xlsx --keys 姓名,日期 --keep last --output out.xlsx
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="按列去重")
    parser.add_argument("--input", "-i", required=True, help="输入 .xlsx 文件")
    parser.add_argument("--output", "-o", required=True, help="输出 .xlsx 或 .csv")
    parser.add_argument("--sheet", default=0, help="工作表名或索引")
    parser.add_argument("--keys", "-k", required=True, help="作为去重键的列名，逗号分隔")
    parser.add_argument("--keep", choices=("first", "last"), default="first", help="保留第一条或最后一条")
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

    keys = [c.strip() for c in args.keys.split(",") if c.strip()]
    missing = [c for c in keys if c not in df.columns]
    if missing:
        print(f"列不存在: {missing}", file=sys.stderr)
        sys.exit(1)

    before = len(df)
    out_df = df.drop_duplicates(subset=keys, keep=args.keep)
    after = len(out_df)
    removed = before - after

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".csv":
        out_df.to_csv(out, index=False, encoding=args.encoding)
    else:
        out_df.to_excel(out, index=False, engine="openpyxl")
    print(f"去重: {before} -> {after} 行，移除 {removed} 条 -> {out}")


if __name__ == "__main__":
    main()
