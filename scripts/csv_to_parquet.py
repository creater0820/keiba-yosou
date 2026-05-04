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

import pandas as pd

# ===== パス設定 =====
RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/historical")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# =====================================================================
# races: TARGET frontier JV (JRA-VAN) RA+SE+単勝オッズ 結合CSV
#   - ヘッダー行なし
#   - 文字コード Shift_JIS (cp932)
#   - 52 列の位置依存フォーマット
# =====================================================================

RACES_JRA_VAN_EXPECTED_COLS = 52

# 列インデックス → 内部フィールド名
# (52列のうち本MVPで使う列のみ。それ以外は無視)
#
# 確定した位置(実データ確認済み):
#   [0-2]   年・月・日
#   [3]     開催回
#   [4]     競馬場(漢字)
#   [5]     開催日次
#   [6]     レース番号    ← race_number
#   [7]     レース名
#   [8]     出走頭数(本MVPでは未使用)
#   [9]     トラック種別(芝/ダ/障)
#   [10]    内/外
#   [11]    距離(m)
#   [12]    馬場状態
#   [13]    馬名
#   [14]    性別
#   [15]    年齢
#   [16]    騎手
#   [17]    斤量
#   [18]    馬番(枠番)
#   [19]    着順(2桁ゼロ埋め文字列、例 '01' = 1着)
#   [20-24] 馬番・着差・補正など
#   [25]    走破タイム(秒、例: 70.3 = 1分10秒3)  ← time_seconds
#   [26]    走破タイム(別表現、1103 = 1分10秒3)
#   [27-31] その他指数
#   [32]    上がり3F(秒)                          ← last_3f
#   [33]    馬体重(kg)                            ← weight
#   [34]    調教師                                ← trainer
#   [35]    厩舎所属(栗東/美浦)
#   [36-39] 各種指数・順位
#   [40]    馬登録番号(10桁)                      ← horse_id
#   [41]    馬主、 [42] 生産牧場
#   [43]    父、 [44] 母、 [45] 母父
#   [46]    毛色、 [47] 生年月日、 [48-50] その他
#   [51]    単勝オッズ
RACES_COL: dict[str, int] = {
    "year":                0,
    "month":               1,
    "day":                 2,
    "racecourse":          4,
    "race_number":         6,
    "race_name":           7,
    "surface":             9,
    "distance":           11,
    "going":              12,
    "horse_name":         13,
    "jockey":             16,
    "finishing_position": 19,
    "time_seconds":       25,
    "last_3f":            32,
    "weight":             33,
    "trainer":            34,
    "horse_id":           40,
    "sire":               43,
    "dam":                44,
    "dam_sire":           45,
    "odds":               51,
}

# 検証用の知名(JRA 中央10場)
KNOWN_COURSES = {
    "東京", "中山", "京都", "阪神", "小倉", "福島", "新潟", "函館", "札幌", "中京",
}

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


def _secs_to_time_str(secs) -> str:
    """秒数を 'M:SS.SS' 形式に変換(NaN は空文字)。"""
    if pd.isna(secs):
        return ""
    minutes = int(secs // 60)
    sec = secs - minutes * 60
    if minutes > 0:
        return f"{minutes}:{sec:05.2f}"
    return f"{sec:.2f}"


def _to_nullable_int(s: pd.Series) -> pd.Series:
    """文字列Series → Int64(欠損は <NA>、小数値は四捨五入)。"""
    f = pd.to_numeric(s, errors="coerce")
    return f.round().astype("Int64")


# =====================================================================
# races コンバータ (JV-Link 位置依存)
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
    """
    frames: list[pd.DataFrame] = []
    for p in paths:
        df = _read_jra_van_csv(p)
        if df.shape[1] != RACES_JRA_VAN_EXPECTED_COLS:
            print(f"  ⚠ {p.name}: {RACES_JRA_VAN_EXPECTED_COLS}列を想定したが {df.shape[1]} 列でした → スキップ")
            continue
        print(f"  ✓ {p.name}: {len(df):,} 行")
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True)

    # 列名インデックスから値を取り出すヘルパ(strip 込み)
    def col(name: str) -> pd.Series:
        return raw[RACES_COL[name]].fillna("").astype(str).str.strip()

    # ----- 日付組み立て(年は 20xx 想定) -----
    yy = col("year").str.zfill(2)
    mm = col("month").str.zfill(2)
    dd = col("day").str.zfill(2)
    race_date = pd.to_datetime("20" + yy + "-" + mm + "-" + dd, format="%Y-%m-%d", errors="coerce")

    # ----- 基本列 -----
    racecourse = col("racecourse")
    race_number = _to_nullable_int(col("race_number"))

    # race_id: "R" + yyyymmdd + "-" + 場頭文字 + zfill2(R)
    # 例: R20230722-札01
    race_id = (
        "R"
        + race_date.dt.strftime("%Y%m%d").fillna("00000000")
        + "-"
        + racecourse.str[:1]
        + race_number.astype("string").str.zfill(2)
    )

    # 走破タイム: 秒数(70.3)→ "1:10.30"
    time_secs = pd.to_numeric(col("time_seconds"), errors="coerce")
    time_str = time_secs.apply(_secs_to_time_str)

    # 馬登録番号: 10桁にゼロ埋め(JRA-VAN は10桁が標準)
    horse_id = col("horse_id").str.zfill(10)

    # 馬体重: 単位に "kg" などが混入することがあるので数値だけ抜く
    weight = _to_nullable_int(col("weight"))

    df = pd.DataFrame({
        "race_id": race_id,
        "race_date": race_date.dt.strftime("%Y-%m-%d"),
        "racecourse": racecourse,
        "race_number": race_number,
        "race_name": col("race_name"),
        "distance": _to_nullable_int(col("distance")),
        "surface": col("surface"),
        "going": col("going"),
        "finishing_position": _to_nullable_int(col("finishing_position")),
        "horse_id": horse_id,
        "horse_name": col("horse_name"),
        "jockey": col("jockey"),
        "trainer": col("trainer"),
        "weight": weight,
        # weight_change は元データに無いので 0 固定
        "weight_change": 0,
        "time": time_str,
        "last_3f": pd.to_numeric(col("last_3f"), errors="coerce"),
        # popularity は元データに無いので NaN
        "popularity": pd.Series([pd.NA] * len(raw), dtype="Int64"),
        "odds": pd.to_numeric(col("odds"), errors="coerce"),
    })
    return df


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
