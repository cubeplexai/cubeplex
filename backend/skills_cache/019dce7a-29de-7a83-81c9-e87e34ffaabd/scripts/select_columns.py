#!/usr/bin/env python3
"""
选择列、重命名列、按指定顺序输出。不指定的列会被丢弃。
用法:
  python select_columns.py --input data.xlsx --columns 姓名,部门,金额 --output out.xlsx
  python select_columns.py --input data.xlsx --columns 姓名,部门,金额 --rename "部门:dept,金额:amount" --output out.xlsx
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="选择/重命名/排序列")
    parser.add_argument("--input", "-i", required=True, help="输入 .xlsx 文件")
    parser.add_argument("--output", "-o", required=True, help="输出 .xlsx 或 .csv")
    parser.add_argument("--sheet", default=0, help="工作表名或索引")
    parser.add_argument("--columns", "-c", required=True, help="保留的列名，按此顺序输出，逗号分隔")
    parser.add_argument("--rename", "-r", help="重命名 旧名:新名，多个用逗号分隔，如 部门:dept,金额:amount")
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

    cols = [c.strip() for c in args.columns.split(",") if c.strip()]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        print(f"列不存在: {missing}", file=sys.stderr)
        sys.exit(1)

    out_df = df[cols].copy()

    if args.rename:
        renames = {}
        for part in args.rename.split(","):
            part = part.strip()
            if ":" not in part:
                continue
            old_name, new_name = part.split(":", 1)
            old_name, new_name = old_name.strip(), new_name.strip()
            if old_name in out_df.columns:
                renames[old_name] = new_name
        out_df = out_df.rename(columns=renames)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".csv":
        out_df.to_csv(out, index=False, encoding=args.encoding)
    else:
        out_df.to_excel(out, index=False, engine="openpyxl")
    print(f"已输出 {len(out_df)} 行 x {len(out_df.columns)} 列 -> {out}")


if __name__ == "__main__":
    main()
