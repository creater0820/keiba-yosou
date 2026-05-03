"""
データ読み込みモジュール。

責務:
1. 過去データ(races / horses / pedigree)を読み込む
   - 本番: data/historical/*.parquet
   - 開発: 上が無ければ data/samples/sample_historical/*.csv にフォールバック
2. お父様がアップロードした当日出馬表 CSV を pandas.DataFrame に変換
3. 当日出馬表の列が想定通りか日本語でバリデーション

Streamlit の app.py からのみ呼ばれる想定。UI には依存しない(疎結合)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import IO

import pandas as pd

# ===== 過去データ読み込み =====

# 本番データ(リポジトリ同梱、Parquet形式)の置き場
HISTORICAL_DIR = Path("data/historical")
# 開発時のフォールバック(CSV形式の縮小版)
SAMPLE_HISTORICAL_DIR = Path("data/samples/sample_historical")

# 過去データの3テーブル名
HISTORICAL_TABLES = ("races", "horses", "pedigree")


@dataclass
class HistoricalData:
    """過去データ3テーブルをまとめて持つ簡易コンテナ。"""
    races: pd.DataFrame      # 過去レース結果(行=馬1頭の1出走)
    horses: pd.DataFrame     # 過去登録馬(行=馬1頭)
    pedigree: pd.DataFrame   # 血統情報(行=馬1頭)
    source: str              # "parquet" または "csv_sample" のいずれか(UI表示用)


def load_historical_data() -> HistoricalData:
    """
    過去データを読み込む。

    Parquet (本番) → CSV サンプル (開発) の順に試し、見つかったほうを返す。
    どちらも無ければ FileNotFoundError を送出する。
    """
    # まず本番Parquetが3つ揃っているかチェック
    parquet_paths = {name: HISTORICAL_DIR / f"{name}.parquet" for name in HISTORICAL_TABLES}
    if all(p.exists() for p in parquet_paths.values()):
        return HistoricalData(
            races=pd.read_parquet(parquet_paths["races"]),
            horses=pd.read_parquet(parquet_paths["horses"]),
            pedigree=pd.read_parquet(parquet_paths["pedigree"]),
            source="parquet",
        )

    # フォールバック: 開発用サンプルCSV
    csv_paths = {name: SAMPLE_HISTORICAL_DIR / f"{name}.csv" for name in HISTORICAL_TABLES}
    if all(p.exists() for p in csv_paths.values()):
        return HistoricalData(
            races=pd.read_csv(csv_paths["races"]),
            horses=pd.read_csv(csv_paths["horses"]),
            pedigree=pd.read_csv(csv_paths["pedigree"]),
            source="csv_sample",
        )

    raise FileNotFoundError(
        "過去データが見つかりません。"
        f" {HISTORICAL_DIR}/ に Parquet を配置するか、"
        f" {SAMPLE_HISTORICAL_DIR}/ にサンプルCSVを配置してください。"
    )


# ===== 当日出馬表(アップロード CSV) =====

# 当日出馬表 CSV に必須の列(これが揃っていないと予想ロジックが回らない)
REQUIRED_RACE_CARD_COLUMNS: tuple[str, ...] = (
    "race_id", "race_date", "racecourse", "race_number", "race_name",
    "distance", "surface", "going",
    "horse_id", "horse_name", "jockey", "trainer",
    "weight", "weight_change", "popularity", "odds",
)


def load_race_card(uploaded_file: IO | str | Path) -> pd.DataFrame:
    """
    アップロードされた当日出馬表 CSV を DataFrame に変換する。

    Streamlit の `st.file_uploader` が返すオブジェクトは
    file-like (read可能) なのでそのまま `pd.read_csv` に渡せる。
    パス文字列・Pathも受け付ける(動作確認・テスト用)。

    エンコーディングは UTF-8 を想定。Shift_JIS 出力の TARGET エクスポートに
    ぶつかった場合は将来 chardet で自動判別を入れる(MVP では UTF-8 固定)。
    """
    # 同じファイルオブジェクトを2度読み込むケースに備えてシークを試みる
    if hasattr(uploaded_file, "seek"):
        try:
            uploaded_file.seek(0)
        except Exception:
            # SpooledTemporaryFile などで失敗するケースは無視(初回読み込みなら問題ない)
            pass
    return pd.read_csv(uploaded_file)


@dataclass
class ValidationResult:
    """出馬表バリデーション結果。UI 側でメッセージ表示するため日本語で詰める。"""
    ok: bool
    missing_columns: list[str]    # 不足している列名
    extra_columns: list[str]      # 想定外の余分な列(参考情報、エラーにはしない)
    message: str                  # 画面表示用の日本語メッセージ


def validate_race_card(df: pd.DataFrame) -> ValidationResult:
    """
    出馬表の列構成をチェック。
    必須列が揃っていれば ok=True。不足があれば不足列名と期待列一覧を日本語で返す。
    """
    actual = set(df.columns)
    expected = set(REQUIRED_RACE_CARD_COLUMNS)

    missing = sorted(expected - actual)
    extra = sorted(actual - expected)

    if missing:
        msg = (
            "列名が想定と異なります。\n"
            f"不足している列: {', '.join(missing)}\n"
            f"想定される列(順不同): {', '.join(REQUIRED_RACE_CARD_COLUMNS)}"
        )
        return ValidationResult(ok=False, missing_columns=missing, extra_columns=extra, message=msg)

    msg = "列構成OK。" + (f"想定外の追加列: {', '.join(extra)}" if extra else "")
    return ValidationResult(ok=True, missing_columns=[], extra_columns=extra, message=msg)


# ===== 集計ヘルパ(UI でファイルプレビュー表示するため) =====

def summarize_race_card(df: pd.DataFrame) -> dict[str, int]:
    """
    出馬表の概要(レース数・出走馬数)を返す。UI のメトリクス表示用。
    """
    return {
        "race_count": int(df["race_id"].nunique()) if "race_id" in df.columns else 0,
        "horse_count": int(len(df)),
    }
