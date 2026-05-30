"""historical/races.parquet の `odds` 列の正体を統計的に診断する(v1.14.0 Step 1)。

推測禁止。実データの統計だけで odds 列が「実 単勝オッズ」か否か、別指標なら
何かを切り分ける材料を出す。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PARQUET = ROOT / "data" / "historical" / "races.parquet"


def main():
    hist = pd.read_parquet(PARQUET)
    odds = pd.to_numeric(hist["odds"], errors="coerce")
    pos = pd.to_numeric(hist["finishing_position"], errors="coerce")
    pop = pd.to_numeric(hist["popularity"], errors="coerce")
    print(f"行数: {len(hist)} / レース: {hist['race_id'].nunique()}")

    print("\n=== odds 列 基本統計 ===")
    print(odds.describe(percentiles=[.01, .05, .1, .25, .5, .75, .9, .95, .99]).round(2).to_string())
    print(f"min={odds.min()} max={odds.max()} null率={odds.isna().mean()*100:.2f}% "
          f"ゼロ値={int((odds==0).sum())}")

    print("\n=== 着順別 odds 中央値(実単勝なら 1着ほど小さい) ===")
    for p in range(1, 11):
        s = odds[pos == p]
        if len(s):
            print(f"  {p}着: n={len(s):>5} median={s.median():>7.1f} mean={s.mean():>7.1f} "
                  f"min={s.min():>6.1f} max={s.max():>7.1f}")

    print("\n=== 人気別 odds 中央値(実単勝なら 1人気ほど小さく単調増加) ===")
    for pp in range(1, 16):
        s = odds[pop == pp]
        if len(s):
            print(f"  {pp:>2}人気: n={len(s):>5} median={s.median():>7.1f} mean={s.mean():>7.1f}")

    print("\n=== レース毎 Σ(1/odds)(健全な単勝本なら 1.1〜1.3) ===")
    race_sums = hist.assign(_o=odds).groupby("race_id")["_o"].apply(
        lambda x: (1.0 / x.clip(lower=0.1)).sum()
    )
    print(race_sums.describe(percentiles=[.05, .25, .5, .75, .95]).round(3).to_string())

    print("\n=== 1番人気馬の odds が最小か(実単勝なら 95%+) ===")
    def _pop1_is_min(g):
        o = pd.to_numeric(g["odds"], errors="coerce")
        pp = pd.to_numeric(g["popularity"], errors="coerce")
        if o.notna().sum() == 0 or (pp == 1).sum() == 0:
            return np.nan
        min_pop = g.loc[o.idxmin(), "popularity"]
        return float(min_pop) == 1.0
    checks = hist.groupby("race_id").apply(_pop1_is_min).dropna()
    print(f"1番人気 = 最小 odds のレース比率: {checks.mean()*100:.2f}% (n={len(checks)})")

    print("\n=== odds と popularity の相関(実単勝なら強い正、低オッズ=上位人気) ===")
    mask = odds.notna() & pop.notna()
    print(f"corr(odds, popularity) = {odds[mask].corr(pop[mask]):.3f}")
    print(f"corr(odds, finishing_position) = "
          f"{odds[odds.notna()&pos.notna()].corr(pos[odds.notna()&pos.notna()]):.3f}")

    print("\n=== odds 分布ヒストグラム ===")
    bins = [0, 1, 1.5, 2, 3, 5, 10, 20, 50, 100, 200, 500, 1000, 100000]
    counts, _ = np.histogram(odds.dropna(), bins=bins)
    mx = counts.max() or 1
    for i, c in enumerate(counts):
        bar = "█" * int(c / mx * 50)
        print(f"  {bins[i]:>6}-{bins[i+1]:<6}: {bar} {c}")

    # 「×10 スケール」仮説のチェック: odds/10 で Σ(1/(o/10)) を見ると 1.1-1.3 か
    print("\n=== ×0.1 スケール仮説(odds/10) ===")
    rs10 = hist.assign(_o=odds/10).groupby("race_id")["_o"].apply(
        lambda x: (1.0 / x.clip(lower=0.01)).sum()
    )
    print(f"  Σ(1/(odds/10)) 中央値: {rs10.median():.3f}")
    s1 = odds[pos == 1]
    print(f"  1着馬 (odds/10) 中央値: {(s1/10).median():.2f}")


if __name__ == "__main__":
    main()
