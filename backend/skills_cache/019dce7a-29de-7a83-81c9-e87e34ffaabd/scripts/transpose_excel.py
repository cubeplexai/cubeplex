#!/usr/bin/env python3
"""
行列转置：首行变首列，首列变首行。可选保留原首行作为列名。
用法:
  python transpose_excel.py --input data.xlsx --output out.xlsx
  python transpose_excel.py --input data.xlsx --output out.xlsx --first-col-as-header
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="行列转置")
    parser.add_argument("--input", "-i", required=True, help="输入 .xlsx 文件")
    parser.add_argument("--output", "-o", required=True, help="输出 .xlsx 或 .csv")
    parser.add_argument("--sheet", default=0, help="工作表名或索引")
    parser.add_argument("--first-col-as-header", action="store_true", help="转置后用第一列作为列名（否则用 0,1,2...）")
    args = parser.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        df = pd.read_excel(path, sheet_name=args.sheet, engine="openpyxl", header=None)
    except Exception as e:
        print(f"读取失败: {e}", file=sys.stderr)
        sys.exit(1)

    out_df = df.T
    if args.first_col_as_header:
        out_df.columns = out_df.iloc[0]
        out_df = out_df.iloc[1:].reset_index(drop=True)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".csv":
        out_df.to_csv(out, index=False, encoding="utf-8-sig")
    else:
        out_df.to_excel(out, index=False, engine="openpyxl")
    print(f"已转置 {df.shape[0]}x{df.shape[1]} -> {out_df.shape[0]}x{out_df.shape[1]} -> {out}")


if __name__ == "__main__":
    main()
