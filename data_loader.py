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

import io
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import pandas as pd

from utils.target_format import (
    decode_with_fallback,
    is_jra_van_headerless,
    parse_jra_van_dataframe,
)

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
    # 各テーブルのデータ出所を独立に持つ。
    # 例: {"races": "parquet", "horses": "csv_sample", "pedigree": "csv_sample"}
    sources: dict[str, str]


def _load_one_table(table_name: str) -> tuple[pd.DataFrame, str]:
    """
    1テーブル分を読み込む。
    本番Parquet (data/historical/{name}.parquet) があればそれを、
    無ければサンプルCSV (data/samples/sample_historical/{name}.csv) を返す。
    どちらも無ければ FileNotFoundError。
    """
    parquet_path = HISTORICAL_DIR / f"{table_name}.parquet"
    csv_path = SAMPLE_HISTORICAL_DIR / f"{table_name}.csv"

    if parquet_path.exists():
        return pd.read_parquet(parquet_path), "parquet"
    if csv_path.exists():
        return pd.read_csv(csv_path), "csv_sample"

    raise FileNotFoundError(
        f"{table_name} のデータが見つかりません。"
        f" {parquet_path} または {csv_path} のいずれかを配置してください。"
    )


def load_historical_data() -> HistoricalData:
    """
    過去データを読み込む。テーブルごとに Parquet / CSV を独立に選択する。
    例えば races のみ本番Parquet、horses/pedigree はサンプルCSV、という混在運用が可能。
    """
    tables: dict[str, pd.DataFrame] = {}
    sources: dict[str, str] = {}
    for name in HISTORICAL_TABLES:
        df, src = _load_one_table(name)
        tables[name] = df
        sources[name] = src

    return HistoricalData(
        races=tables["races"],
        horses=tables["horses"],
        pedigree=tables["pedigree"],
        sources=sources,
    )


# ===== 当日出馬表(アップロード CSV) =====

# 当日出馬表 CSV に必須の列(これが揃っていないと予想ロジックが回らない)
REQUIRED_RACE_CARD_COLUMNS: tuple[str, ...] = (
    "race_id", "race_date", "racecourse", "race_number", "race_name",
    "distance", "surface", "going",
    "horse_id", "horse_name", "jockey", "trainer",
    "weight", "weight_change", "popularity", "odds",
)


def _read_raw_bytes(uploaded_file: IO | str | Path) -> bytes:
    """
    `uploaded_file` を bytes に正規化する。

    Streamlit の `st.file_uploader` 戻り値・通常の file-like・パス文字列・Path
    のいずれにも対応する(後段でエンコーディング自動判定するため、テキストでは
    なく必ず bytes で取り出す)。
    """
    # file-like ならまずシーク(2度目の read に備える)
    if hasattr(uploaded_file, "seek"):
        try:
            uploaded_file.seek(0)
        except Exception:
            # SpooledTemporaryFile 等で失敗しても初回 read なら無視可能
            pass

    if hasattr(uploaded_file, "read"):
        data = uploaded_file.read()
        # まれに既に str になっているケースがあるので bytes に揃える
        return data if isinstance(data, bytes) else data.encode("utf-8")

    return Path(uploaded_file).read_bytes()


def load_race_card(uploaded_file: IO | str | Path) -> pd.DataFrame:
    """
    アップロードされた当日出馬表 CSV を DataFrame に変換する。

    対応する形式:
    1. ヘッダー付き普通CSV (列名が race_id, race_date, ... 等の英名)
       → そのまま pd.read_csv で読む。
    2. TARGET frontier JV (JRA-VAN) の RA+SE+単勝オッズ 結合 CSV
       (52列・ヘッダーなし・Shift_JIS が典型)
       → 位置依存マッピングで内部スキーマに変換。

    エンコーディングは utf-8-sig → utf-8 → shift_jis → cp932 の順で試行する。
    全部失敗したら ValueError(日本語メッセージ)を送出。
    """
    raw_bytes = _read_raw_bytes(uploaded_file)

    # 1) エンコーディング自動判定
    try:
        text, _encoding = decode_with_fallback(raw_bytes)
    except UnicodeDecodeError as e:
        raise ValueError(
            "CSVの文字コードを判定できませんでした。"
            "UTF-8 / Shift_JIS / cp932 のいずれかで保存し直してください。"
        ) from e

    # 2) TARGET 52列ヘッダーなし形式かどうかを1行目で判定
    if is_jra_van_headerless(text):
        # ヘッダーなし → 全列文字列で読み込み、内部スキーマへ変換
        raw_df = pd.read_csv(
            io.StringIO(text),
            header=None,
            dtype=str,
            low_memory=False,
        )
        return parse_jra_van_dataframe(raw_df)

    # 3) ヘッダー付き普通CSV(既存サンプルや日本語列名 CSV はこのパス)
    return pd.read_csv(io.StringIO(text))


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
