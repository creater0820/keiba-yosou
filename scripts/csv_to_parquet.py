"""
お父様から受領した CSV を、Streamlit Cloud で配信する Parquet に変換するスクリプト。

実行例:
    source .venv/bin/activate
    python scripts/csv_to_parquet.py

入力:
    data/raw/races*.csv      … TARGET frontier JV (JRA-VAN DataLab) のエクスポート
                               「メインメニュー(Z) → 開催成績CSV出力 → フルセット+単勝オッズ」
                               ヘッダーなし・Shift_JIS・52列の位置依存フォーマット
                               (JV-Data RA + SE + 単勝オッズ の結合形式)
    data/raw/horses*.csv     … 馬マスタ(列名ヘッダー付きCSV、現状未対応・スキップ)
    data/raw/pedigree*.csv   … 血統情報 (同上)

出力:
    data/historical/races.parquet
    data/historical/horses.parquet (入力があれば)
    data/historical/pedigree.parquet (入力があれば)

詳細手順は docs/UPDATE_DATA.md を参照。
"""

from __future__ import annotations

import sys
from pathlib import Path

# プロジェクトルートを import パスに追加(scripts/ から utils/ を読むため)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

# JV-Link 52列マッピング・パーサ・知名集合は utils/target_format.py に集約。
# data_loader.load_race_card もこのモジュールを再利用する。
from utils.target_format import (
    JV_LINK_EXPECTED_COLS,
    KNOWN_COURSES,
    parse_jra_van_dataframe,
)

# ===== パス設定 =====
RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/historical")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# =====================================================================
# horses / pedigree: 既存の列名ヘッダー付きCSV(rename map で正規化)
# =====================================================================
TABLE_SPECS: dict[str, dict] = {
    "horses": {
        "expected_columns": [
            "horse_id", "horse_name", "sex", "age", "sire", "dam", "dam_sire",
            "total_starts", "wins", "places", "shows",
        ],
        "dedup_keys": ["horse_id"],
        "rename": {
            "馬ID": "horse_id", "馬名": "horse_name", "性別": "sex", "年齢": "age",
            "父": "sire", "母": "dam", "母父": "dam_sire",
            "総出走数": "total_starts", "勝利数": "wins",
            "2着数": "places", "3着数": "shows",
        },
    },
    "pedigree": {
        "expected_columns": [
            "horse_id", "sire_line", "broodmare_sire_line", "inbreeding_score",
        ],
        "dedup_keys": ["horse_id"],
        "rename": {
            "馬ID": "horse_id", "父系": "sire_line",
            "母父系": "broodmare_sire_line", "近親交配スコア": "inbreeding_score",
        },
    },
}


# =====================================================================
# 共通ユーティリティ
# =====================================================================

def _list_csv(table_name: str) -> list[Path]:
    """data/raw/{table_name}*.csv を全部拾う(アンダースコア有無問わず)。"""
    return sorted(RAW_DIR.glob(f"{table_name}*.csv"))


# =====================================================================
# races コンバータ (JV-Link 位置依存パース部は utils/target_format に委譲)
# =====================================================================

def _read_jra_van_csv(path: Path) -> pd.DataFrame:
    """
    JV-Link RA+SE 結合CSVを生のまま読み込む。
    - 文字コード: Shift_JIS (cp932)
    - ヘッダーなし (header=None)
    - 全列文字列として読む(後段で個別に型変換)
    """
    return pd.read_csv(path, encoding="cp932", header=None, dtype=str, low_memory=False)


def _parse_races_jra_van(paths: list[Path]) -> pd.DataFrame:
    """
    JV-Link RA+SE+単勝オッズ CSV(複数可)を読み込み、
    CLAUDE.md スキーマの DataFrame に変換して返す。

    位置依存マッピング自体は utils/target_format.parse_jra_van_dataframe に
    集約されており、ここではファイル読み込み・列数チェック・結合のみ担当する。
    """
    frames: list[pd.DataFrame] = []
    for p in paths:
        df = _read_jra_van_csv(p)
        if df.shape[1] != JV_LINK_EXPECTED_COLS:
            print(f"  ⚠ {p.name}: {JV_LINK_EXPECTED_COLS}列を想定したが {df.shape[1]} 列でした → スキップ")
            continue
        print(f"  ✓ {p.name}: {len(df):,} 行")
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True)
    return parse_jra_van_dataframe(raw)


