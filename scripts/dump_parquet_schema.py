"""v1.10.0 Step 1: 既存 data/historical/races.parquet のスキーマ完全ダンプ。

過去 parquet 最新化パイプライン構築の前提として、現状の parquet 構造
(行数 / 期間 / 列ごとの dtype / null 率 / サンプル値)を正確に把握する。

ここで取得した情報は CLAUDE.md v1.10.0 セクションに転記し、新規パーサーが
出力すべきスキーマの「契約」として参照する。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

HISTORICAL = ROOT / "data" / "historical" / "races.parquet"


def main():
    print("=" * 72)
    print("v1.10.0 Step 1: 既存 races.parquet スキーマ完全ダンプ")
    print(f"  パス: {HISTORICAL}")
    print("=" * 72)

    if not HISTORICAL.exists():
        print("ERROR: parquet が見つかりません。")
        sys.exit(1)

    hist = pd.read_parquet(HISTORICAL)

    print(f"\n【基本統計】")
    print(f"  行数              : {len(hist):,}")
    print(f"  ユニーク race_id  : {hist['race_id'].nunique():,}")
    print(f"  ユニーク horse_id : {hist['horse_id'].nunique():,}")
    print(f"  ユニーク racecourse: {hist['racecourse'].nunique() if 'racecourse' in hist.columns else 'N/A'}")
    print(f"  race_date 範囲    : {hist['race_date'].min()} 〜 {hist['race_date'].max()}")
    print(f"  列数              : {len(hist.columns)}")
    print(f"  file_size         : {HISTORICAL.stat().st_size / 1024 / 1024:.2f} MB")

    # 月別カウント(直近の更新有無を確認)
    if "race_date" in hist.columns:
        try:
            dates = pd.to_datetime(hist["race_date"], errors="coerce")
            monthly = dates.dt.to_period("M").value_counts().sort_index()
            print("\n【月別レース行数(直近 12 ヶ月)】")
            for period, n in monthly.tail(12).items():
                print(f"  {period}: {n:,} 行")
        except Exception as e:
            print(f"\n  月別集計失敗: {e}")

    print("\n【列ごとの詳細】")
    print(f"  {'列名':<22} {'dtype':<12} {'null率':>8}  サンプル値(直近 3 件)")
    print("  " + "-" * 86)
    for col in hist.columns:
        dt = str(hist[col].dtype)
        null_rate = hist[col].isna().mean() * 100
        samples = hist[col].dropna().head(3).tolist()
        # 表示用に長すぎる値を切る
        sample_repr = repr(samples)[:48]
        print(f"  {col:<22} {dt:<12} {null_rate:>6.2f}%  {sample_repr}")

    # 重複キーの確認(race_id + horse_id がユニークかどうか — 取り込み時の
    # 重複排除キー検証)
    print("\n【重複キー検証】")
    dup_count = hist.duplicated(subset=["race_id", "horse_id"]).sum()
    print(f"  (race_id, horse_id) 重複: {dup_count:,} 件")
    if dup_count > 0:
        print("  ⚠ 既存 parquet に既に重複あり。取り込み時の dedup でこれも除去される。")
    else:
        print("  ✓ (race_id, horse_id) は完全ユニーク。複合キーで dedup 可能。")

    # horse_id のフォーマット(桁数分布)
    print("\n【horse_id フォーマット】")
    hid_str = hist["horse_id"].astype(str)
    length_counts = hid_str.str.len().value_counts().sort_index()
    print(f"  桁数分布: {dict(length_counts)}")
    print(f"  最初 5 件: {hid_str.head(5).tolist()}")
    print(f"  最後 5 件: {hid_str.tail(5).tolist()}")
    print(f"  全て数字のみ: {hid_str.str.match(r'^\\d+$').all()}")

    # race_id のフォーマット
    print("\n【race_id フォーマット】")
    rid_str = hist["race_id"].astype(str)
    print(f"  桁数分布: {dict(rid_str.str.len().value_counts().sort_index())}")
    print(f"  サンプル: {rid_str.head(5).tolist()}")

    # racecourse の値一覧(JRA 10 場所か確認)
    print("\n【racecourse 値一覧】")
    if "racecourse" in hist.columns:
        print(f"  {sorted(hist['racecourse'].dropna().unique().tolist())}")

    # surface / going の値一覧
    if "surface" in hist.columns:
        print(f"\n【surface 値一覧】 {sorted(hist['surface'].dropna().unique().tolist())}")
    if "going" in hist.columns:
        print(f"【going 値一覧】 {sorted(hist['going'].dropna().unique().tolist())}")


if __name__ == "__main__":
    main()
