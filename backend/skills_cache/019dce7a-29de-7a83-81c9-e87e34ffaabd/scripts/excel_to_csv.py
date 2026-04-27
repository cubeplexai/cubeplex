#!/usr/bin/env python3
"""
将指定 Excel 工作表导出为 CSV。
用法:
  python excel_to_csv.py --input file.xlsx --output file.csv
  python excel_to_csv.py --input file.xlsx --sheet 明细 --encoding utf-8-sig --output out.csv
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="将 Excel 指定 sheet 导出为 CSV")
    parser.add_argument("--input", "-i", required=True, help="输入 .xlsx 文件")
    parser.add_argument("--output", "-o", required=True, help="输出 .csv 路径")
    parser.add_argument("--sheet", default=0, help="工作表名或索引，默认第一个")
    parser.add_argument("--encoding", default="utf-8-sig", help="CSV 编码，默认 utf-8-sig")
    parser.add_argument("--sep", default=",", help="分隔符，默认逗号")
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

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding=args.encoding, sep=args.sep)
    print(f"已导出 {len(df)} 行到 {out}")


if __name__ == "__main__":
    main()
