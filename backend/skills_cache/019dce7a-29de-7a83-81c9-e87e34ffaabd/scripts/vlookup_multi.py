#!/usr/bin/env python3
"""
多表 VLOOKUP：主表依次与多个查找表按键列左连接，每次合并追加新列。
用法:
  python vlookup_multi.py --main 订单.xlsx --lookups "客户.xlsx:客户ID" "产品.xlsx:产品ID" --output out.xlsx
  python vlookup_multi.py --main main.xlsx --lookups "a.xlsx:编号" "b.xlsx:ID" --sheet-main 0 --output out.xlsx
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="多表按键依次左连接（VLOOKUP 链）")
    parser.add_argument("--main", "-m", required=True, help="主表 .xlsx")
    parser.add_argument("--lookups", "-l", required=True, nargs="+", help="查找表 文件:键列名，如 ref.xlsx:ID，可多个")
    parser.add_argument("--output", "-o", required=True, help="输出 .xlsx 或 .csv")
    parser.add_argument("--sheet-main", default=0, help="主表 sheet 名或索引")
    parser.add_argument("--suffix", default="_lookup", help="追加列与主表重名时的后缀")
    args = parser.parse_args()

    mpath = Path(args.main)
    if not mpath.exists():
        print(f"主表不存在: {mpath}", file=sys.stderr)
        sys.exit(1)

    try:
        df = pd.read_excel(mpath, sheet_name=args.sheet_main, engine="openpyxl")
    except Exception as e:
        print(f"读取主表失败: {e}", file=sys.stderr)
        sys.exit(1)

    for spec in args.lookups:
        if ":" not in spec:
            print(f"无效 lookups 项（需 文件:键列）: {spec}", file=sys.stderr)
            sys.exit(1)
        fpath, on_col = spec.split(":", 1)
        fpath, on_col = fpath.strip(), on_col.strip()
        lpath = Path(fpath)
        if not lpath.exists():
            lpath = (mpath.parent / fpath).resolve()
        if not lpath.exists():
            print(f"查找表不存在: {fpath}", file=sys.stderr)
            sys.exit(1)
        try:
            right = pd.read_excel(lpath, sheet_name=0, engine="openpyxl")
        except Exception as e:
            print(f"读取查找表失败 {fpath}: {e}", file=sys.stderr)
            sys.exit(1)
        if on_col not in df.columns:
            print(f"主表缺少键列: {on_col}", file=sys.stderr)
            sys.exit(1)
        if on_col not in right.columns:
            print(f"查找表 {fpath} 缺少键列: {on_col}", file=sys.stderr)
            sys.exit(1)
        right_cols = [c for c in right.columns if c != on_col]
        if not right_cols:
            continue
        dup = [c for c in right_cols if c in df.columns]
        if dup:
            right = right.rename(columns={c: c + args.suffix for c in dup})
            right_cols = [c + args.suffix if c in dup else c for c in right_cols]
        df = pd.merge(df, right, on=on_col, how="left", suffixes=("", "_drop"))
        drop_cols = [c for c in df.columns if c.endswith("_drop")]
        df = df.drop(columns=drop_cols, errors="ignore")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".csv":
        df.to_csv(out, index=False, encoding="utf-8-sig")
    else:
        df.to_excel(out, index=False, engine="openpyxl")
    print(f"已合并 {len(args.lookups)} 个查找表，共 {len(df)} 行 x {len(df.columns)} 列 -> {out}")


if __name__ == "__main__":
    main()
