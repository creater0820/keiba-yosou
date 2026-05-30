"""historical/races.parquet の odds 列を真の単勝オッズに修正する(v1.14.0)。

診断(scripts/diagnose_odds_column_v14.py)で、parquet の `odds` 列は実 単勝
オッズではなく ~48 中心の別指標(SE col[51] 由来、着順/人気と無相関)だった
ことが判明。一方 SE 形式の **col[48] が真の単勝オッズ**で、現行パーサ
(utils/target_history_parser.parse_se_csv)は col[48] を正しく odds に
マッピングしている。

本スクリプトは:
1. 既存 parquet をバックアップ
2. 真の単勝オッズソース(SE 形式 CSV、parse_se_csv で col[48])を読み、
   (race_id, horse_id) で parquet に join して odds を上書き
3. ソースが無い行(SE でカバーされない日付 = 2023-24)は **NaN**
   (誤った ~48 値を残すより「不明」が正しい。live 予想は parquet odds を
    使わないので影響なし。backtest は NaN を自動スキップする)
4. 健全性チェック(1着 median 3-5 / Σ(1/odds) 1.1-1.3 / 1番人気=min 90%+)

CLI:
    --se <SE CSV or dir>   真の単勝オッズソース(複数可)
    --dry-run              書き込まず統計だけ表示
    --no-backup            バックアップを作らない(非推奨)
    --restore <bak>        バックアップから復元

dtype は float64 のまま(既存スキーマ非破壊)。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PARQUET = ROOT / "data" / "historical" / "races.parquet"

from utils.target_history_parser import parse_se_csv


def _collect_se_paths(arg: str) -> list[Path]:
    p = Path(arg)
    if p.is_dir():
        return sorted([q for q in p.iterdir() if q.suffix.lower() == ".csv"])
    return [p]


def _build_real_odds_map(se_args: list[str]) -> dict[tuple[str, str], float]:
    """SE 形式 CSV 群から {(race_id, horse_id): 単勝オッズ(col48)} を作る。"""
    odds_map: dict[tuple[str, str], float] = {}
    for arg in se_args:
        for path in _collect_se_paths(arg):
            res = parse_se_csv(str(path))
            df = res.df
            o = pd.to_numeric(df["odds"], errors="coerce")
            for rid, hid, ov in zip(df["race_id"].astype(str),
                                    df["horse_id"].astype(str), o):
                if pd.notna(ov) and ov > 0:
                    odds_map[(rid, hid)] = float(ov)
            print(f"  SE 読込: {path.name} → {len(df)} 行 "
                  f"(date {res.date_min}〜{res.date_max})")
    return odds_map


def _health_report(df: pd.DataFrame, label: str) -> None:
    o = pd.to_numeric(df["odds"], errors="coerce")
    pos = pd.to_numeric(df["finishing_position"], errors="coerce")
    pop = pd.to_numeric(df["popularity"], errors="coerce")
    valid = df[o.notna() & (o > 0)]
    ov = pd.to_numeric(valid["odds"], errors="coerce")
    print(f"\n=== 健全性 [{label}] ===")
    print(f"  有効 odds 行: {len(valid)}/{len(df)} ({len(valid)/len(df)*100:.1f}%)")
    if len(valid) == 0:
        return
    win = ov[pd.to_numeric(valid["finishing_position"], errors="coerce") == 1]
    print(f"  1着馬 odds 中央値: {win.median():.2f} (健全=3〜5)")
    rs = valid.assign(_o=ov).groupby("race_id")["_o"].apply(
        lambda x: (1.0 / x.clip(lower=0.1)).sum())
    print(f"  Σ(1/odds) 中央値: {rs.median():.3f} (健全=1.1〜1.3)")
    def _p1(g):
        oo = pd.to_numeric(g["odds"], errors="coerce")
        pp = pd.to_numeric(g["popularity"], errors="coerce")
        if oo.notna().sum() == 0 or (pp == 1).sum() == 0:
            return np.nan
        return float(g.loc[oo.idxmin(), "popularity"]) == 1.0
    chk = valid.groupby("race_id").apply(_p1).dropna()
    if len(chk):
        print(f"  1番人気=最小odds率: {chk.mean()*100:.1f}% (健全=90%+)")
    cpop = ov.corr(pd.to_numeric(valid["popularity"], errors="coerce"))
    print(f"  corr(odds, popularity): {cpop:.3f} (健全=強い正)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--se", action="append", default=[],
                    help="真の単勝オッズソース(SE 形式 CSV / ディレクトリ)。複数可")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--restore", default=None)
    args = ap.parse_args()

    if args.restore:
        shutil.copy2(args.restore, PARQUET)
        print(f"復元: {args.restore} → {PARQUET}")
        return

    if not args.se:
        print("--se で真の単勝オッズソース(SE 形式 CSV)を指定してください。")
        return

    hist = pd.read_parquet(PARQUET)
    print(f"parquet: {len(hist)} 行 / {hist['race_id'].nunique()} レース")
    _health_report(hist, "修正前")

    print("\n真の単勝オッズソースを読み込み中...")
    odds_map = _build_real_odds_map(args.se)
    print(f"真の odds マップ: {len(odds_map)} (race_id, horse_id) エントリ")

    keys = list(zip(hist["race_id"].astype(str), hist["horse_id"].astype(str)))
    new_odds = np.array([odds_map.get(k, np.nan) for k in keys], dtype=np.float64)
    n_real = int(np.isfinite(new_odds).sum())
    n_nan = len(new_odds) - n_real
    print(f"\n上書き対象: 真odds={n_real} 行 / NaN(ソース無)={n_nan} 行")

    hist2 = hist.copy()
    hist2["odds"] = new_odds  # float64 維持
    _health_report(hist2, "修正後")

    # dtype 健全性(既存スキーマ非破壊の確認)
    assert hist2["odds"].dtype == np.float64, "odds dtype が float64 でない"
    assert list(hist2.columns) == list(hist.columns), "列構成が変わった"

    if args.dry_run:
        print("\n[dry-run] 書き込みはスキップしました。")
        return

    if not args.no_backup:
        bak = PARQUET.with_suffix(".parquet.bak.v13_before_v14_odds_fix")
        if not bak.exists():
            shutil.copy2(PARQUET, bak)
            print(f"\nバックアップ作成: {bak}")
        else:
            print(f"\nバックアップ既存(上書きせず): {bak}")

    hist2.to_parquet(PARQUET, index=False)
    print(f"書き込み完了: {PARQUET} ({len(hist2)} 行)")


if __name__ == "__main__":
    main()
