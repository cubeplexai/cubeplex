#!/usr/bin/env python3
"""
合并多个 Excel 文件或同一文件中的多个 sheet 为单个表，输出一个 .xlsx。
用法:
  python merge_sheets.py --inputs a.xlsx b.xlsx --output out.xlsx
  python merge_sheets.py --inputs ./data --output out.xlsx
  python merge_sheets.py --inputs file.xlsx --sheet Sheet1 --sheet Sheet2 --output out.xlsx
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def _collect_paths(inputs):
    paths = []
    for p in inputs:
        path = Path(p).resolve()
        if not path.exists():
            print(f"跳过不存在的路径: {path}", file=sys.stderr)
            continue
        if path.is_file():
            if path.suffix.lower() in (".xlsx", ".xls"):
                paths.append(path)
            else:
                print(f"跳过非 Excel 文件: {path}", file=sys.stderr)
        else:
            for f in path.glob("*.xlsx"):
                paths.append(f)
            for f in path.glob("*.xls"):
                paths.append(f)
    return paths


def main():
    parser = argparse.ArgumentParser(description="合并多个 Excel 文件或 sheet 为单个表")
    parser.add_argument("--inputs", nargs="+", required=True, help="输入文件或目录")
    parser.add_argument("--output", "-o", required=True, help="输出 .xlsx 路径")
    parser.add_argument("--sheet", action="append", default=None, help="仅当输入为单文件时生效，可多次指定要合并的 sheet 名")
    parser.add_argument("--header-row", type=int, default=0, help="表头行索引，默认 0")
    args = parser.parse_args()

    paths = _collect_paths(args.inputs)
    if not paths:
        print("没有找到任何 Excel 文件", file=sys.stderr)
        sys.exit(1)

    dfs = []
    for path in paths:
        try:
            if len(paths) == 1 and args.sheet:
                for sh in args.sheet:
                    df = pd.read_excel(path, sheet_name=sh, header=args.header_row, engine="openpyxl")
                    dfs.append(df)
            else:
                # 单文件无 --sheet：读所有 sheet
                xl = pd.ExcelFile(path, engine="openpyxl")
                for name in xl.sheet_names:
                    df = pd.read_excel(xl, sheet_name=name, header=args.header_row)
                    dfs.append(df)
        except Exception as e:
            print(f"读取失败 {path}: {e}", file=sys.stderr)
            continue

    if not dfs:
        print("没有成功读取任何表", file=sys.stderr)
        sys.exit(1)

    merged = pd.concat(dfs, ignore_index=True)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_excel(out, index=False, engine="openpyxl")
    print(f"已合并 {len(dfs)} 个表，共 {len(merged)} 行，输出: {out}")


if __name__ == "__main__":
    main()