# =====================================================================
# 検証 & レポート
# =====================================================================

def _validate_races(df: pd.DataFrame) -> list[str]:
    """変換結果の品質チェック。失敗条件にヒットしたらメッセージリストを返す(空 = OK)。"""
    issues: list[str] = []
    n = len(df)
    if n == 0:
        return ["変換結果が0行(全行パース失敗)"]

    # race_date が日付パースできない行が10%超
    bad_date = df["race_date"].isna().sum() + (df["race_date"] == "").sum()
    if bad_date / n > 0.10:
        issues.append(
            f"race_date パース失敗が {bad_date:,}/{n:,} 行 ({bad_date/n:.1%}, 閾値10%)"
        )

    # distance が数値変換できない行が10%超
    bad_dist = df["distance"].isna().sum()
    if bad_dist / n > 0.10:
        issues.append(
            f"distance パース失敗が {bad_dist:,}/{n:,} 行 ({bad_dist/n:.1%}, 閾値10%)"
        )

    # finishing_position が 1〜18 の範囲外/欠損が30%超
    fp = df["finishing_position"]
    out_of_range = ((fp < 1) | (fp > 18) | fp.isna()).sum()
    if out_of_range / n > 0.30:
        issues.append(
            f"finishing_position 範囲外/欠損が {out_of_range:,}/{n:,} 行 ({out_of_range/n:.1%}, 閾値30%)"
        )

    # racecourse が知名以外が10%超
    bad_course = (~df["racecourse"].isin(KNOWN_COURSES)).sum()
    if bad_course / n > 0.10:
        unknown = df.loc[~df["racecourse"].isin(KNOWN_COURSES), "racecourse"].value_counts().head(5)
        issues.append(
            f"racecourse 知名外が {bad_course:,}/{n:,} 行 ({bad_course/n:.1%}, 閾値10%)\n"
            f"      頻出値: {dict(unknown)}"
        )

    return issues


def _print_sample(df: pd.DataFrame, n: int = 10) -> None:
    """変換結果サンプルを人間可読で出力。"""
    print(f"\n=== 変換結果サンプル(先頭 {n} 行)===")
    show_cols = [
        "race_date", "racecourse", "race_number", "race_name", "distance", "surface",
        "finishing_position", "horse_name", "jockey", "weight", "time", "last_3f", "odds",
    ]
    pd.set_option("display.max_colwidth", 14)
    pd.set_option("display.width", 200)
    print(df.head(n)[show_cols].to_string(index=False))


def _report_races(df: pd.DataFrame) -> None:
    """変換結果のレポート(レース数・期間・場別件数・null率)。"""
    print("\n" + "─" * 60)
    print("【races 変換結果レポート】")
    print(f"  総レース数(distinct race_id): {df['race_id'].nunique():,}")
    print(f"  総出走馬延べ数(行数)       : {len(df):,}")

    valid_dates = pd.to_datetime(df["race_date"], errors="coerce").dropna()
    if not valid_dates.empty:
        print(f"  期間                        : {valid_dates.min().date()}{valid_dates.max().date()}")

    print("\n  競馬場別レース数:")
    course_counts = (
        df.drop_duplicates("race_id").groupby("racecourse").size().sort_values(ascending=False)
    )
    for course, count in course_counts.items():
        marker = "  " if course in KNOWN_COURSES else "❓"
        print(f"    {marker} {course}: {count:,} レース")

    print("\n  各列の null/空 率:")
    for c in df.columns:
        if df[c].dtype == object:
            null_count = (df[c].astype(str).str.strip() == "").sum() + df[c].isna().sum()
        else:
            null_count = df[c].isna().sum()
        print(f"    {c:<22}: {null_count/len(df):.1%}")


# =====================================================================
# horses / pedigree コンバータ (既存の rename map 方式)
# =====================================================================

