"""期待値(EV)計算エンジン(v1.13.0 Phase 2)。

確率(utils/probability_engine.py の HorseProbability)とオッズから期待値 EV を
計算する純粋関数群。rating_engine / probability_engine には **一切手を入れない**
完全分離設計。

用語(使う前の 1 行解説):
- 公正オッズ(fair odds): その馬の予測確率なら「最低これだけのオッズが付けば
  損得ゼロ」という分岐点。単勝なら 1 / win_prob。
- EV(期待値 / Expected Value): 1 円賭けたときの期待損益率。
  単勝 EV = win_prob × オッズ − 1。EV > 0 なら理論上プラス。
- Kelly 基準: 期待値が正のとき資金の何割を賭けるのが最適かの比率(Phase 4 用)。

設計方針:
- DC 形式の当日 CSV は単勝オッズを含まない(v1.7.4 で確定)。よって
  (1) 公正オッズはオッズ不要で常時計算、(2) マーケットオッズは UI 手動入力 or
  バックテストの履歴 odds を渡して EV を計算する 2 系統。
- recommendation(お買い得フラグ)の閾値は引数で差し替え可能
  (サイドバースライダー / バックテスト感度分析で変える)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

EV_SCHEMA_VERSION = "v1-ev-phase2"

# お買い得判定の閾値(既定。サイドバー / バックテストで上書き可能)
EV_THRESHOLD_HIGH = 0.20      # 🎯 高期待値(+20%)
EV_THRESHOLD_MID = 0.10       # ⭐ お買い得(+10%)
EV_THRESHOLD_LOW = 0.00       # 💡 プラス期待値(>0)

# 最低オッズ閾値(極端な低オッズ馬は推奨フラグから除外)
MIN_ODDS_FOR_RECOMMENDATION = 2.0

REC_HIGH = "🎯 高期待値"
REC_MID = "⭐ お買い得"
REC_LOW = "💡 プラス期待値"
REC_NONE = ""


@dataclass(frozen=True)
class HorseEV:
    """馬 1 頭分の EV / 公正オッズ計算結果。"""
    horse_id: str
    horse_number: int           # 馬番(probability_engine と統一、spec の umaban)
    win_prob: float             # 単勝確率 [0-1]
    place_prob: float           # 複勝確率 [0-1]
    fair_odds_tan: Optional[float]   # 公正単勝オッズ = 1 / win_prob(0 なら None)
    fair_odds_fuku: Optional[float]  # 公正複勝オッズ近似 = 1 / place_prob
    market_odds_tan: Optional[float] # マーケット単勝オッズ(手動入力 or 履歴)
    market_odds_fuku: Optional[float]
    ev_tan: Optional[float]          # 単勝 EV(マーケットオッズ未入力なら None)
    ev_fuku: Optional[float]         # 複勝 EV
    recommendation: str         # REC_HIGH / REC_MID / REC_LOW / REC_NONE


def _fair_odds(prob: float) -> Optional[float]:
    """公正オッズ = 1/prob。prob ≤ 0(除外馬等)は None。"""
    if prob is None or prob <= 0:
        return None
    return 1.0 / prob


def _classify(
    ev_tan: Optional[float],
    market_odds_tan: Optional[float],
    *,
    threshold_high: float,
    threshold_mid: float,
    threshold_low: float,
    min_odds: float,
) -> str:
    """単勝 EV と最低オッズ条件から お買い得フラグを決める。"""
    if ev_tan is None or market_odds_tan is None:
        return REC_NONE
    if market_odds_tan < min_odds:
        return REC_NONE
    if ev_tan >= threshold_high:
        return REC_HIGH
    if ev_tan >= threshold_mid:
        return REC_MID
    if ev_tan >= threshold_low:
        return REC_LOW
    return REC_NONE


def compute_horse_ev(
    win_prob: float,
    place_prob: float,
    market_odds_tan: Optional[float] = None,
    market_odds_fuku: Optional[float] = None,
    *,
    horse_id: str = "",
    horse_number: int = 0,
    threshold_high: float = EV_THRESHOLD_HIGH,
    threshold_mid: float = EV_THRESHOLD_MID,
    threshold_low: float = EV_THRESHOLD_LOW,
    min_odds: float = MIN_ODDS_FOR_RECOMMENDATION,
) -> HorseEV:
    """単馬の公正オッズと EV を計算する。

    market_odds_* が None / ≤0 なら EV は None(公正オッズのみ算出)。
    win_prob ≤ 0(B1/B2 除外馬など)は公正オッズ None・EV None・フラグなし。
    """
    fair_tan = _fair_odds(win_prob)
    fair_fuku = _fair_odds(place_prob)

    ev_tan: Optional[float] = None
    ev_fuku: Optional[float] = None
    if market_odds_tan is not None and market_odds_tan > 0 and win_prob > 0:
        ev_tan = win_prob * market_odds_tan - 1.0
    if market_odds_fuku is not None and market_odds_fuku > 0 and place_prob > 0:
        ev_fuku = place_prob * market_odds_fuku - 1.0

    recommendation = _classify(
        ev_tan, market_odds_tan,
        threshold_high=threshold_high, threshold_mid=threshold_mid,
        threshold_low=threshold_low, min_odds=min_odds,
    )

    return HorseEV(
        horse_id=horse_id, horse_number=horse_number,
        win_prob=win_prob, place_prob=place_prob,
        fair_odds_tan=fair_tan, fair_odds_fuku=fair_fuku,
        market_odds_tan=market_odds_tan, market_odds_fuku=market_odds_fuku,
        ev_tan=ev_tan, ev_fuku=ev_fuku,
        recommendation=recommendation,
    )


def compute_race_evs(
    horse_probabilities: Iterable,
    market_odds_dict: Optional[dict] = None,
    *,
    threshold_high: float = EV_THRESHOLD_HIGH,
    threshold_mid: float = EV_THRESHOLD_MID,
    threshold_low: float = EV_THRESHOLD_LOW,
    min_odds: float = MIN_ODDS_FOR_RECOMMENDATION,
) -> list[HorseEV]:
    """レース内の全馬の公正オッズ + EV を計算する。

    引数:
        horse_probabilities: HorseProbability のリスト(.horse_number/.win_prob/
                             .place_prob を持つ)。
        market_odds_dict: {horse_number: {"tan": float, "fuku": float}}。
                          None なら公正オッズのみ(EV は None)。
        threshold_*/min_odds: お買い得フラグ判定の閾値(UI スライダー差し替え用)。

    戻り値: HorseEV のリスト(入力順を維持)。
    """
    odds_map = market_odds_dict or {}
    results: list[HorseEV] = []
    for p in horse_probabilities:
        odds = odds_map.get(p.horse_number, {}) or {}
        ev = compute_horse_ev(
            win_prob=p.win_prob,
            place_prob=p.place_prob,
            market_odds_tan=odds.get("tan"),
            market_odds_fuku=odds.get("fuku"),
            horse_id=p.horse_id,
            horse_number=p.horse_number,
            threshold_high=threshold_high,
            threshold_mid=threshold_mid,
            threshold_low=threshold_low,
            min_odds=min_odds,
        )
        results.append(ev)
    return results


def kelly_fraction(win_prob: float, market_odds_tan: float) -> float:
    """Kelly 基準による最適投資比率(Phase 4 用、Phase 2 では計算のみ)。

    f* = (b·p − q) / b
    b = オッズ − 1(純利益倍率)、p = win_prob、q = 1 − p。
    オッズ ≤ 1 や勝率 ≤ 0 では 0、結果は [0, 1] にクリップ。
    """
    if market_odds_tan is None or market_odds_tan <= 1.0 or win_prob <= 0:
        return 0.0
    b = market_odds_tan - 1.0
    q = 1.0 - win_prob
    f = (b * win_prob - q) / b
    return max(0.0, min(f, 1.0))
