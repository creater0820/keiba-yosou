"""
坂路調教 CSV(JV-Link / TARGET 等から出力した日次 CSV)のパーサ +
当日出馬表とのマッチ + F4 / F5 ルール評価ヘルパ。

v1.5 で導入。それまで rating_rules.py で「TODO: データ未取得」として
永続無効化されていた F4 / F5 を実発火可能にする。

ファイル形式(実機サンプル DC260509 / 坂路_20260509.csv で確認):
- エンコーディング: Shift_JIS(cp932 fallback)
- ヘッダー行あり、18 列
- 列: 場所, 年月日, 曜日, 時刻, 馬名, Ｃ, 性別, 年齢, 収得賞金,
      調教師, Time1, Time2, Time3, Time4, Lap4, Lap3, Lap2, Lap1
- Lap1 = 1F→0F(ゴール直前 1F)→ **F4 判定で使用**(≤ 11.2 で発火)
- Lap2 = 2F→1F(直前 1F のひとつ前)→ **F5 判定の追加条件**(≤ 11.2)

API:
- parse_training_csv(file_bytes) -> pd.DataFrame
- match_training_to_horses(training_df, race_card_df, target_date) ->
    dict[horse_id -> {"lap1": float, "lap2": float, "place": str, "time": str}]
- evaluate_f4_f5(training_match) -> tuple[str | None, int, str | None]
"""

from __future__ import annotations

import io
import unicodedata
from typing import Any

import pandas as pd


F4_F5_THRESHOLD = 11.2  # 1F ラップの F4/F5 発火閾値(秒)。≤ で含む。

# 入力 CSV の日本語列名 → 内部使用の英名マッピング
_COLUMN_RENAME = {
    "場所":     "place",
    "年月日":   "training_date",
    "曜日":     "weekday",
    "時刻":     "training_time",
    "時間":     "training_time",  # 旧フォーマット表記揺れ吸収
    "馬名":     "horse_name",
    "Ｃ":       "course_flag",     # 全角 C(コース種別等の予約フィールド)
    "C":        "course_flag",
    "性別":     "sex",
    "性":       "sex",
    "年齢":     "age",
    "収得賞金": "earnings",
    "馬体重増減": "weight_diff",   # 旧表記 fallback
    "調教師":   "trainer",
    "Time1":    "time1",
    "Time2":    "time2",
    "Time3":    "time3",
    "Time4":    "time4",
    "Lap4":     "lap4_time",
    "Lap3":     "lap3_time",
    "Lap2":     "lap2_time",
    "Lap1":     "lap1_time",
}


def _decode_with_fallback(raw: bytes) -> str:
    """坂路 CSV のエンコーディングを自動判定。

    お父様の TARGET 出力は Shift_JIS が標準だが、Excel 経由で utf-8-sig に
    なる可能性も考慮して順に試す。
    """
    for enc in ("shift_jis", "cp932", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        "坂路 CSV の文字コードを判定できませんでした。"
        "Shift_JIS / UTF-8 で保存し直してください。"
    )


def _normalize_horse_name(name: Any) -> str:
    """馬名を NFKC 正規化(全角/半角統一)+ 前後空白除去。

    historical 由来の馬名(漢字 or カナ)と TARGET 坂路 CSV の馬名(カナ)で
    全角・半角の揺れを吸収する。
    """
    if name is None:
        return ""
    s = str(name)
    if not s or s.lower() == "nan":
        return ""
    return unicodedata.normalize("NFKC", s).strip()


