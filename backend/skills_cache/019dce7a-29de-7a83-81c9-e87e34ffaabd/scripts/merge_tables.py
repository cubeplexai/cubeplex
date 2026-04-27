#!/usr/bin/env python3
"""
两表按键列合并（类似 VLOOKUP）：左表为主，右表按 key 列匹配并追加列。
用法:
  python merge_tables.py --left left.xlsx --right right.xlsx --on 编号 --output out.xlsx
  python merge_tables.py --left left.xlsx --right right.xlsx --on 编号 --how inner --output out.xlsx
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="两表按键列合并（VLOOKUP 式）")
    parser.add_argument("--left", "-l", required=True, help="左表 .xlsx（主表）")
    parser.add_argument("--right", "-r", required=True, help="右表 .xlsx（被查表）")
    parser.add_argument("--on", required=True, help="键列名（两表共有），逗号分隔为多键")
    parser.add_argument("--output", "-o", required=True, help="输出 .xlsx 或 .csv")
    parser.add_argument("--sheet-left", default=0, help="左表 sheet 名或索引")
    parser.add_argument("--sheet-right", default=0, help="右表 sheet 名或索引")
    parser.add_argument("--how", choices=("left", "inner", "outer"), default="left", help="合并方式：left 保留左表全部，inner 交集，outer 并集")
    parser.add_argument("--suffixes", default="_left,_right", help="重名列后缀，逗号分隔")
    args = parser.parse_args()

    lpath = Path(args.left)
    rpath = Path(args.right)
    if not lpath.exists():
        print(f"左表不存在: {lpath}", file=sys.stderr)
        sys.exit(1)
    if not rpath.exists():
        print(f"右表不存在: {rpath}", file=sys.stderr)
        sys.exit(1)

    try:
        left = pd.read_excel(lpath, sheet_name=args.sheet_left, engine="openpyxl")
        right = pd.read_excel(rpath, sheet_name=args.sheet_right, engine="openpyxl")
    except Exception as e:
        print(f"读取失败: {e}", file=sys.stderr)
        sys.exit(1)

    keys = [k.strip() for k in args.on.split(",") if k.strip()]
    for k in keys:
        if k not in left.columns:
            print(f"左表缺少键列: {k}", file=sys.stderr)
            sys.exit(1)
        if k not in right.columns:
            print(f"右表缺少键列: {k}", file=sys.stderr)
            sys.exit(1)

    suffixes = [s.strip() for s in args.suffixes.split(",")]
    if len(suffixes) != 2:
        suffixes = ["_left", "_right"]
    merged = pd.merge(left, right, on=keys, how=args.how, suffixes=suffixes)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".csv":
        merged.to_csv(out, index=False, encoding="utf-8-sig")
    else:
        merged.to_excel(out, index=False, engine="openpyxl")
    print(f"合并完成: 左 {len(left)} 行 + 右 {len(right)} 行 -> {len(merged)} 行 -> {out}")


if __name__ == "__main__":
    main()
