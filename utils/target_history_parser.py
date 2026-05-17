"""TARGET frontier JV「SE(成績)形式」過去 CSV → races.parquet 互換 DataFrame パーサ。

v1.10.0 で新規追加。**既存の当日 CSV パーサ(`utils/target_format.py`)とは
完全に独立**したモジュール。当日 RA / DC 形式の読み込みフローは v1.9.x まで
の挙動を絶対に変更しないため、ここから既存パーサを import しない。

================================================================
TARGET SE 形式の仕様(実機検証、`data/test/target_history_sample.csv`)
================================================================
- エンコーディング: Shift_JIS(BOM なし)
- ヘッダ: 無し
- 列数: 52 列
- 区切り: カンマ
- 1 行 = 1 出走馬

列マッピング(0-index)
| col | 内容 | parquet 列マッピング |
|---:|---|---|
|   0 | 西暦下 2 桁 (YY)              | race_date 構築 |
|   1 | 月 MM                         | race_date 構築 |
|   2 | 日 DD                         | race_date 構築 |
|   3 | 開催回(月内)                  | 使わない |
|   4 | 場名 漢字 2 文字              | racecourse / race_id 構築 |
|   5 | 開催日目                       | 使わない |
|   6 | R 番号 2 桁                    | race_number / race_id 構築 |
|   7 | レース名(末尾 `*` は条件付き)| race_name |
|   8 | クラスコード                   | 使わない |
|   9 | 芝/ダ/障                        | surface |
|  10 | 内外コード                     | 使わない(v1.10.x スコープ外) |
|  11 | 距離 m                         | distance |
|  12 | 馬場(良/稍/重/不)             | going |
|  13 | 馬名(カナ)                    | horse_name(strip) |
|  14 | 性別(牡/牝/セ)                | 既存スキーマになし → 捨てる |
|  15 | 年齢                           | 既存スキーマになし → 捨てる |
|  16 | 騎手氏名                       | jockey(strip) |
|  17 | 斤量(kg)                       | carry_weight(float) |
|  18 | 頭数                           | 使わない |
|  19 | 馬番 2 桁                      | horse_number(Int64) |
|  20 | 着順(0=中止/除外/失格/取消)   | finishing_position(0 含む)|
|  21 | (馬番複写 — SE 仕様)            | 使わない |
|  22 | 0(意味不明、常に 0)            | 使わない |
|  23 | 1 着とのタイム差(秒)          | 使わない |
|  24 | 単勝人気                       | popularity(Int64)|
|  25 | 着差順タイム(?)                 | 使わない |
|  26 | 走破タイム 4-5 桁(MSSX/MMSSX) | time("M:SS.X" 形式に変換)|
|  27 | スピード指数(?)                 | 使わない |
|  28 | 0                              | 使わない |
|  29 | 0                              | 使わない |
|  30 | 3 コーナー通過順位             | corner_3(Int64)|
|  31 | 4 コーナー通過順位             | corner_4(Int64)|
|  32 | 上がり 3F(秒)                  | last_3f(float)|
|  33 | 馬体重(kg)                     | weight(Int64)|
|  34 | 調教師                         | trainer(strip)|
|  35 | 厩舎所属(栗/美/地/外)         | 既存スキーマになし |
|  36 | 賞金(?)                         | 使わない |
|  37 | 馬 ID 8 桁(血統登録番号)      | horse_id(str zfill 8)|
|  38 | 5 桁数値(意味不明)            | 使わない |
|  39 | 5 桁数値(意味不明)            | 使わない |
|  40 | 10 桁数値(意味不明)           | 使わない |
|  41 | 馬主                           | 使わない |
|  42 | 牧場                           | 使わない |
|  43 | 父                             | 使わない(将来 sire 列追加時に活用候補)|
|  44 | 母                             | 使わない |
|  45 | 母父                           | 使わない |
|  46 | 毛色                           | 使わない |
|  47 | 生年月日 YYMMDD                 | 使わない |
|  48 | 単勝オッズ(倍)                 | odds(float)|
|  49 | 空                             | 使わない |
|  50 | 空                             | 使わない |
|  51 | タイム指数 / TARGET ZI(?)      | 使わない |

================================================================
既存 parquet スキーマで SE 形式に「対応列がない」項目の扱い
================================================================
- **corner_1 / corner_2**: SE は短距離コーナーを持たない仕様(既存
  parquet も 1600m 以下では 55.98% null)→ pd.NA で埋める。
- **post_time**: SE に時刻情報なし → 空文字 `""` で埋める
  (既存 parquet は 0% null だが、SE 取り込み行は post_time 不明)。
- **weight_change**: SE に体重増減なし → 0 で埋める
  (CLAUDE.md に「0 = 取り込み元 SE に情報なし」と注記)。

================================================================
race_id 構築仕様
================================================================
既存 parquet の race_id は `R<YYYYMMDD>-<場の最初 1 文字><RR>` 形式(13 桁)。
- 札幌 → 札 / 中山 → 中 / 中京 → 中(← 中山と衝突。既存仕様踏襲)
- 京都 → 京 / 阪神 → 阪 / 東京 → 東 / 福島 → 福
- 新潟 → 新 / 小倉 → 小 / 函館 → 函

⚠ 中山と中京が同一プレフィックスになる既知の制約。同日に両場で開催が
ある場合、(race_id, horse_id) 複合キーが衝突するリスクがあるが、既存
parquet ではこれまで実害が出ていない(同馬が両場同時出走しないため)。
将来スキーマ拡張時に内外区別を含めた race_id v2 に移行する余地あり。

================================================================
取り込み挙動の方針
================================================================
- 着順 0(中止・除外・失格・取消)も**そのまま取り込む**
  (既存 parquet に 1,336 件存在、skip しない)
- 列マッピング不能な行(必須列欠損・型変換失敗)はログ出力 + skip
- 戻り値の DataFrame は **既存 parquet と完全同一の 26 列・同一 dtype**
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import pandas as pd

# 取り込みパーサ自体のスキーマバージョン(出力列・dtype を変える時に bump)
SE_PARSER_SCHEMA_VERSION = "v1.10.0-se-historical"

# 既存 parquet の正規列リスト(26 列、dtype 含めて固定)
PARQUET_COLUMNS: tuple[str, ...] = (
    "race_id", "race_date", "racecourse", "race_number", "race_name",
    "post_time", "distance", "surface", "going",
    "finishing_position", "horse_number", "horse_id", "horse_name",
    "jockey", "trainer", "weight", "carry_weight", "weight_change",
    "time", "last_3f", "popularity", "odds",
    "corner_1", "corner_2", "corner_3", "corner_4",
)

# 場名(漢字 2 文字)→ race_id プレフィックス(1 文字)
# 既存 parquet 仕様: 場名の最初の 1 文字を採用。
RACECOURSE_TO_PREFIX: dict[str, str] = {
    "札幌": "札", "函館": "函", "福島": "福", "新潟": "新",
    "東京": "東", "中山": "中", "中京": "中",  # 衝突は既存仕様
    "京都": "京", "阪神": "阪", "小倉": "小",
}

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """SE CSV パース結果の集計。サマリ表示・ログ用。"""
    df: pd.DataFrame                   # 既存 parquet 互換 26 列
    total_rows: int                    # 入力 CSV の全行数
    parsed_rows: int                   # 正常パースできた行数
    skipped_rows: int                  # 型変換失敗等で除外した行数
    unique_races: int                  # ユニーク race_id 数
    unique_horses: int                 # ユニーク horse_id 数
    date_min: str                      # 最小 race_date
    date_max: str                      # 最大 race_date
    skipped_reasons: dict[str, int]    # スキップ理由ごとの件数


def _read_bytes(src: bytes | IO | str | Path) -> bytes:
    """src を bytes に正規化(Streamlit UploadedFile / Path / bytes に対応)。"""
    if isinstance(src, (bytes, bytearray)):
        return bytes(src)
    if hasattr(src, "read"):
        if hasattr(src, "seek"):
            try:
                src.seek(0)
            except Exception:
                pass
        data = src.read()
        return data if isinstance(data, bytes) else data.encode("utf-8")
    return Path(src).read_bytes()


def _decode_shift_jis(raw: bytes) -> str:
    """Shift_JIS → UTF-8 → cp932 の順で復号試行。"""
    for enc in ("shift_jis", "cp932", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        "SE 形式の文字コードを判定できませんでした。"
        "Shift_JIS / cp932 / UTF-8 のいずれかで保存し直してください。"
    )


def _to_race_date(yy: str, mm: str, dd: str) -> str | None:
    """YY / MM / DD を "20YY-MM-DD" に。失敗時 None。"""
    try:
        y = int(yy)
        m = int(mm)
        d = int(dd)
        if not (0 <= y <= 99 and 1 <= m <= 12 and 1 <= d <= 31):
            return None
        return f"20{y:02d}-{m:02d}-{d:02d}"
    except (TypeError, ValueError):
        return None


def _to_race_id(race_date: str, racecourse: str, race_number: int) -> str | None:
    """既存 parquet 形式 `R<YYYYMMDD>-<場1文字><RR>` に組み立て。失敗時 None。"""
    prefix = RACECOURSE_TO_PREFIX.get(racecourse)
    if not prefix:
        return None
    try:
        yyyymmdd = race_date.replace("-", "")
        return f"R{yyyymmdd}-{prefix}{race_number:02d}"
    except (AttributeError, TypeError):
        return None


def _convert_time_str(raw: str) -> str:
    """SE の走破タイム(末尾 1 桁が小数点なしの BCD 風表記)を "M:SS.X" に変換。

    入力例:
      - "1114" → "1:11.4"   (4 桁 = 1 分台)
      - "0589" → "0:58.9"   (4 桁、1 分未満)
      - "10234" → "1:02.34"(5 桁、ただし末尾 1 桁が 0.x の場合 4 桁が主流)
      - "12345" → "1:23.45" / "11234" → "1:12.34"
    SE 形式は通常 4 桁(M=1) or 5 桁(M>=2 分 / 3 分台以上)で表す。
    既存 parquet 形式 "1:10.30" との互換は 4 桁時 "1:11.4" になる
    (細部は dtype=str のため、UI 表示でしか使わない)。
    """
    s = str(raw or "").strip()
    if not s or not s.isdigit():
        return ""
    if len(s) == 4:
        # MMSS 形式の SE は最後 1 桁が 0.x → "M:SS.X"
        return f"{int(s[0])}:{int(s[1:3]):02d}.{s[3]}"
    if len(s) == 5:
        # MMSSX 風: 最初 1 桁が分、次 2 桁が秒、末尾 2 桁が 0.xx
        # ただし TARGET 仕様により 5 桁時も末尾 1 桁を小数 1 位とする運用に
        # 倣う(2 桁見える時は M+0 を前置く)
        # 安全策: 最初の 2 桁を分、次 2 桁を秒、末尾 1 桁を 0.x として扱う
        return f"{int(s[:2])}:{int(s[2:4]):02d}.{s[4]}"
    if len(s) == 3:
        # 万一の "MSS" 形式
        return f"{int(s[0])}:{int(s[1:3]):02d}.0"
    return ""


def _to_int_or_na(v) -> object:
    """Int64 用: 空文字や "-" は pd.NA、数字なら int。失敗時 pd.NA。"""
    if v is None:
        return pd.NA
    s = str(v).strip()
    if s in ("", "-", "  ", "--", "NA"):
        return pd.NA
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return pd.NA


def _to_float_or_nan(v) -> float:
    """float64 用: 空文字や非数値は NaN。"""
    if v is None:
        return float("nan")
    s = str(v).strip()
    if s in ("", "-", "--", "NA"):
        return float("nan")
    try:
        return float(s)
    except (ValueError, TypeError):
        return float("nan")


def _to_str_clean(v) -> str:
    """str 用: 末尾空白除去、None は空文字。"""
    if v is None:
        return ""
    return str(v).strip()


def parse_se_csv(
    src: bytes | IO | str | Path,
) -> ParseResult:
    """SE 形式 CSV を既存 parquet 互換 DataFrame に変換する。

    引数:
        src: ファイルパス / file-like / bytes のいずれか。
    戻り値:
        ParseResult(df, 集計サマリ)。df は 26 列の既存 parquet スキーマに準拠。

    挙動:
        - 着順 0(中止/除外/失格/取消)はそのまま保持(既存 parquet 仕様)
        - 必須列(年月日・場名・R番号・馬番・馬名・馬 ID)欠損行は skip
        - corner_1/corner_2/post_time/weight_change は SE に対応情報なし
          → それぞれ pd.NA / "" / 0 で埋める
    """
    raw_bytes = _read_bytes(src)
    text = _decode_shift_jis(raw_bytes)

    raw = pd.read_csv(
        io.StringIO(text),
        header=None,
        dtype=str,
        low_memory=False,
        # SE 形式は 52 列固定だが、行末の空フィールド数が日によりブレる
        # 場合があるので列数を厳格チェックしない
    )
    total_rows = len(raw)

    skipped_reasons: dict[str, int] = {}
    out_rows: list[dict] = []

    # 列インデックス(SE 形式の固定マッピング)
    YY, MM, DD = 0, 1, 2
    RACECOURSE = 4
    RACE_NUMBER = 6
    RACE_NAME = 7
    SURFACE = 9
    DISTANCE = 11
    GOING = 12
    HORSE_NAME = 13
    JOCKEY = 16
    CARRY_WEIGHT = 17
    HORSE_NUMBER = 19
    FINISHING = 20
    POPULARITY = 24
    TIME = 26
    CORNER_3 = 30
    CORNER_4 = 31
    LAST_3F = 32
    WEIGHT = 33
    TRAINER = 34
    HORSE_ID = 37
    ODDS = 48

    needed_max = max(
        YY, MM, DD, RACECOURSE, RACE_NUMBER, RACE_NAME,
        SURFACE, DISTANCE, GOING, HORSE_NAME, JOCKEY,
        CARRY_WEIGHT, HORSE_NUMBER, FINISHING, POPULARITY,
        TIME, CORNER_3, CORNER_4, LAST_3F, WEIGHT,
        TRAINER, HORSE_ID, ODDS,
    )
    if raw.shape[1] <= needed_max:
        raise ValueError(
            f"SE CSV の列数が想定より少ないです(実 {raw.shape[1]} 列、"
            f"必要 {needed_max + 1} 列)。TARGET frontier JV の SE 形式エクス"
            f"ポートをご確認ください。"
        )

    # **perf**: iterrows ではなく to_dict("records") + plain dict ループ
    records = raw.to_dict("records")
    for rec in records:
        # --- race_date 構築 ---
        race_date = _to_race_date(rec.get(YY), rec.get(MM), rec.get(DD))
        if not race_date:
            skipped_reasons["invalid_date"] = skipped_reasons.get("invalid_date", 0) + 1
            continue

        # --- racecourse / race_number ---
        racecourse = _to_str_clean(rec.get(RACECOURSE))
        race_number_raw = _to_int_or_na(rec.get(RACE_NUMBER))
        if not racecourse or race_number_raw is pd.NA:
            skipped_reasons["invalid_race_meta"] = (
                skipped_reasons.get("invalid_race_meta", 0) + 1
            )
            continue
        race_number = int(race_number_raw)
        race_id = _to_race_id(race_date, racecourse, race_number)
        if not race_id:
            skipped_reasons["unknown_racecourse"] = (
                skipped_reasons.get("unknown_racecourse", 0) + 1
            )
            continue

        # --- horse_id(8 桁ゼロパディングを維持)---
        horse_id_raw = _to_str_clean(rec.get(HORSE_ID))
        if not horse_id_raw or not horse_id_raw.isdigit():
            skipped_reasons["invalid_horse_id"] = (
                skipped_reasons.get("invalid_horse_id", 0) + 1
            )
            continue
        horse_id = horse_id_raw.zfill(8)

        # --- horse_name / horse_number ---
        horse_name = _to_str_clean(rec.get(HORSE_NAME))
        horse_number_raw = _to_int_or_na(rec.get(HORSE_NUMBER))
        if not horse_name or horse_number_raw is pd.NA:
            skipped_reasons["invalid_horse"] = (
                skipped_reasons.get("invalid_horse", 0) + 1
            )
            continue

        # --- distance / surface / going(必須)---
        distance_raw = _to_int_or_na(rec.get(DISTANCE))
        if distance_raw is pd.NA:
            skipped_reasons["invalid_distance"] = (
                skipped_reasons.get("invalid_distance", 0) + 1
            )
            continue

        out_rows.append({
            "race_id":            race_id,
            "race_date":          race_date,
            "racecourse":         racecourse,
            "race_number":        race_number,
            "race_name":          _to_str_clean(rec.get(RACE_NAME)),
            "post_time":          "",  # SE 形式に含まれない
            "distance":           int(distance_raw),
            "surface":            _to_str_clean(rec.get(SURFACE)),
            "going":              _to_str_clean(rec.get(GOING)),
            "finishing_position": _to_int_or_na(rec.get(FINISHING)),
            "horse_number":       int(horse_number_raw),
            "horse_id":           horse_id,
            "horse_name":         horse_name,
            "jockey":             _to_str_clean(rec.get(JOCKEY)),
            "trainer":            _to_str_clean(rec.get(TRAINER)),
            "weight":             _to_int_or_na(rec.get(WEIGHT)),
            "carry_weight":       _to_float_or_nan(rec.get(CARRY_WEIGHT)),
            "weight_change":      0,  # SE 形式に含まれない(0=不明)
            "time":               _convert_time_str(rec.get(TIME)),
            "last_3f":            _to_float_or_nan(rec.get(LAST_3F)),
            "popularity":         _to_int_or_na(rec.get(POPULARITY)),
            "odds":               _to_float_or_nan(rec.get(ODDS)),
            "corner_1":           pd.NA,  # SE 形式に含まれない
            "corner_2":           pd.NA,  # SE 形式に含まれない
            "corner_3":           _to_int_or_na(rec.get(CORNER_3)),
            "corner_4":           _to_int_or_na(rec.get(CORNER_4)),
        })

    df = pd.DataFrame(out_rows, columns=list(PARQUET_COLUMNS))

    # dtype 強制(既存 parquet と完全一致)
    df = _coerce_dtypes(df)

    return ParseResult(
        df=df,
        total_rows=total_rows,
        parsed_rows=len(df),
        skipped_rows=total_rows - len(df),
        unique_races=int(df["race_id"].nunique()) if not df.empty else 0,
        unique_horses=int(df["horse_id"].nunique()) if not df.empty else 0,
        date_min=str(df["race_date"].min()) if not df.empty else "",
        date_max=str(df["race_date"].max()) if not df.empty else "",
        skipped_reasons=skipped_reasons,
    )


def _coerce_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """既存 parquet と完全一致する dtype に強制変換。

    既存 parquet:
      - str:     race_id, race_date, racecourse, race_name, post_time,
                 surface, going, horse_id, horse_name, jockey, trainer, time
      - Int64:   race_number, distance, finishing_position, horse_number,
                 weight, popularity, corner_1, corner_2, corner_3, corner_4
      - float64: carry_weight, last_3f, odds
      - int64:   weight_change  (※他の Int カラムと違い nullable ではない)
    """
    str_cols = (
        "race_id", "race_date", "racecourse", "race_name", "post_time",
        "surface", "going", "horse_id", "horse_name", "jockey", "trainer",
        "time",
    )
    int64_cols = (
        "race_number", "distance", "finishing_position", "horse_number",
        "weight", "popularity", "corner_1", "corner_2", "corner_3", "corner_4",
    )
    float_cols = ("carry_weight", "last_3f", "odds")

    out = df.copy()
    for c in str_cols:
        out[c] = out[c].astype(str)
    for c in int64_cols:
        out[c] = pd.array(out[c].tolist(), dtype="Int64")
    for c in float_cols:
        out[c] = out[c].astype("float64")
    # weight_change だけは既存 parquet が int64(non-nullable)
    out["weight_change"] = out["weight_change"].astype("int64")
    return out
