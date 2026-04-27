#!/usr/bin/env python3
"""
将一个大 Excel 按行数分片，或按某列取值拆成多个文件（每个取值一个文件）。
用法:
  python split_excel.py --input data.xlsx --by-rows 5000 --output-dir ./chunks
  python split_excel.py --input data.xlsx --by-column 地区 --output-dir ./by_region
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def _sanitize_filename(s):
    s = str(s).strip()
    for c in '\\/:*?"<>|':
        s = s.replace(c, "_")
    return s[:50] or "unnamed"


def main():
    parser = argparse.ArgumentParser(description="按行数或按列取值拆分 Excel")
    parser.add_argument("--input", "-i", required=True, help="输入 .xlsx 文件")
    parser.add_argument("--sheet", default=0, help="工作表名或索引")
    parser.add_argument("--by-rows", type=int, help="每 N 行一个文件")
    parser.add_argument("--by-column", help="按该列取值拆分，每个取值一个文件")
    parser.add_argument("--output-dir", "-o", default=".", help="输出目录")
    parser.add_argument("--prefix", default="part", help="按行分片时的文件名前缀")
    args = parser.parse_args()

    if not args.by_rows and not args.by_column:
        print("请指定 --by-rows 或 --by-column", file=sys.stderr)
        sys.exit(1)
    if args.by_rows and args.by_column:
        print("请只指定 --by-rows 或 --by-column 之一", file=sys.stderr)
        sys.exit(1)

    path = Path(args.input)
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        df = pd.read_excel(path, sheet_name=args.sheet, engine="openpyxl")
    except Exception as e:
        print(f"读取失败: {e}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = path.stem

    if args.by_rows:
        n = args.by_rows
        for i in range(0, len(df), n):
            chunk = df.iloc[i : i + n]
            out_path = out_dir / f"{args.prefix}_{i // n + 1:04d}.xlsx"
            chunk.to_excel(out_path, index=False, engine="openpyxl")
        count = (len(df) + n - 1) // n
        print(f"已按每 {n} 行拆成 {count} 个文件 -> {out_dir}")
        return

    col = args.by_column
    if col not in df.columns:
        print(f"列不存在: {col}", file=sys.stderr)
        sys.exit(1)
    for val, sub in df.groupby(col, dropna=False):
        name = _sanitize_filename(val) if pd.notna(val) else "null"
        out_path = out_dir / f"{base}_{name}.xlsx"
        sub.to_excel(out_path, index=False, engine="openpyxl")
    print(f"已按列 '{col}' 拆成 {df[col].nunique()} 个文件 -> {out_dir}")


if __name__ == "__main__":
    main()
