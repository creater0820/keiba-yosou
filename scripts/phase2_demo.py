"""
Phase 2 動作確認デモ:
当日出馬表 + historical 過去戦績から、本ロジック v1.0 / Step 1 の
○マーク収集ルールを各馬に適用して結果を表示する。

使い方:
    python scripts/phase2_demo.py [race_id]
    例: python scripts/phase2_demo.py R20260503-京11

引数省略時は morning_race_card_20260503.csv の 京都11R 天皇賞春で実行。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# プロジェクトルートを sys.path へ
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_loader import load_race_card  # noqa: E402
from utils.race_history import (  # noqa: E402
    get_recent_n_runs,
    determine_running_style,
)
from utils.onmark_rules import collect_onmarks  # noqa: E402

DEFAULT_CARD = "data/test/morning_race_card_20260503.csv"
DEFAULT_RACE = "R20260503-京11"
DEFAULT_HISTORICAL = "data/historical/races.parquet"


def main() -> None:
    race_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RACE

    card = load_race_card(DEFAULT_CARD)
    race = card[card["race_id"] == race_id]
    if race.empty:
        print(f"❌ race_id={race_id} が出馬表に見つかりません")
        sys.exit(1)

    head = race.iloc[0]
    target_date = str(head["race_date"])
    print(
        f"=== {head['racecourse']} {int(head['race_number'])}R "
        f"{head['race_name']} {int(head['distance'])}m {head['surface']} "
        f"({head['going']}) / {len(race)} 頭立て ==="
    )

    historical = pd.read_parquet(DEFAULT_HISTORICAL)

    rows: list[tuple] = []
    for _, horse in race.iterrows():
        hid = str(horse["horse_id"])
        recent = get_recent_n_runs(hid, target_date, historical, n=5)
        style = determine_running_style(recent)
        n_marks, reasons = collect_onmarks(recent)
        rows.append((horse["horse_number"], horse["horse_name"], style, n_marks, reasons))

    rows.sort(key=lambda x: (-x[3], int(x[0])))

    print(f"\n{'馬番':>4} {'馬名':>16} {'脚質':>6} {'○':>3}  該当ルール")
    print("-" * 100)
    for hn, name, style, marks, reasons in rows:
        badge = ""
        if marks >= 5:
            badge = "★◎候補"
        elif marks >= 3:
            badge = "○注目"
        print(f"{int(hn):>4} {name:>16} {style:>6} {marks:>3}  {badge}")
        for r in reasons:
            print(f"          ↳ {r}")

    print()
    honmei = [r for r in rows if r[3] >= 5]
    chumoku = [r for r in rows if 3 <= r[3] < 5]
    other = [r for r in rows if 0 < r[3] < 3]
    print(f"=== 集計 ===")
    print(f"  ◎候補(○≥5)  : {len(honmei)} 頭")
    print(f"  ○注目(○3-4) : {len(chumoku)} 頭")
    print(f"  ○ 1-2 個   : {len(other)} 頭")
    print(f"  ○ 0 個     : {len(rows) - len(honmei) - len(chumoku) - len(other)} 頭")

    if not honmei:
        print(
            "\n  注: ◎候補は spec 通り 5個以上 の閾値で判定。長距離 G1 のような"
            "出走馬の経歴が偏るレースでは ○ が累積しにくく、◎候補が 0 頭になることが"
            "ある(spec「該当馬がいない場合は ◎なし」想定通り)。Phase 3 の減点・補正ルール"
            "適用後に再判定する設計。"
        )


if __name__ == "__main__":
    main()