def _load_and_concat(paths: list[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for p in paths:
        try:
            df = pd.read_csv(p)
        except UnicodeDecodeError:
            print(f"  ⚠ {p.name}: UTF-8 で読めないため Shift_JIS で再試行")
            df = pd.read_csv(p, encoding="cp932")
        print(f"  ✓ {p.name}: {len(df):,} 行")
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _normalize_columns(df: pd.DataFrame, spec: dict) -> tuple[pd.DataFrame, list[str], list[str]]:
    df = df.rename(columns=spec["rename"])
    expected = set(spec["expected_columns"])
    actual = set(df.columns)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    keep_cols = [c for c in spec["expected_columns"] if c in df.columns]
    return df[keep_cols], missing, extra


# =====================================================================
# 各テーブルの変換エントリポイント
# =====================================================================

def convert_races() -> bool:
    """races の変換。成功で True、入力なしで False、検証失敗で sys.exit(1)。"""
    print("\n=== races (JV-Link 位置依存パース) ===")
    paths = _list_csv("races")
    if not paths:
        print(f"  ⏭  data/raw/races*.csv が見つかりません(ファイルなしスキップ)")
        return False
    print(f"対象ファイル: {len(paths)} 件")

    df = _parse_races_jra_van(paths)
    if df.empty:
        print("  ❌ パース可能なファイルがありませんでした")
        return False

    # サンプル出力(検証用)
    _print_sample(df, n=10)

    # 検証
    issues = _validate_races(df)
    if issues:
        print("\n❌ マッピング失敗(検証ルール違反):")
        for issue in issues:
            print(f"   - {issue}")
        print("\n変換結果を破棄します。スキーマ・列マッピングを見直してください。")
        sys.exit(1)

    # 重複除去 (race_id, horse_id)
    before = len(df)
    df = df.drop_duplicates(subset=["race_id", "horse_id"], keep="last")
    after = len(df)
    if before != after:
        print(f"\n  重複除去: {before - after:,} 行削除 → {after:,} 行")

    # Parquet 書き出し
    out_path = OUT_DIR / "races.parquet"
    df.to_parquet(out_path, index=False)
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"\n  ✅ {out_path} ({size_mb:.2f} MB, {len(df):,} 行)")

    # 詳細レポート
    _report_races(df)
    return True


def convert_table_with_rename(table_name: str) -> bool:
    """horses / pedigree など、列名ヘッダー付きCSV用の変換。"""
    print(f"\n=== {table_name} (列名ヘッダー方式) ===")
    paths = _list_csv(table_name)
    if not paths:
        print(f"  ⏭  data/raw/{table_name}*.csv が見つかりません(ファイルなしスキップ)")
        return False

    spec = TABLE_SPECS[table_name]
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

    before = len(df)
    df = df.drop_duplicates(subset=spec["dedup_keys"], keep="last")
    after = len(df)
    if before != after:
        print(f"  重複除去: {before - after:,} 行削除 → {after:,} 行")

    out_path = OUT_DIR / f"{table_name}.parquet"
    df.to_parquet(out_path, index=False)
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"  ✅ {out_path} ({size_mb:.2f} MB, {len(df):,} 行)")
    return True


# =====================================================================
# main
# =====================================================================

def main() -> None:
    if not RAW_DIR.exists():
        print(f"❌ {RAW_DIR}/ がありません。受領CSVをそこに置いてください。")
        sys.exit(1)

    print(f"入力: {RAW_DIR}/")
    print(f"出力: {OUT_DIR}/")

    results: dict[str, bool] = {
        "races": convert_races(),
        "horses": convert_table_with_rename("horses"),
        "pedigree": convert_table_with_rename("pedigree"),
    }

    print("\n" + "=" * 60)
    print("処理結果サマリ:")
    for table, ok in results.items():
        status = "変換成功" if ok else "ファイルなしスキップ"
        print(f"  - {table:<10}: {status}")

    converted = [t for t, ok in results.items() if ok]
    if converted:
        print("\n次のステップ:")
        print("  git checkout -b chore/update-historical-data-YYYYMM")
        print(f"  git add data/historical/{{{','.join(converted)}}}.parquet")
        print("  git commit -m 'chore: update historical data through YYYY-MM'")
        print("  git push -u origin chore/update-historical-data-YYYYMM")
        print("  → GitHub で PR を作成し main にマージ")
    else:
        print("\n変換対象なし(data/raw/ が空でした)")


if __name__ == "__main__":
    main()
