"""v1.14.0: odds 列修正の健全性テスト。

historical/races.parquet の odds 列が真の単勝オッズ(SE col[48])であること、
SE パーサが col[48] を odds にマッピングすること、スキーマ非破壊を検証する。

データファイル(parquet / SE サンプル)が無い環境では各テストを skip する。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
PARQUET = ROOT / "data" / "historical" / "races.parquet"
SE_SAMPLE = ROOT / "data" / "test" / "target_history_sample.csv"


def _load_parquet():
    if not PARQUET.exists():
        pytest.skip("races.parquet が無い")
    return pd.read_parquet(PARQUET)


# =====================================================================
# パーサ: SE col[48] → odds の正しさ
# =====================================================================

def test_parse_se_maps_col48_as_real_odds():
    """parse_se_csv の odds(col[48])が実単勝オッズの分布を持つ(1着が低オッズ)。"""
    if not SE_SAMPLE.exists():
        pytest.skip("SE サンプルが無い")
    from utils.target_history_parser import parse_se_csv
    df = parse_se_csv(str(SE_SAMPLE)).df
    o = pd.to_numeric(df["odds"], errors="coerce")
    win = o[pd.to_numeric(df["finishing_position"], errors="coerce") == 1]
    # 1着馬の単勝オッズ中央値は実市場なら 3〜6 倍程度
    assert 2.5 <= win.median() <= 7.0, f"1着 odds 中央値が不自然: {win.median()}"


def test_parse_se_odds_correlates_with_finish():
    """実オッズなら低オッズ馬ほど上位入着(odds と着順に正の相関)。"""
    if not SE_SAMPLE.exists():
        pytest.skip("SE サンプルが無い")
    from utils.target_history_parser import parse_se_csv
    df = parse_se_csv(str(SE_SAMPLE)).df
    o = pd.to_numeric(df["odds"], errors="coerce")
    pos = pd.to_numeric(df["finishing_position"], errors="coerce")
    m = o.notna() & pos.notna() & (o > 0) & (pos > 0)
    assert o[m].corr(pos[m]) > 0.1, "odds と着順が無相関(実オッズでない疑い)"


# =====================================================================
# 修正後 parquet の健全性
# =====================================================================

def test_parquet_odds_winner_median_realistic():
    """1着馬の odds 中央値が 3〜6 倍(実単勝オッズの証拠)。"""
    h = _load_parquet()
    o = pd.to_numeric(h["odds"], errors="coerce")
    valid = h[o.notna() & (o > 0)]
    win = pd.to_numeric(valid[valid["finishing_position"] == 1]["odds"], errors="coerce")
    assert 3.0 <= win.median() <= 6.0, f"1着 odds 中央値が不自然: {win.median()}"


def test_parquet_odds_book_sum_healthy():
    """有効 odds 行のレース毎 Σ(1/odds) 中央値が 1.0〜1.4(健全な単勝本)。"""
    h = _load_parquet()
    o = pd.to_numeric(h["odds"], errors="coerce")
    valid = h[o.notna() & (o > 0)].copy()
    valid["_o"] = pd.to_numeric(valid["odds"], errors="coerce")
    rs = valid.groupby("race_id")["_o"].apply(lambda x: (1.0 / x.clip(lower=0.1)).sum())
    assert 1.0 <= rs.median() <= 1.4, f"Σ(1/odds) 中央値が不健全: {rs.median()}"


def test_parquet_favorite_has_min_odds():
    """有効 odds 行で 1番人気=最小odds のレース比率が 90% 以上。"""
    h = _load_parquet()
    o = pd.to_numeric(h["odds"], errors="coerce")
    valid = h[o.notna() & (o > 0)]

    def _p1(g):
        oo = pd.to_numeric(g["odds"], errors="coerce")
        pp = pd.to_numeric(g["popularity"], errors="coerce")
        if oo.notna().sum() == 0 or (pp == 1).sum() == 0:
            return np.nan
        return float(g.loc[oo.idxmin(), "popularity"]) == 1.0

    chk = valid.groupby("race_id").apply(_p1).dropna()
    assert chk.mean() >= 0.90, f"1番人気=最小odds率が低い: {chk.mean()*100:.1f}%"


def test_parquet_odds_correlates_with_popularity():
    """odds と popularity に強い正の相関(実単勝オッズの証拠)。"""
    h = _load_parquet()
    o = pd.to_numeric(h["odds"], errors="coerce")
    pop = pd.to_numeric(h["popularity"], errors="coerce")
    m = o.notna() & pop.notna() & (o > 0)
    assert o[m].corr(pop[m]) > 0.5, "odds と人気が弱相関(実オッズでない疑い)"


# =====================================================================
# スキーマ非破壊 / NaN ポリシー
# =====================================================================

def test_parquet_schema_intact():
    """26 列・odds は float64(既存スキーマ非破壊)。"""
    h = _load_parquet()
    assert h.shape[1] == 26, f"列数が 26 でない: {h.shape[1]}"
    assert h["odds"].dtype == np.float64, f"odds dtype: {h['odds'].dtype}"


def test_parquet_pre2025_odds_nan():
    """真オッズソースの無い 2023-24 は odds=NaN(誤った値を残さない)。"""
    h = _load_parquet()
    old = h[(h["race_date"] >= "2023-01-01") & (h["race_date"] <= "2024-12-31")]
    if len(old) == 0:
        pytest.skip("2023-24 データなし")
    o = pd.to_numeric(old["odds"], errors="coerce")
    assert o.notna().sum() == 0, f"2023-24 に非NaN odds が残存: {o.notna().sum()}"


def test_parquet_post2025_odds_present():
    """2025 年以降は真オッズが入っている(大半が非NaN)。"""
    h = _load_parquet()
    rec = h[h["race_date"] >= "2025-01-01"]
    if len(rec) == 0:
        pytest.skip("2025+ データなし")
    o = pd.to_numeric(rec["odds"], errors="coerce")
    assert o.notna().mean() > 0.9, f"2025+ の odds 充足率が低い: {o.notna().mean()*100:.1f}%"


def test_parquet_other_columns_unchanged_dtype():
    """odds 以外の主要列の dtype が既存仕様のまま(回帰防止)。

    文字列列は pandas/pyarrow のバージョンで 'object' / 'str' / 'string' の
    いずれかになりうるため、string 系であることだけを確認する。数値列は厳密。
    """
    h = _load_parquet()
    assert str(h["race_id"].dtype) in ("object", "str", "string")
    assert str(h["finishing_position"].dtype) == "Int64"
    assert str(h["popularity"].dtype) == "Int64"
    assert str(h["last_3f"].dtype) == "float64"


def test_parquet_dtypes_match_backup():
    """修正前(バックアップ)と修正後で全 26 列の dtype が完全一致(非破壊保証)。"""
    if not PARQUET.exists():
        pytest.skip("races.parquet が無い")
    bak = PARQUET.with_suffix(".parquet.bak.v13_before_v14_odds_fix")
    if not bak.exists():
        pytest.skip("バックアップが無い")
    old = pd.read_parquet(bak)
    new = pd.read_parquet(PARQUET)
    assert list(old.columns) == list(new.columns)
    for c in old.columns:
        assert str(old[c].dtype) == str(new[c].dtype), \
            f"{c} の dtype が変化: {old[c].dtype} -> {new[c].dtype}"


def test_fix_script_health_helper_importable():
    """修正スクリプトの主要関数が import できる(再現性担保)。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fix_odds_v14", ROOT / "scripts" / "fix_odds_column_v14.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "_build_real_odds_map")
    assert hasattr(mod, "_health_report")
