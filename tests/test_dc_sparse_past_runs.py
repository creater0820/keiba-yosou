"""
DC 形式 CSV で過去走数が行ごとにバラバラ(空セル混在)な実データの
リグレッションテスト。

歴史的経緯:
- DC260509.CSV(synthetic 全行 7 走完備)では問題なし
- 当日出馬表-47821f30.CSV(実データ、過去走 1〜7 走バラバラ + 空セル多数)で
  「The truth value of a Series is ambiguous」エラーが発生
- 原因: target_date_iso が None かつ filename に DCYYMMDD 規則が無い場合、
  parse_dc_dataframe 内で `date_str = "20" + yy_code(Series) + "-00-00"` と
  なり Series を bool 評価 → 例外
- 修正(commit xxx): date_str を必ず「真の文字列(ISO 形式)」に揃える、
  fallback として今日の日付を採用

このテストは過去走 0 / 1 / 3 / 7 走 + 空セル混在の synthetic CSV を構築して
parse_dc_dataframe が例外を出さず適切に dict を返すことを確認する。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_loader import load_race_card  # noqa: E402
from utils.target_format import parse_dc_dataframe  # noqa: E402


# 45 列(末尾カンマで 46 列扱い)を埋めるヘルパ
def _make_dc_row(
    *,
    race_id: str,
    distance: int = 1800,
    track_code: int = 1,
    field_size: int = 10,
    zi: int = 100,
    weeks_to_today: int = 4,
    past_runs: list[tuple[int, int, int, int, int]] | None = None,
) -> str:
    """1 行分の DC CSV 行を組み立てる。past_runs は最大 7 個。"""
    today = [
        race_id,                # col[0]
        "7",                    # col[1] class
        str(distance),           # col[2]
        str(track_code),         # col[3]
        str(field_size),         # col[4]
        str(zi),                 # col[5]
        str(weeks_to_today),     # col[6]
        "0",                     # col[7]
        "0",                     # col[8]
        "0",                     # col[9]
    ]
    past_blocks: list[str] = []
    for run in (past_runs or []):
        past_blocks.extend(str(x) for x in run)
    # 残りを空セルでパディング(7 走 × 5 列 = 35 列)
    while len(past_blocks) < 35:
        past_blocks.append("")
    line = ",".join(today + past_blocks) + ","   # 末尾カンマ(46 列扱いに)
    return line


def _make_csv(rows: list[str]) -> str:
    return "\n".join(rows) + "\n"


# ==================================================================
# T1: 過去走 0 走でも parse_dc_dataframe は例外を出さない
# ==================================================================
def test_zero_past_runs_no_exception():
    csv = _make_csv([
        _make_dc_row(race_id="0426130201", past_runs=[]),
        _make_dc_row(race_id="0426130202", past_runs=[]),
    ])
    raw = pd.read_csv(io.StringIO(csv), header=None, dtype=str)
    # target_date_iso 明示なし → 今日の日付フォールバック
    df, past = parse_dc_dataframe(raw, target_date_iso=None)
    assert len(df) == 2
    # 全頭の過去走は全て None でパディングされる
    for hid, runs in past.items():
        assert len(runs) == 5
        assert all(r is None for r in runs)


# ==================================================================
# T2: 過去走 1 走のみ + 残り空セル(実データの行 1 と同等)
# ==================================================================
def test_one_past_run_with_empty_cells():
    csv = _make_csv([
        _make_dc_row(
            race_id="0426130201",
            past_runs=[(7, 1400, 1, 0, 87)],  # 1 走のみ
        ),
    ])
    raw = pd.read_csv(io.StringIO(csv), header=None, dtype=str)
    df, past = parse_dc_dataframe(raw, target_date_iso="2026-05-09")
    runs = list(past.values())[0]
    valid_runs = [r for r in runs if r is not None]
    assert len(valid_runs) == 1
    assert valid_runs[0]["distance"] == 1400
    assert valid_runs[0]["adjusted_time"] == 87


# ==================================================================
# T3: 過去走 4 走で打ち切り(実データの行 3 相当)
# ==================================================================
def test_four_past_runs_truncated():
    csv = _make_csv([
        _make_dc_row(
            race_id="0426130203",
            past_runs=[
                (7, 1700, 1, 3, 0),
                (7, 2200, 0, 8, 0),
                (7, 2200, 0, 7, 93),
                (15, 2000, 0, 0, 88),
            ],
        ),
    ])
    raw = pd.read_csv(io.StringIO(csv), header=None, dtype=str)
    df, past = parse_dc_dataframe(raw, target_date_iso="2026-05-09")
    runs = list(past.values())[0]
    valid_runs = [r for r in runs if r is not None]
    assert len(valid_runs) == 4
    assert valid_runs[0]["distance"] == 1700


# ==================================================================
# T4: target_date_iso=None でも例外を出さない(以前 Series bool で失敗していた)
# ==================================================================
def test_none_target_date_no_series_bool_error():
    """target_date_iso=None で parse_dc_dataframe が Series bool エラーを出さない。

    歴史的: 旧コードは `date_str = "20" + yy_code + "-00-00"` で yy_code が
    Series だったため `if date_str` で ValueError 発生。今は今日の日付に
    フォールバックされるため例外なし。
    """
    csv = _make_csv([
        _make_dc_row(race_id="0426130201", past_runs=[(7, 1400, 1, 0, 87)]),
    ])
    raw = pd.read_csv(io.StringIO(csv), header=None, dtype=str)
    # target_date_iso=None でも例外なし
    df, past = parse_dc_dataframe(raw, target_date_iso=None)
    assert len(df) == 1
    # race_date は今日の日付(YYYY-MM-DD 文字列)になっている
    rd = df["race_date"].iloc[0]
    assert isinstance(rd, str) and len(rd) == 10
    # ISO 形式として parse 可能
    pd.Timestamp(rd)


def test_invalid_target_date_falls_back_to_today():
    """不正な target_date_iso(空文字 / "2026-00-00" 等)もフォールバック発動。"""
    csv = _make_csv([
        _make_dc_row(race_id="0426130201", past_runs=[(7, 1400, 1, 0, 87)]),
    ])
    raw = pd.read_csv(io.StringIO(csv), header=None, dtype=str)
    for bad in ["", "abc", "2026-00-00", "2026-13-99"]:
        df, past = parse_dc_dataframe(raw, target_date_iso=bad)
        rd = df["race_date"].iloc[0]
        assert isinstance(rd, str) and len(rd) == 10
        pd.Timestamp(rd)  # parse 成功すること


# ==================================================================
# T5: 失敗 CSV(当日出馬表-47821f30.CSV)の end-to-end ロード成功
# ==================================================================
def test_real_failing_csv_loads_without_error():
    """当日出馬表-47821f30.CSV(実データ)をエラー無くロードできること。"""
    real_path = ROOT / "data" / "raw" / "当日出馬表-47821f30.CSV"
    if not real_path.exists():
        return  # CI で raw データが無い場合はスキップ
    df = load_race_card(real_path)
    assert df.attrs.get("data_format") == "dc", "DC 形式として認識されること"
    assert len(df) > 100, f"行数が少なすぎる: {len(df)}"
    past = df.attrs.get("dc_past_runs")
    assert isinstance(past, dict) and len(past) > 0


def test_mixed_past_run_counts_across_horses():
    """1 つの CSV 内で 0 走 / 3 走 / 7 走 が混在しても正常パース。"""
    csv = _make_csv([
        _make_dc_row(race_id="0426130201", past_runs=[]),                          # 0 走
        _make_dc_row(race_id="0426130202", past_runs=[(7,1400,1,4,90)] * 3),       # 3 走
        _make_dc_row(race_id="0426130203", past_runs=[(7,1400,1,4,90)] * 7),       # 7 走
    ])
    raw = pd.read_csv(io.StringIO(csv), header=None, dtype=str)
    df, past = parse_dc_dataframe(raw, target_date_iso="2026-05-09")
    counts = {}
    for hid, runs in past.items():
        counts[hid] = sum(1 for r in runs if r is not None)
    # 直近 5 走に揃えるので最大 5 走、不足分はそのまま 0/3
    assert sorted(counts.values()) == [0, 3, 5]


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
