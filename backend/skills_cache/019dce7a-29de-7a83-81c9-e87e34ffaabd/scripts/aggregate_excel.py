#!/usr/bin/env python3
"""
按列分组并聚合（sum/count/mean/min/max），输出到新 Excel。
用法:
  python aggregate_excel.py --input data.xlsx --group-by 地区 --agg "销售额:sum,数量:sum"
  python aggregate_excel.py --input data.xlsx --group-by 部门,月份 --agg "金额:sum" "人数:count" --output out.xlsx
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

AGG_FUNCS = {"sum": "sum", "count": "count", "mean": "mean", "min": "min", "max": "max"}


def main():
    parser = argparse.ArgumentParser(description="按列分组聚合")
    parser.add_argument("--input", "-i", required=True, help="输入 .xlsx 文件")
    parser.add_argument("--output", "-o", help="输出 .xlsx，默认覆盖输入并加 _agg 后缀")
    parser.add_argument("--sheet", default=0, help="工作表名或索引")
    parser.add_argument("--group-by", "-g", required=True, help="分组列，逗号分隔")
    parser.add_argument("--agg", "-a", action="append", required=True, help="聚合：列名:sum 或 列名:count 等，可多次或逗号分隔")
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

    group_cols = [c.strip() for c in args.group_by.split(",") if c.strip()]
    missing = [c for c in group_cols if c not in df.columns]
    if missing:
        print(f"分组列不存在: {missing}", file=sys.stderr)
        sys.exit(1)

    agg_spec = {}
    for item in args.agg:
        for part in item.split(","):
            part = part.strip()
            if ":" not in part:
                continue
            col, func = part.split(":", 1)
            col, func = col.strip(), func.strip().lower()
            if col not in df.columns:
                print(f"聚合列不存在: {col}", file=sys.stderr)
                sys.exit(1)
            if func not in AGG_FUNCS:
                print(f"不支持的聚合: {func}，可选 sum/count/mean/min/max", file=sys.stderr)
                sys.exit(1)
            agg_spec[col] = AGG_FUNCS[func]

    if not agg_spec:
        print("请至少指定一个 --agg 列:函数", file=sys.stderr)
        sys.exit(1)

    result = df.groupby(group_cols, dropna=False).agg(agg_spec).reset_index()

    if args.output:
        out = Path(args.output)
    else:
        out = path.parent / f"{path.stem}_agg.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_excel(out, index=False, engine="openpyxl")
    print(f"聚合完成，{len(result)} 行 -> {out}")


if __name__ == "__main__":
    main()
