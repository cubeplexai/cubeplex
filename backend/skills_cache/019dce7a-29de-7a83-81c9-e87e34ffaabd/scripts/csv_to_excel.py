#!/usr/bin/env python3
"""
将 CSV 文件转为 Excel。支持单文件单 sheet，或多 CSV 合并为一个 Excel 多 sheet。
用法:
  python csv_to_excel.py --input data.csv --output data.xlsx
  python csv_to_excel.py --inputs a.csv b.csv --output out.xlsx
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="CSV 转 Excel")
    parser.add_argument("--input", "-i", help="单个输入 CSV（与 --inputs 二选一）")
    parser.add_argument("--inputs", nargs="+", help="多个 CSV，每个转为一个 sheet")
    parser.add_argument("--output", "-o", required=True, help="输出 .xlsx 路径")
    parser.add_argument("--encoding", default="utf-8-sig", help="CSV 编码")
    parser.add_argument("--sep", default=",", help="CSV 分隔符")
    parser.add_argument("--sheet-name", help="单文件时的 sheet 名，默认用文件名")
    args = parser.parse_args()

    if args.input and args.inputs:
        print("请只使用 --input 或 --inputs 之一", file=sys.stderr)
        sys.exit(1)
    if not args.input and not args.inputs:
        print("请指定 --input 或 --inputs", file=sys.stderr)
        sys.exit(1)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.input:
        path = Path(args.input)
        if not path.exists():
            print(f"文件不存在: {path}", file=sys.stderr)
            sys.exit(1)
        df = pd.read_csv(path, encoding=args.encoding, sep=args.sep)
        sheet_name = args.sheet_name or path.stem
        if len(sheet_name) > 31:
            sheet_name = sheet_name[:31]
        df.to_excel(out, sheet_name=sheet_name, index=False, engine="openpyxl")
        print(f"已转换 {len(df)} 行 -> {out} (sheet: {sheet_name})")
        return

    paths = [Path(p) for p in args.inputs]
    for p in paths:
        if not p.exists():
            print(f"文件不存在: {p}", file=sys.stderr)
            sys.exit(1)

    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for path in paths:
            df = pd.read_csv(path, encoding=args.encoding, sep=args.sep)
            name = path.stem
            if len(name) > 31:
                name = name[:31]
            df.to_excel(writer, sheet_name=name, index=False)
    print(f"已转换 {len(paths)} 个 CSV -> {out}")


if __name__ == "__main__":
    main()
