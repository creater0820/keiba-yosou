"""
本ロジック v1.0 / Step 5: 買い目戦略・補正。

CLAUDE.md「推奨馬選定ロジック(本ロジック v1.0)」の Step 5 を担当する純関数群。

- ルール 23: ダート + 不良馬場 → 逃げ脚質に○+1 補正
- ルール  2: 1番人気の枠の偶奇でワイド候補をフィルタ
- 推奨買い目の生成(全券種)
"""

from __future__ import annotations

from dataclasses import dataclass

from utils.judgment_engine import HorseMarkData, WideCandidate


# ==================================================================
# Step 5 / 補正ルール 23: ダート不良で逃げに加点
# ==================================================================

DIRT_HEAVY_BONUS = 1   # ○+1


def apply_dirt_heavy_correction(
    horses: list[HorseMarkData],
    race_meta: dict,
) -> list[HorseMarkData]:
    """
    ダート + 不良馬場 のレースでは逃げ脚質の馬に○+1 を加算する。

    元のリストを変更せず、新しい HorseMarkData リストを返す(immutable に近い扱い)。
    対象外のレース(芝、または不良以外)では入力をそのまま返す。
    """
    surface = str(race_meta.get("surface", "")).strip()
    going = str(race_meta.get("going", "")).strip()

    if surface != "ダ" or going != "不良":
        return list(horses)

    out: list[HorseMarkData] = []
    for h in horses:
        if h.running_style == "逃げ":
            new_marks = h.marks_count + DIRT_HEAVY_BONUS
            new_reasons = list(h.matched_rules) + [
                f"R23: ダート不良 + 逃げ → ○+{DIRT_HEAVY_BONUS}(計 {new_marks})"
            ]
            out.append(HorseMarkData(
                horse_id=h.horse_id,
                horse_name=h.horse_name,
                horse_number=h.horse_number,
                frame_number=h.frame_number,
                popularity=h.popularity,
                running_style=h.running_style,
                marks_count=new_marks,
                matched_rules=new_reasons,
                last_finishing_position=h.last_finishing_position,
            ))
        else:
            out.append(h)
    return out


# ==================================================================
# Step 5 / ルール 2: 1番人気の枠の偶奇でワイド候補絞り込み
# ==================================================================

def filter_by_frame_parity(
    wide_candidates: list[WideCandidate],
    horses: list[HorseMarkData],
) -> list[WideCandidate]:
    """
    単勝1番人気の枠が奇数(1,3,5,7) → 候補を奇数枠の馬に絞る。
    単勝1番人気の枠が偶数(2,4,6,8) → 候補を偶数枠の馬に絞る。

    1番人気が見つからなければ候補そのまま。
    枠番情報が wide_candidates 単独からは取れないので、horses リストから
    horse_id → frame_number を引いて参照する。
    """
    fav = next((h for h in horses if h.popularity == 1), None)
    if fav is None:
        return list(wide_candidates)

    fav_parity = fav.frame_number % 2  # 1 = 奇数、0 = 偶数

    frame_by_id = {h.horse_id: h.frame_number for h in horses}

    filtered: list[WideCandidate] = []
    for c in wide_candidates:
        f = frame_by_id.get(c.horse_id)
        if f is None:
            continue
        if f % 2 == fav_parity:
            # 同じ偶奇 → 残す
            filtered.append(c)
    return filtered


# ==================================================================
# Step 5 / 買い目生成(全券種)
# ==================================================================

@dataclass
class BetTicket:
    """1 枚の馬券。"""
    bet_type: str          # "単勝" / "複勝" / "馬連" / "三連複" / "ワイド"
    horse_numbers: list[int]   # 含まれる馬番
    horse_names: list[str]     # 含まれる馬名(順序対応)
    note: str = ""             # 補足(BOX / 流し 等)


@dataclass
class BettingPlan:
    """1 レース分の全推奨買い目。"""
    tickets: list[BetTicket]
    main_horse_label: str   # "◎ 馬番X 馬名" 等の表示用


def generate_betting_recommendations(
    main_pick_id: str | None,
    sub_pick_id: str | None,
    wide_candidates: list[WideCandidate],
    horses: list[HorseMarkData],
) -> BettingPlan:
    """
    本命(または準本命)とワイド候補 から全券種の買い目を組み立てる。

    main_pick_id が None なら sub_pick_id を軸にする(spec の準本命扱い)。
    両方 None / ワイド候補 0頭 などの病的ケースでは空の BettingPlan。
    """
    # 軸馬を確定
    axis_id = main_pick_id or sub_pick_id
    if axis_id is None:
        return BettingPlan(tickets=[], main_horse_label="(軸馬決定不能)")

    horse_by_id = {h.horse_id: h for h in horses}
    axis = horse_by_id.get(axis_id)
    if axis is None:
        return BettingPlan(tickets=[], main_horse_label="(軸馬データなし)")

    main_label_prefix = "◎" if main_pick_id else "準◎"
    main_horse_label = (
        f"{main_label_prefix} 馬番{axis.horse_number} {axis.horse_name}"
    )

    tickets: list[BetTicket] = []

    # 単勝・複勝 はいつでも1点ずつ
    tickets.append(BetTicket(
        bet_type="単勝",
        horse_numbers=[axis.horse_number],
        horse_names=[axis.horse_name],
    ))
    tickets.append(BetTicket(
        bet_type="複勝",
        horse_numbers=[axis.horse_number],
        horse_names=[axis.horse_name],
    ))

    # ワイド候補から最大3頭
    wides = wide_candidates[:3]

    if wides:
        # 馬連: 軸 — ワイド候補トップ
        top = wides[0]
        tickets.append(BetTicket(
            bet_type="馬連",
            horse_numbers=[axis.horse_number, top.horse_number],
            horse_names=[axis.horse_name, top.horse_name],
            note="軸 — ワイド候補トップ 1点",
        ))

    if len(wides) >= 2:
        # 三連複: 軸 — ワイド候補上位2頭(BOX 1点)
        top1, top2 = wides[0], wides[1]
        tickets.append(BetTicket(
            bet_type="三連複",
            horse_numbers=sorted([axis.horse_number, top1.horse_number, top2.horse_number]),
            horse_names=[axis.horse_name, top1.horse_name, top2.horse_name],
            note="軸 — ワイド候補上位2頭の BOX 1点",
        ))

    if wides:
        # ワイド: 軸 — 各ワイド候補(個別、最大3点)
        for w in wides:
            tickets.append(BetTicket(
                bet_type="ワイド",
                horse_numbers=sorted([axis.horse_number, w.horse_number]),
                horse_names=[axis.horse_name, w.horse_name],
                note=f"軸 — WC{wides.index(w)+1}",
            ))

    return BettingPlan(tickets=tickets, main_horse_label=main_horse_label)
