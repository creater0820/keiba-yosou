"""v1.10.0: TARGET SE 形式パーサ utils/target_history_parser.py のテスト。

実機サンプル(data/test/target_history_sample.csv)を含めた検証。
既存 parquet スキーマとの完全 dtype 一致、horse_id 8 桁ゼロパディング、
race_id 形式の正確性、Shift_JIS デコード、時刻フォーマット変換、
着順 0(中止・除外・失格・取消)の保持などを担保する。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from utils.target_history_parser import (  # noqa: E402
    PARQUET_COLUMNS,
    RACECOURSE_TO_PREFIX,
    SE_PARSER_SCHEMA_VERSION,
    _convert_time_str,
    _to_race_date,
    _to_race_id,
    parse_se_csv,
)


SAMPLE_PATH = ROOT / "data" / "test" / "target_history_sample.csv"


# ==================================================================
# race_id 構築ロジック
# ==================================================================
def test_race_id_basic():
    """既存 parquet と完全一致する race_id を組み立てる。"""
    assert _to_race_id("2025-07-26", "札幌", 1) == "R20250726-札01"
    assert _to_race_id("2026-05-09", "東京", 11) == "R20260509-東11"
    assert _to_race_id("2023-01-05", "中山", 1) == "R20230105-中01"


def test_race_id_nakayama_nakakyo_collision_is_kept():
    """中山と中京は既存 parquet 仕様で同じ「中」プレフィックスになる。

    既存スキーマ踏襲のため修正しない(CLAUDE.md に注記)。
    """
    assert _to_race_id("2026-05-09", "中山", 1) == "R20260509-中01"
    assert _to_race_id("2026-05-09", "中京", 1) == "R20260509-中01"


def test_race_id_unknown_racecourse_returns_none():
    """場名マップにない場所(例: 海外)は None。"""
    assert _to_race_id("2026-05-09", "ドバイ", 1) is None
    assert _to_race_id("2026-05-09", "", 1) is None


def test_racecourse_map_has_all_10_jra_courses():
    """JRA 10 場所が全部マップに入っている(過去取り込み漏れ防止)。"""
    expected = {"札幌", "函館", "福島", "新潟", "東京", "中山",
                "中京", "京都", "阪神", "小倉"}
    assert set(RACECOURSE_TO_PREFIX.keys()) == expected


# ==================================================================
# race_date 構築
# ==================================================================
def test_race_date_basic():
    assert _to_race_date("25", "07", "26") == "2025-07-26"
    assert _to_race_date("23", "01", "05") == "2023-01-05"


def test_race_date_zero_padding():
    """月日が 1 桁でも 2 桁ゼロパディングして既存 YYYY-MM-DD に揃える。"""
    assert _to_race_date("25", "7", "5") == "2025-07-05"


def test_race_date_invalid_returns_none():
    assert _to_race_date("", "", "") is None
    assert _to_race_date("25", "13", "01") is None    # 13 月は無効
    assert _to_race_date("25", "01", "32") is None    # 32 日は無効
    assert _to_race_date("xx", "07", "26") is None


# ==================================================================
# 走破タイム変換
# ==================================================================
def test_time_conversion_4digits():
    """4 桁 MSSX → "M:SS.X" 形式。"""
    assert _convert_time_str("1114") == "1:11.4"
    assert _convert_time_str("1091") == "1:09.1"
    assert _convert_time_str("0589") == "0:58.9"


def test_time_conversion_5digits():
    """5 桁(3 分超など)→ "MM:SS.X" 形式。"""
    assert _convert_time_str("12345") == "12:34.5"


def test_time_conversion_invalid():
    """空文字や非数値は空文字を返す。"""
    assert _convert_time_str("") == ""
    assert _convert_time_str("abc") == ""
    assert _convert_time_str(None) == ""


# ==================================================================
# サンプル CSV を使った統合テスト
# ==================================================================
def test_parse_sample_csv_loads_all_rows():
    """data/test/target_history_sample.csv 実機を全行パース。"""
    if not SAMPLE_PATH.exists():
        import pytest
        pytest.skip(f"サンプル CSV なし: {SAMPLE_PATH}")
    result = parse_se_csv(SAMPLE_PATH)
    assert result.total_rows == 65869
    assert result.parsed_rows == 65869
    assert result.skipped_rows == 0


def test_parse_sample_dtypes_match_parquet_exactly():
    """パース結果の dtype が既存 parquet と全 26 列で完全一致。"""
    if not SAMPLE_PATH.exists():
        import pytest
        pytest.skip(f"サンプル CSV なし: {SAMPLE_PATH}")
    hist_path = ROOT / "data" / "historical" / "races.parquet"
    if not hist_path.exists():
        import pytest
        pytest.skip(f"historical parquet なし: {hist_path}")

    result = parse_se_csv(SAMPLE_PATH)
    hist = pd.read_parquet(hist_path)
    for col in PARQUET_COLUMNS:
        assert str(result.df[col].dtype) == str(hist[col].dtype), \
            f"dtype mismatch on {col}: parsed={result.df[col].dtype} vs existing={hist[col].dtype}"


def test_parse_sample_horse_id_is_8digit_str():
    """horse_id は 8 桁文字列のままになっている(int 化されてない)。

    pandas 2.x では astype(str) が object ではなく StringDtype を返すことが
    あるため、dtype 文字列表現で str/string/object を許容。
    既存 parquet との dtype 完全一致は test_parse_sample_dtypes_match_parquet_exactly
    で別途担保している。
    """
    if not SAMPLE_PATH.exists():
        import pytest
        pytest.skip(f"サンプル CSV なし: {SAMPLE_PATH}")
    result = parse_se_csv(SAMPLE_PATH)
    hid = result.df["horse_id"]
    assert str(hid.dtype) in ("object", "string", "str"), \
        f"unexpected horse_id dtype: {hid.dtype}"
    lengths = hid.str.len().unique().tolist()
    assert lengths == [8], f"horse_id 桁数が 8 に統一されていない: {lengths}"


def test_parse_sample_finishing_position_0_preserved():
    """着順 0(中止・除外・失格・取消)が skip されずそのまま取り込まれる。

    既存 parquet にも 1,336 件存在する仕様。
    """
    if not SAMPLE_PATH.exists():
        import pytest
        pytest.skip(f"サンプル CSV なし: {SAMPLE_PATH}")
    result = parse_se_csv(SAMPLE_PATH)
    zero_count = (result.df["finishing_position"] == 0).sum()
    assert zero_count > 0, "着順 0 が 1 件もないのは想定外"
    # サンプル CSV では 516 件確認済(実機ダンプ)
    assert zero_count == 516


def test_parse_sample_race_id_format():
    """全 race_id が `R<YYYYMMDD>-<場1文字><RR>` 形式(13 文字)。"""
    if not SAMPLE_PATH.exists():
        import pytest
        pytest.skip(f"サンプル CSV なし: {SAMPLE_PATH}")
    result = parse_se_csv(SAMPLE_PATH)
    rids = result.df["race_id"]
    lengths = rids.str.len().unique().tolist()
    assert lengths == [13], f"race_id 桁数不揃い: {lengths}"
    # 先頭は必ず "R"
    assert (rids.str[0] == "R").all()


def test_parse_sample_corner_1_2_are_na():
    """SE 形式は corner_1/corner_2 を持たない → すべて pd.NA。"""
    if not SAMPLE_PATH.exists():
        import pytest
        pytest.skip(f"サンプル CSV なし: {SAMPLE_PATH}")
    result = parse_se_csv(SAMPLE_PATH)
    assert result.df["corner_1"].isna().all()
    assert result.df["corner_2"].isna().all()


def test_parse_sample_corner_3_4_present():
    """corner_3/corner_4 は SE に存在 → ほぼ全行で値あり。"""
    if not SAMPLE_PATH.exists():
        import pytest
        pytest.skip(f"サンプル CSV なし: {SAMPLE_PATH}")
    result = parse_se_csv(SAMPLE_PATH)
    # 着順 0(中止・除外)では corner も欠損のため、完全 100% ではない
    assert result.df["corner_3"].notna().mean() > 0.95
    assert result.df["corner_4"].notna().mean() > 0.95


def test_parse_sample_post_time_is_empty_string():
    """post_time は SE に含まれないため全て空文字 ""。"""
    if not SAMPLE_PATH.exists():
        import pytest
        pytest.skip(f"サンプル CSV なし: {SAMPLE_PATH}")
    result = parse_se_csv(SAMPLE_PATH)
    assert (result.df["post_time"] == "").all()


def test_parse_sample_weight_change_is_zero():
    """weight_change は SE に含まれないため全て 0(int)。"""
    if not SAMPLE_PATH.exists():
        import pytest
        pytest.skip(f"サンプル CSV なし: {SAMPLE_PATH}")
    result = parse_se_csv(SAMPLE_PATH)
    assert (result.df["weight_change"] == 0).all()
    assert str(result.df["weight_change"].dtype) == "int64"


def test_parse_sample_horse_name_stripped():
    """馬名の末尾空白が strip されている。"""
    if not SAMPLE_PATH.exists():
        import pytest
        pytest.skip(f"サンプル CSV なし: {SAMPLE_PATH}")
    result = parse_se_csv(SAMPLE_PATH)
    names = result.df["horse_name"]
    # 末尾空白が混じっていないか
    has_trailing = names.str.endswith(" ").any() or names.str.endswith("　").any()
    assert not has_trailing


def test_parse_sample_first_race_round_trip():
    """1 R 目の特定馬の値が SE 原本と一致(列マッピング正確性の最終確認)。"""
    if not SAMPLE_PATH.exists():
        import pytest
        pytest.skip(f"サンプル CSV なし: {SAMPLE_PATH}")
    result = parse_se_csv(SAMPLE_PATH)
    # 1 行目 = 札幌 2025/7/26 1R 馬番 1 セイウンダイフク
    row = result.df.iloc[0]
    assert row["race_id"] == "R20250726-札01"
    assert row["race_date"] == "2025-07-26"
    assert row["racecourse"] == "札幌"
    assert row["race_number"] == 1
    assert row["horse_number"] == 1
    assert row["horse_id"] == "23104705"
    assert row["horse_name"] == "セイウンダイフク"
    assert row["jockey"] == "池添謙一"
    assert row["finishing_position"] == 12
    assert row["weight"] == 428
    assert row["carry_weight"] == 55.0
    assert row["popularity"] == 6
    assert row["odds"] == 36.9
    assert row["last_3f"] == 35.1
    assert row["distance"] == 1200
    assert row["surface"] == "芝"
    assert row["going"] == "良"
    assert row["time"] == "1:11.4"
    assert row["corner_3"] == 12
    assert row["corner_4"] == 12
    # SE になし
    assert pd.isna(row["corner_1"])
    assert pd.isna(row["corner_2"])
    assert row["post_time"] == ""
    assert row["weight_change"] == 0


# ==================================================================
# 小さい合成 CSV(エッジケース)
# ==================================================================
def _make_minimal_se_csv(rows: list[list[str]]) -> bytes:
    """テスト用に小さい SE 形式 CSV を作成。52 列ぴったりにパディング。"""
    out_lines = []
    for r in rows:
        # 52 列に満たない部分は空文字で埋める
        padded = r + [""] * (52 - len(r))
        out_lines.append(",".join(padded))
    text = "\n".join(out_lines)
    return text.encode("shift_jis")


def test_parse_minimal_valid_row():
    """最小限の有効行(必須列だけ埋める)を 1 行パース。"""
    row = [""] * 52
    row[0], row[1], row[2] = "25", "07", "26"
    row[4] = "東京"
    row[6] = "01"
    row[7] = "テストレース"
    row[9] = "芝"
    row[11] = "1600"
    row[12] = "良"
    row[13] = "テストウマ"
    row[16] = "テスト騎手"
    row[17] = "55.0"
    row[19] = "01"
    row[20] = "1"
    row[24] = "1"
    row[26] = "1320"
    row[30] = "1"
    row[31] = "1"
    row[32] = "33.5"
    row[33] = "500"
    row[34] = "テスト調教師"
    row[37] = "21100648"
    row[48] = "1.5"
    data = _make_minimal_se_csv([row])
    result = parse_se_csv(data)
    assert result.parsed_rows == 1
    assert result.df.iloc[0]["race_id"] == "R20250726-東01"
    assert result.df.iloc[0]["horse_id"] == "21100648"


def test_parse_skip_invalid_horse_id():
    """horse_id が空・非数字なら skip。"""
    row1 = [""] * 52
    row1[0], row1[1], row1[2] = "25", "07", "26"
    row1[4] = "東京"
    row1[6] = "01"
    row1[11] = "1600"
    row1[13] = "ABC"
    row1[19] = "01"
    row1[37] = ""  # ← 空
    row2 = list(row1)
    row2[37] = "BAD123"  # ← 非数字混じり

    data = _make_minimal_se_csv([row1, row2])
    result = parse_se_csv(data)
    assert result.parsed_rows == 0
    assert result.skipped_rows == 2
    assert result.skipped_reasons.get("invalid_horse_id", 0) == 2


def test_parse_skip_invalid_date():
    """年月日が不正な行は skip。"""
    row = [""] * 52
    row[0], row[1], row[2] = "", "", ""
    row[4] = "東京"
    row[37] = "21100648"
    data = _make_minimal_se_csv([row])
    result = parse_se_csv(data)
    assert result.parsed_rows == 0
    assert result.skipped_reasons.get("invalid_date", 0) == 1


def test_parse_horse_id_zero_padded():
    """horse_id が 7 桁以下でも 8 桁にゼロパディングされる。"""
    row = [""] * 52
    row[0], row[1], row[2] = "25", "07", "26"
    row[4] = "東京"
    row[6] = "01"
    row[11] = "1600"
    row[13] = "馬"
    row[19] = "01"
    row[37] = "1234567"  # 7 桁
    data = _make_minimal_se_csv([row])
    result = parse_se_csv(data)
    assert result.parsed_rows == 1
    assert result.df.iloc[0]["horse_id"] == "01234567"


# ==================================================================
# Schema バージョン定数
# ==================================================================
def test_schema_version_constant_exists():
    """cache 無効化用の SE_PARSER_SCHEMA_VERSION が設定されている。"""
    assert SE_PARSER_SCHEMA_VERSION
    assert "v1.10" in SE_PARSER_SCHEMA_VERSION


# ==================================================================
# bytes / file-like / Path 受け入れ
# ==================================================================
def test_parse_accepts_bytes():
    """bytes 直接渡しでパースできる。"""
    row = [""] * 52
    row[0], row[1], row[2] = "25", "07", "26"
    row[4] = "東京"
    row[6] = "01"
    row[11] = "1600"
    row[13] = "馬"
    row[19] = "01"
    row[37] = "21100648"
    data = _make_minimal_se_csv([row])
    result = parse_se_csv(data)
    assert result.parsed_rows == 1


def test_parse_accepts_io_bytesio():
    """BytesIO file-like でパースできる(Streamlit UploadedFile 互換)。"""
    row = [""] * 52
    row[0], row[1], row[2] = "25", "07", "26"
    row[4] = "東京"
    row[6] = "01"
    row[11] = "1600"
    row[13] = "馬"
    row[19] = "01"
    row[37] = "21100648"
    data = _make_minimal_se_csv([row])
    result = parse_se_csv(io.BytesIO(data))
    assert result.parsed_rows == 1
