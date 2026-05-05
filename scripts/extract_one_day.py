"""
data/raw/races2023_2026.csv (TARGET frontier JV 形式) から特定日のレースだけ
抽出して、結果列を空にした「朝の出馬表」相当の CSV を生成するスクリプト。

実行例:
    python scripts/extract_one_day.py 2026-05-03
    python scripts/extract_one_day.py 2026-05-03 --keep-results

仕様:
- 入力: data/raw/races*.csv (Shift_JIS、ヘッダーなし、52列)
- 出力: data/test/morning_race_card_YYYYMMDD.csv
        既定では結果列(着順/タイム/上3F 等)を空文字でクリア
        --keep-results オプションで結果も含めて出力可能
- 出力エンコーディング: UTF-8-sig (BOM 付き)
  プロジェクト規約: 出力CSVは UTF-8-sig で統一(README「CSVエンコーディング規約」)。

朝時点で確定している列(維持):
    年月日 / 場 / レース名 / 距離 / 芝ダ / 馬場 / 馬名 / 性齢
    騎手 / 斤量 / 馬番 / 馬体重 / 単勝オッズ 等

クリアする列(レース結果由来):
    [20]/[21] 着順(及び複製)、[22-23] 着差・着差秒、
    [25-26] 走破タイム、[27-31] 時計指数・通過順、[32] 上がり3F
    ※ [19] = 馬番、[24] = 単勝人気 は朝時点で確定しているのでクリアしない。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# data/raw/ にある TARGET 形式 CSV(複数あれば結合)
RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/test")

# クリアする列インデックス(レース結果由来)
# [19] = 馬番、[24] = 単勝人気 は朝の出馬表に確定済 → クリアしない。
# 真の着順は [20]/[21]、レース後の値なのでクリア対象。
RESULT_COLS_TO_CLEAR = [20, 21, 22, 23, 25, 26, 27, 28, 29, 30, 31, 32]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("date", help="抽出対象日 (YYYY-MM-DD 形式、例: 2026-05-03)")
    parser.add_argument("--keep-results", action="store_true",
                        help="結果列もそのまま残す(過去レースの完全コピーが欲しい時用)")
    parser.add_argument("--source", type=Path, default=None,
                        help="入力 CSV パス(未指定時は data/raw/races*.csv を全部結合)")
    parser.add_argument("--out", type=Path, default=None,
                        help="出力先(未指定時は data/test/morning_race_card_YYYYMMDD.csv)")
    args = parser.parse_args()

    # 日付パース
    try:
        target = pd.Timestamp(args.date)
    except Exception:
        print(f"❌ 日付として解釈できません: {args.date}")
        sys.exit(1)
    yy = f"{target.year % 100:02d}"
    mm = f"{target.month:02d}"
    dd = f"{target.day:02d}"

    # 入力ファイル
    if args.source:
        sources = [args.source]
    else:
        sources = sorted(RAW_DIR.glob("races*.csv"))
    if not sources:
        print(f"❌ 入力CSVが見つかりません: {RAW_DIR}/races*.csv")
        sys.exit(1)

    # 全部読み込んで結合(Shift_JIS、ヘッダーなし、全文字列)
    print(f"入力: {len(sources)} ファイル")
    frames: list[pd.DataFrame] = []
    for p in sources:
        df = pd.read_csv(p, encoding="cp932", header=None, dtype=str, low_memory=False)
        print(f"  ✓ {p.name}: {len(df):,} 行")
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True)
    print(f"  結合後: {len(raw):,} 行")

    # 対象日でフィルタ
    yy_col = raw[0].fillna("").astype(str).str.strip().str.zfill(2)
    mm_col = raw[1].fillna("").astype(str).str.strip().str.zfill(2)
    dd_col = raw[2].fillna("").astype(str).str.strip().str.zfill(2)
    mask = (yy_col == yy) & (mm_col == mm) & (dd_col == dd)
    today = raw[mask].copy()

    if today.empty:
        print(f"❌ {args.date} のレースが見つかりませんでした")
        sys.exit(1)

    n_races = today.groupby([4, 6]).ngroups
    print(f"\n{args.date} の対象: {len(today):,} 行 / {n_races} レース")

    # 結果列クリア(--keep-results 指定なし時)
    if not args.keep_results:
        for c in RESULT_COLS_TO_CLEAR:
            today[c] = ""
        print(f"クリアした列: {RESULT_COLS_TO_CLEAR}")
    else:
        print("結果列はそのまま残します(--keep-results 指定)")

    # 出力先
    if args.out:
        out_path = args.out
    else:
        out_path = OUT_DIR / f"morning_race_card_{target.strftime('%Y%m%d')}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # UTF-8-sig で書き出し(BOM 付き、Excel 互換)
    today.to_csv(out_path, encoding="utf-8-sig", header=False, index=False)
    print(f"\n✅ 出力: {out_path} ({out_path.stat().st_size:,} bytes, UTF-8-sig)")


if __name__ == "__main__":
    main()
