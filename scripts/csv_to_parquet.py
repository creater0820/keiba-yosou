"""
お父様から受領した CSV を、Streamlit Cloud で配信する Parquet に変換するスクリプト。

実行例:
    source .venv/bin/activate
    python scripts/csv_to_parquet.py

入力:
    data/raw/races_*.csv      … 過去レース結果(複数月分まとめて置いて可)
    data/raw/horses_*.csv     … 馬マスタ
    data/raw/pedigree_*.csv   … 血統情報

出力:
    data/historical/races.parquet
    data/historical/horses.parquet
    data/historical/pedigree.parquet

詳細手順は docs/UPDATE_DATA.md を参照。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# ===== パス設定 =====
RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/historical")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ===== 各テーブルの仕様 =====
# 想定列・主キー・受領CSVに頻出するゆらぎ列名のリネーム表をテーブル単位で持つ。
# 受領CSVの列名規則が変わったらここに追記すれば対応できる。

TABLE_SPECS: dict[str, dict] = {
    "races": {
        "expected_columns": [
            "race_id", "race_date", "racecourse", "race_number", "race_name",
            "distance", "surface", "going", "finishing_position", "horse_id",
            "horse_name", "jockey", "trainer", "weight", "weight_change",
            "time", "last_3f", "popularity", "odds",
        ],
        # 主キー(重複行の判定に使う)
        "dedup_keys": ["race_id", "horse_id"],
        # 受領CSVでよくあるゆらぎ列名 → 想定列名 のマップ
        "rename": {
            "レースID": "race_id",
            "開催日": "race_date",
            "競馬場": "racecourse",
            "レース番号": "race_number",
            "レース名": "race_name",
            "距離": "distance",
            "馬場": "surface",
            "馬場状態": "going",
            "着順": "finishing_position",
            "馬ID": "horse_id",
            "馬名": "horse_name",
            "騎手": "jockey",
            "調教師": "trainer",
            "馬体重": "weight",
            "増減": "weight_change",
            "タイム": "time",
            "上がり3F": "last_3f",
            "人気": "popularity",
            "オッズ": "odds",
        },
    },
    "horses": {
        "expected_columns": [
            "horse_id", "horse_name", "sex", "age", "sire", "dam", "dam_sire",
            "total_starts", "wins", "places", "shows",
        ],
        "dedup_keys": ["horse_id"],
        "rename": {
            "馬ID": "horse_id",
            "馬名": "horse_name",
            "性別": "sex",
            "年齢": "age",
            "父": "sire",
            "母": "dam",
            "母父": "dam_sire",
            "総出走数": "total_starts",
            "勝利数": "wins",
            "2着数": "places",
            "3着数": "shows",
        },
    },
    "pedigree": {
        "expected_columns": [
            "horse_id", "sire_line", "broodmare_sire_line", "inbreeding_score",
        ],
        "dedup_keys": ["horse_id"],
        "rename": {
            "馬ID": "horse_id",
            "父系": "sire_line",
            "母父系": "broodmare_sire_line",
            "近親交配スコア": "inbreeding_score",
        },
    },
}


# ===== 共通ユーティリティ =====

def _list_csv(table_name: str) -> list[Path]:
    """data/raw/{table_name}_*.csv を全部拾う。"""
    return sorted(RAW_DIR.glob(f"{table_name}_*.csv"))


def _load_and_concat(paths: list[Path]) -> pd.DataFrame:
    """複数のCSVを読み込んで縦結合。"""
    frames: list[pd.DataFrame] = []
    for p in paths:
        # 文字コードは UTF-8 を想定。Shift_JIS の場合は encoding='cp932' を試す
        try:
            df = pd.read_csv(p)
        except UnicodeDecodeError:
            print(f"  ⚠ {p.name}: UTF-8 で読めないため Shift_JIS で再試行")
            df = pd.read_csv(p, encoding="cp932")
        print(f"  ✓ {p.name}: {len(df):,} 行")
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _normalize_columns(df: pd.DataFrame, spec: dict) -> tuple[pd.DataFrame, list[str], list[str]]:
    """
    列名を rename 表で正規化。
    返り値: (正規化後DataFrame, 不足している必須列, 想定外の余分列)
    """
    df = df.rename(columns=spec["rename"])

    expected = set(spec["expected_columns"])
    actual = set(df.columns)

    missing = sorted(expected - actual)
    extra = sorted(actual - expected)

    # 必須列は揃っている前提で、想定列だけに絞る(extra は捨てる)
    keep_cols = [c for c in spec["expected_columns"] if c in df.columns]
    return df[keep_cols], missing, extra


# ===== メイン処理 =====

def convert_table(table_name: str) -> bool:
    """
    指定テーブルを raw → parquet に変換。
    成功したら True、入力CSVが0件なら False。
    """
    spec = TABLE_SPECS[table_name]
    print(f"\n=== {table_name} ===")

    paths = _list_csv(table_name)
    if not paths:
        print(f"  ⚠ data/raw/{table_name}_*.csv が見つかりません(スキップ)")
        return False

    print(f"対象ファイル: {len(paths)} 件")
    df = _load_and_concat(paths)
    print(f"結合後: {len(df):,} 行")

    df, missing, extra = _normalize_columns(df, spec)

    if missing:
        print(f"  ❌ 必須列が不足: {missing}")
        print(f"     想定列: {spec['expected_columns']}")
        sys.exit(1)
    if extra:
        print(f"  ℹ️  想定外の列(無視します): {extra}")

    # 重複除去
    before = len(df)
    df = df.drop_duplicates(subset=spec["dedup_keys"], keep="last")
    after = len(df)
    if before != after:
        print(f"  重複除去: {before - after:,} 行を削除 → {after:,} 行")

    # Parquet 書き出し(snappy 圧縮、デフォルト)
    out_path = OUT_DIR / f"{table_name}.parquet"
    df.to_parquet(out_path, index=False)
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"  ✅ {out_path} ({size_mb:.2f} MB, {len(df):,} 行)")

    return True


def main() -> None:
    if not RAW_DIR.exists():
        print(f"❌ {RAW_DIR}/ がありません。受領CSVをそこに置いてください。")
        sys.exit(1)

    print(f"入力: {RAW_DIR}/")
    print(f"出力: {OUT_DIR}/")

    converted = []
    for table in TABLE_SPECS.keys():
        if convert_table(table):
            converted.append(table)

    print("\n" + "=" * 50)
    if converted:
        print(f"変換完了: {', '.join(converted)}")
        print("\n次のステップ:")
        print("  git checkout -b chore/update-historical-data-YYYYMM")
        print("  git add -f data/historical/*.parquet")
        print("  git commit -m 'chore: update historical data through YYYY-MM'")
        print("  git push -u origin chore/update-historical-data-YYYYMM")
        print("  → GitHub で PR を作成し main にマージ")
    else:
        print("変換対象なし(data/raw/ が空でした)")


if __name__ == "__main__":
    main()
