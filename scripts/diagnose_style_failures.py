"""v1.9.1 Step 1: 脚質判定不能馬の実態調査スクリプト

DC260509.CSV + historical/races.parquet を使って:
  1. determine_running_style() が「不明(先行扱い)」を返す馬を一覧化
  2. その馬がどのカテゴリ(DC マッチ失敗 / 過去走 0 / 過去走 1-2 走 /
     corner_1 欠損で 3 件未満)かを分類
  3. corner_1 欠損馬を corner_2 / corner_3 / corner_4 で救済できるかを試算
  4. 結果を JSON で出力し、CLAUDE.md v1.9.1 セクションに転記する

実機データのみで動作(推測禁止のため)。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from data_loader import enrich_dc_with_historical, load_race_card  # noqa: E402
from utils.race_history import (  # noqa: E402
    determine_running_style,
    get_recent_runs_for_race,
)


DC_CSV = ROOT / "data" / "raw" / "DC260509.CSV"
HISTORICAL = ROOT / "data" / "historical" / "races.parquet"
TODAY_GOING = "良"


def _count_valid_corner(runs: list[dict | None], corner_key: str) -> int:
    """直近 5 走から corner_key が有効値(非 NaN)の数を返す。"""
    n = 0
    for r in runs[:5]:
        if r is None:
            continue
        v = r.get(corner_key)
        if v is None:
            continue
        try:
            if not pd.isna(v):
                n += 1
        except (TypeError, ValueError):
            n += 1  # int 等は NaN ではない
    return n


def _classify_horse(
    horse_row: dict,
    runs: list[dict | None],
    style: str,
) -> str:
    """脚質不明だった場合の原因カテゴリを分類。"""
    if style != "不明(先行扱い)":
        return "判定済"

    n_runs = sum(1 for r in runs if r is not None)
    matched_hist_id = horse_row.get("matched_historical_horse_id")

    if matched_hist_id is None or pd.isna(matched_hist_id):
        if n_runs == 0:
            return "失敗A: DC マッチ失敗 + 過去走 0(真の新馬 or DB 未登録)"
        return f"失敗B: DC マッチ失敗 + 過去走 {n_runs} 走あるが historical 引当不可"

    # マッチ成功なのに脚質不明 → corner_1 欠損が原因
    n_c1 = _count_valid_corner(runs, "corner_1")
    n_c2 = _count_valid_corner(runs, "corner_2")
    n_c3 = _count_valid_corner(runs, "corner_3")
    n_c4 = _count_valid_corner(runs, "corner_4")

    if n_runs == 0:
        return "失敗C: マッチ成功だが target 以前の過去走 0"
    if n_runs < 3:
        return f"失敗D: マッチ成功 + 過去走 {n_runs} 走(3 走未満でサンプル不足)"
    if n_c1 < 3:
        return (
            f"失敗E: 過去走 {n_runs} 走あるが corner_1 有効値 {n_c1} 件 "
            f"(corner_2={n_c2}, corner_3={n_c3}, corner_4={n_c4})"
        )
    return f"失敗X: 原因不明(c1={n_c1}, runs={n_runs})"


def _can_be_rescued_by_corner_fallback(runs: list[dict | None]) -> bool:
    """corner_1 が 3 件未満でも corner_3 or corner_4 で救済できるか。"""
    for key in ("corner_3", "corner_4", "corner_2"):
        if _count_valid_corner(runs, key) >= 3:
            return True
    return False


def main():
    print("=" * 70)
    print("v1.9.1 Step 1: 脚質判定不能馬の実態調査")
    print(f"  DC CSV     : {DC_CSV}")
    print(f"  historical : {HISTORICAL}")
    print(f"  today_going: {TODAY_GOING}")
    print("=" * 70)

    historical = pd.read_parquet(HISTORICAL)
    print(f"\n[historical] 行数 {len(historical):,}, "
          f"unique horses {historical['horse_id'].nunique():,}")

    race_card = load_race_card(str(DC_CSV))
    print(f"[race_card ] DC 形式 {len(race_card)} 馬, "
          f"format={race_card.attrs.get('data_format')}")

    enriched = enrich_dc_with_historical(
        race_card, historical, today_going=TODAY_GOING,
    )
    print(f"[enriched  ] match_count={enriched.attrs.get('dc_match_count')} / "
          f"total={enriched.attrs.get('dc_total_count')}")

    past_runs_by_horse = enriched.attrs.get("dc_past_runs", {})

    # 全馬について determine_running_style を呼ぶ
    unknown_horses: list[dict] = []
    style_counter = Counter()
    failure_counter = Counter()
    rescue_counter = Counter()

    for row in enriched.to_dict("records"):
        hid = str(row["horse_id"])
        runs = past_runs_by_horse.get(hid, [])
        style = determine_running_style(runs)
        style_counter[style] += 1

        if style == "不明(先行扱い)":
            cat = _classify_horse(row, runs, style)
            failure_counter[cat] += 1
            can_rescue = _can_be_rescued_by_corner_fallback(runs)
            rescue_counter["救済可" if can_rescue else "救済不可"] += 1
            unknown_horses.append({
                "race_id": str(row.get("race_id", "")),
                "horse_number": int(row.get("horse_number", 0) or 0),
                "horse_name": str(row.get("horse_name", "")),
                "matched_hist_id": str(row.get("matched_historical_horse_id") or ""),
                "category": cat,
                "n_runs": sum(1 for r in runs if r is not None),
                "n_c1": _count_valid_corner(runs, "corner_1"),
                "n_c2": _count_valid_corner(runs, "corner_2"),
                "n_c3": _count_valid_corner(runs, "corner_3"),
                "n_c4": _count_valid_corner(runs, "corner_4"),
                "can_rescue": can_rescue,
            })

    total = sum(style_counter.values())
    unknown = style_counter.get("不明(先行扱い)", 0)

    print("\n" + "=" * 70)
    print("【脚質判定結果サマリー】")
    print("=" * 70)
    for sty in ("逃げ", "先行", "差し", "追込", "不明(先行扱い)"):
        n = style_counter.get(sty, 0)
        pct = (n / total * 100) if total else 0
        print(f"  {sty:>14}: {n:4d} 頭 ({pct:5.1f}%)")
    print(f"  {'合計':>14}: {total:4d} 頭")
    print()
    print(f"判定不能率: {unknown}/{total} = {unknown/total*100:.2f}%")

    print("\n" + "=" * 70)
    print("【不明馬の原因カテゴリ別内訳】")
    print("=" * 70)
    for cat, n in failure_counter.most_common():
        print(f"  {n:4d} 頭: {cat}")

    print("\n" + "=" * 70)
    print("【corner フォールバック救済可否】")
    print("=" * 70)
    for cat, n in rescue_counter.most_common():
        print(f"  {n:4d} 頭: {cat}")
    rescuable = rescue_counter.get("救済可", 0)
    if unknown:
        print(f"\n→ 不明 {unknown} 頭中 {rescuable} 頭が "
              f"corner_2/3/4 フォールバックで救済可能 "
              f"({rescuable/unknown*100:.1f}%)")

    # サンプル出力(最初の 10 頭)
    print("\n" + "=" * 70)
    print("【不明馬サンプル(先頭 10 頭)】")
    print("=" * 70)
    for h in unknown_horses[:10]:
        print(
            f"  {h['race_id']} 馬番{h['horse_number']:2d} "
            f"{h['horse_name']:>12} | runs={h['n_runs']} "
            f"c1={h['n_c1']} c2={h['n_c2']} c3={h['n_c3']} c4={h['n_c4']} "
            f"{'[救済可]' if h['can_rescue'] else '[救済不可]'}"
        )

    # JSON 保存
    out_path = ROOT / "scripts" / "diagnose_style_failures_output.json"
    out_path.write_text(json.dumps({
        "summary": {
            "total": total,
            "style_breakdown": dict(style_counter),
            "unknown_rate_pct": round(unknown / total * 100, 2) if total else 0,
            "failure_categories": dict(failure_counter),
            "rescue": dict(rescue_counter),
            "rescuable_pct": round(rescuable / unknown * 100, 2) if unknown else 0,
        },
        "unknown_horses": unknown_horses,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ JSON 出力: {out_path}")


if __name__ == "__main__":
    main()