def parse_training_csv(file_bytes: bytes) -> pd.DataFrame:
    """坂路調教 CSV(bytes)を DataFrame に変換する。

    返す DataFrame の列(英名):
      place, training_date, weekday, training_time, horse_name,
      course_flag, sex, age, earnings, trainer,
      time1, time2, time3, time4, lap4_time, lap3_time, lap2_time, lap1_time

    数値列(time*, lap*_time)は float、空欄/不正値は NaN。
    馬名は NFKC 正規化 + strip 済み。
    training_date は文字列のまま("20260509" 形式)で保持(マッチ時の比較用)。
    """
    text = _decode_with_fallback(file_bytes)
    df = pd.read_csv(io.StringIO(text), dtype=str, low_memory=False)

    # 列名リネーム(未知の列は元名のまま残す)
    rename_map = {col: _COLUMN_RENAME[col] for col in df.columns if col in _COLUMN_RENAME}
    df = df.rename(columns=rename_map)

    # 馬名正規化
    if "horse_name" in df.columns:
        df["horse_name"] = df["horse_name"].map(_normalize_horse_name)

    # 数値列を float に
    numeric_cols = [
        "time1", "time2", "time3", "time4",
        "lap4_time", "lap3_time", "lap2_time", "lap1_time",
        "age", "earnings",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # training_date は str のまま、ただし不要な空白除去
    if "training_date" in df.columns:
        df["training_date"] = df["training_date"].astype(str).str.strip()

    # 空行(全 NaN 行)を捨てる
    df = df.dropna(how="all").reset_index(drop=True)

    return df


def match_training_to_horses(
    training_df: pd.DataFrame,
    race_card_df: pd.DataFrame,
    target_date: str | None = None,
) -> dict[str, dict]:
    """当日出馬表の各馬に坂路調教データを引き当てる。

    引数:
        training_df: parse_training_csv の戻り値。
        race_card_df: enrich 済みの当日出馬表(horse_id, horse_name 必須)。
        target_date: "YYYYMMDD" or "YYYY-MM-DD" 形式。指定された場合は
                     その日 **以前**(同日含む)の調教を使い、各馬の
                     **最新日 + 最新時刻** のものを 1 行採用する。
                     None なら training_df 全範囲。

    戻り値:
        {horse_id: {"lap1": float, "lap2": float, "place": str,
                    "training_time": str, "horse_name_match": str}}
        マッチ失敗馬は dict に **含めない**(F4/F5 評価対象外)。
        通常レース当日朝の調教は無いので、直前の追切日(target_date - 数日)
        の調教データを参照する。同一馬で複数追切があれば最新を採用。
    """
    if training_df is None or training_df.empty:
        return {}
    if "horse_name" not in training_df.columns:
        return {}

    # target_date を YYYYMMDD 形式に揃えて、その日「以前」の調教に絞る。
    # レース当日朝は通常追切なし → 直前の追切日(数日前)を採用するのが運用。
    if target_date:
        td = str(target_date).replace("-", "").replace("/", "").strip()
        if "training_date" in training_df.columns:
            training_df = training_df[training_df["training_date"] <= td]
            if training_df.empty:
                return {}

    # 同一馬で複数行 → 最新日 → 最新時刻 の順で先頭を採用
    sort_cols = ["horse_name"]
    sort_asc = [True]
    if "training_date" in training_df.columns:
        sort_cols.append("training_date")
        sort_asc.append(False)
    if "training_time" in training_df.columns:
        sort_cols.append("training_time")
        sort_asc.append(False)
    training_df = training_df.sort_values(sort_cols, ascending=sort_asc)
    horse_to_train = (
        training_df.drop_duplicates(subset=["horse_name"], keep="first")
        .set_index("horse_name")
        .to_dict("index")
    )

    out: dict[str, dict] = {}
    if "horse_id" not in race_card_df.columns or "horse_name" not in race_card_df.columns:
        return {}

    # **perf**: 旧実装は race_card_df.iterrows() で 495 行を Python ループ
    # していて 8 秒級の hot path だった。to_dict("records") で一括変換し、
    # plain dict のリストに対するループ + dict 参照に変えると 100 倍以上速い。
    rc_records = race_card_df[["horse_id", "horse_name"]].to_dict("records")

    for row in rc_records:
        hid = str(row["horse_id"])
        hname_norm = _normalize_horse_name(row.get("horse_name"))
        if not hname_norm:
            continue
        # 「馬番N(新馬)」「馬番N(DB照合不能)」等の DC マッチ失敗ラベルは除外
        if hname_norm.startswith("馬番"):
            continue

        train_row = horse_to_train.get(hname_norm)
        if not train_row:
            continue

        out[hid] = {
            "lap1": _safe_float(train_row.get("lap1_time")),
            "lap2": _safe_float(train_row.get("lap2_time")),
            "lap3": _safe_float(train_row.get("lap3_time")),
            "lap4": _safe_float(train_row.get("lap4_time")),
            "time4": _safe_float(train_row.get("time4")),
            "place": str(train_row.get("place") or ""),
            "training_time": str(train_row.get("training_time") or ""),
            "horse_name_match": hname_norm,
        }
    return out


def _safe_float(v: Any) -> float | None:
    """None/NaN/空文字を None に丸めた float 変換。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def evaluate_f4_f5(
    training: dict | None,
) -> tuple[str | None, int, str | None]:
    """1 頭分の training データから F4 / F5 を評価する。

    引数:
        training: match_training_to_horses 戻り値の 1 馬分 dict。None 可。

    戻り値:
        (rule_id, rate, reason) のタプル。
        - F5 発火: lap1 ≤ 11.2 AND lap2 ≤ 11.2 → ("F5", 40, 理由文字列)
        - F4 発火: lap1 ≤ 11.2 のみ           → ("F4", 30, 理由文字列)
        - 不発: (None, 0, None)
        F5 が発火する場合は F4 は **採用しない**(F5 が排他、+40 のみ)。
        境界値 11.2 ジャストは ≤ なので発火する。
    """
    if not training:
        return None, 0, None

    lap1 = training.get("lap1")
    lap2 = training.get("lap2")

    if lap1 is None:
        return None, 0, None
    if lap1 > F4_F5_THRESHOLD:
        return None, 0, None

    # ここまで lap1 ≤ 11.2 確定
    if lap2 is not None and lap2 <= F4_F5_THRESHOLD:
        reason = f"坂路 1F = {lap1:.1f} + 2F = {lap2:.1f}(両方 ≤ {F4_F5_THRESHOLD})"
        return "F5", 40, reason

    # F4 単独
    lap2_str = f"{lap2:.1f}" if lap2 is not None else "-"
    reason = f"坂路 1F = {lap1:.1f} ≤ {F4_F5_THRESHOLD}(2F = {lap2_str})"
    return "F4", 30, reason
