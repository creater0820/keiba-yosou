"""
TARGET frontier JV の DC 形式検出と日本語ガイドエラー送出のユニットテスト。

DC 形式(ダイレクト/データカード系メニュー出力)は本アプリが必要とする
情報を含まないため、load_race_card() で早期に専用エラーを出して RA+SE
形式への切り替えを促す。

実行:
- python tests/test_target_format_dc.py     # 単体実行
- python -m pytest tests/test_target_format_dc.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_loader import load_race_card  # noqa: E402
from utils.target_format import (  # noqa: E402
    DC_FORMAT_ERROR_MESSAGE,
    is_dc_format,
    is_jra_van_headerless,
)


# サンプル DC 行(実 DC260509.CSV からの先頭行をそのまま流用)
_DC_FIRST_LINE = (
    "0426130101,  7,2890,  2, 13,104, 17,  0,  0,  0,  7,2910,  3, 13,  0,"
    "  7,2910,  3,  3,  0,  7,2970,  3,  5,  0, 43,1400,  1,  9,108,"
    " 43,1200,  1, 21,104, 43,1200,  1,  5,115, 43,1200,  1,  2,112,"
)


# RA+SE 形式の典型 1 行(年/月/日 + 場 + R + ... の 52 列)
_RA_FIRST_LINE = ",".join([
    "26", "5", "3", "5", "京都", "18", "11",
    "天皇賞春G1", "15", "芝", "B", "3200", "良",
    "クロワデュノール", "牡", "4", "北村友一", "58", "15", "7",
    "01", "01", "0.0", "0.0", "1", "201.5", "20503", "2", "0", "0",
    "0", "0", "32.9", "470", "藤原英昭", "栗", "37", "00",
    "22105102", "00", "1234567890", "0",
    "", "クロワデュノール父", "クロワデュノール母", "クロワデュノール母父",
    "0", "0", "0", "0", "0", "1.5",
])


# ==================================================================
# is_dc_format: 検出ロジック
# ==================================================================
def test_dc_format_detected_on_real_dc_line():
    assert is_dc_format(_DC_FIRST_LINE) is True


def test_dc_format_not_detected_on_ra_se_format():
    """RA+SE 形式(52 列)は DC として誤検出されてはならない。"""
    # 実 RA+SE は 52 列。is_jra_van_headerless が True であることを先に確認
    assert is_jra_van_headerless(_RA_FIRST_LINE) is True
    # かつ DC 検出は False
    assert is_dc_format(_RA_FIRST_LINE) is False


def test_dc_format_not_detected_on_empty():
    assert is_dc_format("") is False


def test_dc_format_not_detected_on_header_csv():
    """普通の英名ヘッダー付き CSV は DC ではない。"""
    header = "race_id,race_date,racecourse,race_number,horse_name,jockey,distance,surface,going"
    assert is_dc_format(header) is False


def test_dc_format_rejects_non_10digit_first_field():
    """1 列目が 10 桁数字でないものは DC ではない。"""
    line = "12345,1,2,3,4,5,6,7,8,9," + ",".join(["0"] * 36)
    assert is_dc_format(line) is False


def test_dc_format_rejects_extreme_column_counts():
    """30 列未満や 80 列以上の数値オンリー行は誤検出回避のため False。"""
    # 10 列だけ(短すぎ)
    short = ",".join(["0"] * 10)
    assert is_dc_format(short) is False
    # 200 列(長すぎ)
    long = ",".join(["0"] * 200)
    assert is_dc_format(long) is False


# ==================================================================
# load_race_card: DC 形式パース成功 + 簡易予想モードで動作
# ==================================================================
def test_load_race_card_parses_dc_with_attrs():
    """実 DC260509.CSV を読み込むと parse 成功し data_format='dc' が attrs に乗る。"""
    dc_path = ROOT / "data" / "raw" / "DC260509.CSV"
    if not dc_path.exists():
        return  # CI 環境等で raw データが無ければスキップ

    df = load_race_card(dc_path)
    # 読み込み成功
    assert df.attrs.get("data_format") == "dc", \
        f"data_format must be 'dc', got {df.attrs.get('data_format')}"
    # 過去走 dict が attrs に格納されている
    past_runs = df.attrs.get("dc_past_runs")
    assert isinstance(past_runs, dict) and len(past_runs) > 0, \
        "dc_past_runs が attrs に乗っていない"
    # DC 必須列が揃っている
    for col in ("race_id", "race_date", "racecourse", "race_number",
                "horse_id", "horse_number", "horse_name",
                "distance", "surface", "target_index"):
        assert col in df.columns, f"DC 必須列 {col} が欠落"
    # 36 レース分の馬データが期待される
    assert len(df) > 400, f"unexpected DC row count: {len(df)}"


def test_load_race_card_dc_jra_codes_decoded():
    """DC 形式の col[0][:2] が JRA 場名(新潟/東京/京都 等)に変換されている。"""
    dc_path = ROOT / "data" / "raw" / "DC260509.CSV"
    if not dc_path.exists():
        return
    df = load_race_card(dc_path)
    courses = set(df["racecourse"].unique())
    # 2026-05-09 開催の 3 場(新潟・東京・京都)
    assert {"新潟", "東京", "京都"} <= courses, \
        f"JRA 場名のデコードに失敗: {courses}"


def test_dc_error_message_constants():
    """DC_FORMAT_ERROR_MESSAGE が必須キーワードを含むこと。"""
    msg = DC_FORMAT_ERROR_MESSAGE
    for keyword in ["DC", "フルセット+単勝オッズ", "メインメニュー", "馬名", "騎手"]:
        assert keyword in msg, f"keyword '{keyword}' missing from DC_FORMAT_ERROR_MESSAGE"


# ==================================================================
# 既存形式の後方互換確認(回帰テスト)
# ==================================================================
def test_existing_morning_race_card_still_loads():
    """ヘッダ付き普通 CSV(legacy_dummy/morning_race_card_*.csv)が DC 検出で
    誤って弾かれないことを確認する境界テスト。

    v1.11.0 で正式入力フォーマットを DC 形式(46 列純数値)に統一したため、
    morning_race_card 系は data/test/legacy_dummy/ に移動済。本テストは
    「ヘッダ付き普通 CSV を DC として誤検出しない」境界条件の担保として
    維持する。
    """
    morning = ROOT / "data" / "test" / "legacy_dummy" / "morning_race_card_20260503.csv"
    if not morning.exists():
        return  # CI 等にファイルがない場合はスキップ
    df = load_race_card(morning)
    # 36 レース分くらいのデータが入っているはず(既存テスト時点)
    assert len(df) > 100, f"unexpected row count: {len(df)}"
    # 必須列が揃っていること
    for col in ("race_id", "race_date", "racecourse", "horse_name", "jockey"):
        assert col in df.columns, f"必須列 {col} が欠落"


# ==================================================================
# v1.13.1: 開催回コードが英数字(例 "2B")の DC 形式
# ==================================================================
# 背景: TARGET の DC 形式は col[0] の開催回コード(4-6 桁目)が英数字に
# なりうる(例 "05262B0101" の "2B")。旧 is_dc_format は col[0] 全体を
# isdigit() で要求していたため、回次が進んだ開催日の CSV が DC 検出されず
# header_csv 経路に落ちて KeyError で読込失敗していた。
# 注: 複数会場開催は元々 OK(dc_format_sample.csv は 3 会場で動作済)。
# 真因は「英数字の開催回コード」であって会場数ではない。

# 開催回コードに英字 "B" を含む DC 行(col[0]="05262B0101"、東京 05)
_DC_ALNUM_MEETING_LINE = (
    "05262B0101"
    + "," + ",".join(["0"] * 45)
)


def test_dc_format_detected_with_alphanumeric_meeting_code():
    """開催回コードが英数字(2B)でも DC として検出される(v1.13.1 修正)。"""
    assert is_dc_format(_DC_ALNUM_MEETING_LINE) is True


def test_dc_format_still_detected_with_numeric_meeting_code():
    """従来の数値開催回コードも引き続き DC として検出される(regression)。"""
    assert is_dc_format(_DC_FIRST_LINE) is True


def test_dc_format_rejects_unknown_venue_code():
    """場コードが既知 JRA(01〜10)でなければ DC ではない。"""
    line = "99262B0101," + ",".join(["0"] * 45)
    assert is_dc_format(line) is False


def test_dc_format_rejects_nonnumeric_race_number_position():
    """R番(6-8桁目)が数字でない col[0] は DC ではない(構造健全性)。"""
    # 6-8 桁目 "XX" を英字に: 場05/年26/開催13/R="XX"/馬番01
    line = "052613XX01," + ",".join(["0"] * 45)
    assert is_dc_format(line) is False


def test_dc_format_rejects_nonnumeric_data_columns():
    """col[1..9] に数値でない値があれば DC ではない(ヘッダ誤検出回避)。"""
    line = "05262B0101,foo," + ",".join(["0"] * 44)
    assert is_dc_format(line) is False


def test_multi_meeting_sample_loads_as_dc():
    """英数字開催回コードの実サンプルが data_format='dc' で読み込める。"""
    sample = ROOT / "data" / "test" / "dc_format_multi_venue_sample.csv"
    if not sample.exists():
        return
    df = load_race_card(sample)
    assert df.attrs.get("data_format") == "dc", \
        f"data_format must be 'dc', got {df.attrs.get('data_format')}"
    for col in ("race_id", "race_date", "racecourse", "race_number",
                "horse_id", "horse_number", "distance", "surface"):
        assert col in df.columns, f"DC 必須列 {col} が欠落"


def test_multi_meeting_sample_has_two_venues():
    """サンプルが 2 会場(東京・京都)を含み、会場が独立して取れる。"""
    sample = ROOT / "data" / "test" / "dc_format_multi_venue_sample.csv"
    if not sample.exists():
        return
    df = load_race_card(sample)
    courses = set(df["racecourse"].unique())
    assert {"東京", "京都"} <= courses, f"会場デコード失敗: {courses}"


def test_multi_meeting_sample_race_id_no_collision():
    """異なる会場のレースが別 race_id になる(東/京 プレフィックス衝突なし)。"""
    sample = ROOT / "data" / "test" / "dc_format_multi_venue_sample.csv"
    if not sample.exists():
        return
    df = load_race_card(sample)
    # 各 race_id 内で会場が一意(混在しない)
    per = df.groupby("race_id")["racecourse"].nunique()
    assert (per == 1).all(), "1 つの race_id に複数会場が混在している"
    # 会場別レース数が両方 > 0
    by_course = df.groupby("racecourse")["race_id"].nunique()
    assert by_course.get("東京", 0) > 0 and by_course.get("京都", 0) > 0


def test_alnum_meeting_parse_decodes_venue_and_meeting():
    """parse_dc_dataframe が英数字開催回コードを horse_id に保持し場を正しく decode。"""
    import pandas as pd
    from utils.target_format import parse_dc_dataframe
    raw = pd.DataFrame([["05262B0101"] + ["0"] * 45,
                        ["08263B0501"] + ["0"] * 45])
    rc, _past = parse_dc_dataframe(raw, target_date_iso="2026-05-30")
    assert rc.loc[0, "racecourse"] == "東京"
    assert rc.loc[1, "racecourse"] == "京都"
    # 開催回コード(2B / 3B)が horse_id に opaque 文字列として入る
    assert "2B" in rc.loc[0, "horse_id"]
    assert "3B" in rc.loc[1, "horse_id"]


def test_existing_dc_sample_detection_unchanged():
    """既存 DC サンプル(数値開催回)が引き続き DC 検出される(regression)。"""
    sample = ROOT / "data" / "test" / "dc_format_sample.csv"
    if not sample.exists():
        return
    df = load_race_card(sample)
    assert df.attrs.get("data_format") == "dc"


# ==================================================================
# 単体実行用ランナー
# ==================================================================
def _all_tests():
    funcs = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    fails = []
    for f in funcs:
        try:
            f()
            print(f"  ✓ {f.__name__}")
        except AssertionError as e:
            print(f"  ✗ {f.__name__}: {e}")
            fails.append(f.__name__)
        except Exception as e:
            print(f"  ✗ {f.__name__}: {type(e).__name__}: {e}")
            fails.append(f.__name__)
    print(f"\n{len(funcs) - len(fails)}/{len(funcs)} passed")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(_all_tests())
