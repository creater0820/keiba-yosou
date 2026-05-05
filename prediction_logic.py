"""
予想ロジックモジュール。

設計方針:
- このモジュールは「データの読み込み(data_loader.py)」と「画面描画(app.py)」のどちらにも依存しない。
- スコアリングは複数の小さな関数(rule)に分離し、将来お父様の本ロジックに差し替え可能にする。
- 各 rule は (race_card_row, historical_data) を受け取り (score, reason_text) を返す純粋関数。
- predict_race() は出馬表1レース分を受け取り、各馬のスコアと印(◎○▲△)を返す。

MVP段階の暫定ロジック(CLAUDE.md):
- 直近3走の平均着順 × -10点
- 直近3走の上がり3F平均が33.5秒未満なら +20点
- 騎手の年間勝率 × 100点
- 距離適性(同距離での連対率) × 50点
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
import streamlit as st

from data_loader import HistoricalData


# ===== 戻り値の型 =====

@dataclass
class HorsePrediction:
    """1頭分の予想結果。画面に直接表示できるよう日本語フィールドを持つ。"""
    horse_id: str
    horse_name: str
    jockey: str
    score: float                 # 合計スコア(高いほど推奨)
    mark: str                    # ◎ ○ ▲ △ または "" (印なし)
    reasons: list[str] = field(default_factory=list)  # 理由(画面で展開表示)


# 印は上位4頭にこの順番で付与
RANK_MARKS = ["◎", "○", "▲", "△"]


# ===== スコアリングルール群(差し替え可能な部品) =====
#
# 各ルールは下記のシグネチャ:
#     def rule(horse_row: pd.Series, hist: HistoricalData) -> tuple[float, str | None]
#
# - 第1引数: 出馬表の1行(その馬のレースエントリー)
# - 第2引数: 過去データ一式
# - 戻り値: (このルールのスコア, 理由文字列または None)
#   理由が None の場合は表示しない(該当条件が成立しなかったケース)


def _past_3_runs(horse_id: str, hist: HistoricalData) -> pd.DataFrame:
    """指定した馬の直近3走分のレース履歴を返す。日付降順。"""
    past = hist.races[hist.races["horse_id"] == horse_id].copy()
    if past.empty:
        return past
    # race_date は文字列のことも日付のこともあるため、両対応
    past["race_date"] = pd.to_datetime(past["race_date"], errors="coerce")
    return past.sort_values("race_date", ascending=False).head(3)


def rule_recent_finish(horse_row: pd.Series, hist: HistoricalData) -> tuple[float, str | None]:
    """直近3走の平均着順 × -10点 (着順が良いほど高スコア)"""
    past3 = _past_3_runs(horse_row["horse_id"], hist)
    if past3.empty:
        return 0.0, None
    avg = past3["finishing_position"].mean()
    score = -10.0 * avg
    return score, f"直近3走の平均着順 {avg:.1f} → {score:+.1f}点"


def rule_recent_last3f(horse_row: pd.Series, hist: HistoricalData) -> tuple[float, str | None]:
    """直近3走の上がり3F平均が 33.5秒未満なら +20点(末脚評価)"""
    past3 = _past_3_runs(horse_row["horse_id"], hist)
    if past3.empty or "last_3f" not in past3.columns:
        return 0.0, None
    avg_3f = past3["last_3f"].mean()
    if pd.isna(avg_3f):
        return 0.0, None
    if avg_3f < 33.5:
        return 20.0, f"上がり3F平均 {avg_3f:.1f}秒(33.5秒未満)→ +20点"
    return 0.0, f"上がり3F平均 {avg_3f:.1f}秒(33.5秒未満ではない)"


def rule_jockey_win_rate(horse_row: pd.Series, hist: HistoricalData) -> tuple[float, str | None]:
    """騎手の(過去データ全体での)勝率 × 100点"""
    jockey = horse_row.get("jockey")
    if not jockey:
        return 0.0, None
    jockey_rides = hist.races[hist.races["jockey"] == jockey]
    if jockey_rides.empty:
        return 0.0, f"騎手 {jockey}: 過去データなし"
    wins = (jockey_rides["finishing_position"] == 1).sum()
    win_rate = wins / len(jockey_rides)
    score = 100.0 * win_rate
    return score, f"騎手 {jockey} の勝率 {win_rate:.1%}({wins}/{len(jockey_rides)})→ {score:+.1f}点"


def rule_distance_aptitude(horse_row: pd.Series, hist: HistoricalData) -> tuple[float, str | None]:
    """距離適性: 同じ距離での連対率(1-2着率) × 50点"""
    horse_id = horse_row["horse_id"]
    distance = horse_row.get("distance")
    if distance is None:
        return 0.0, None
    same_dist = hist.races[
        (hist.races["horse_id"] == horse_id) & (hist.races["distance"] == distance)
    ]
    if same_dist.empty:
        return 0.0, f"距離{distance}m での実績なし"
    rentai = (same_dist["finishing_position"] <= 2).sum()
    rate = rentai / len(same_dist)
    score = 50.0 * rate
    return score, f"距離{distance}m 連対率 {rate:.1%}({rentai}/{len(same_dist)})→ {score:+.1f}点"


# 適用するルールのリスト。お父様の本ロジック実装時はここを差し替えるだけで良い。
# (1) ルールごとに関数を入れ替え、(2) 順序や有効化フラグもここで制御。
ScoringRule = Callable[[pd.Series, HistoricalData], tuple[float, "str | None"]]

DEFAULT_RULES: list[ScoringRule] = [
    rule_recent_finish,
    rule_recent_last3f,
    rule_jockey_win_rate,
    rule_distance_aptitude,
]


# ===== レース1本分の予想 =====

def predict_race(
    race_df: pd.DataFrame,
    historical: HistoricalData,
    rules: list[ScoringRule] | None = None,
    top_n: int = 4,
) -> list[HorsePrediction]:
    """
    1レース分の出馬表に対して予想を行う。

    引数:
        race_df: 1レース分の出馬表 DataFrame(行=出走馬1頭)
        historical: 過去データ
        rules: 適用するスコアリングルール(未指定なら DEFAULT_RULES)
        top_n: 印を付ける頭数(デフォルト 4 → ◎○▲△)

    戻り値:
        スコア降順に並んだ HorsePrediction のリスト(全頭分。上位 top_n 頭に印付き)
    """
    if rules is None:
        rules = DEFAULT_RULES

    predictions: list[HorsePrediction] = []
    for _, row in race_df.iterrows():
        # 1頭ずつ各ルールを適用してスコアを合計。例外が出た馬はスキップせずスコア0扱いで継続。
        total_score = 0.0
        reasons: list[str] = []
        for rule in rules:
            try:
                score, reason = rule(row, historical)
            except Exception as e:
                # 個別ルール内例外は致命的ではない: その馬・そのルールだけ無視して継続
                score, reason = 0.0, f"[{rule.__name__}] 計算スキップ: {e}"
            total_score += score
            if reason:
                reasons.append(reason)

        # 過去データに完全にいない馬は明示的に注釈
        if historical.races[historical.races["horse_id"] == row["horse_id"]].empty:
            reasons.insert(0, "※過去データなし(参考スコアのみ)")

        predictions.append(HorsePrediction(
            horse_id=str(row["horse_id"]),
            horse_name=str(row["horse_name"]),
            jockey=str(row.get("jockey", "")),
            score=round(total_score, 2),
            mark="",  # 後でランク付け
            reasons=reasons,
        ))

    # スコア降順に並べ替えて上位 top_n に印を付ける
    predictions.sort(key=lambda p: p.score, reverse=True)
    for i, pred in enumerate(predictions[:top_n]):
        if i < len(RANK_MARKS):
            pred.mark = RANK_MARKS[i]

    return predictions


def predict_all_races(
    race_card_df: pd.DataFrame,
    historical: HistoricalData,
    rules: list[ScoringRule] | None = None,
) -> dict[str, list[HorsePrediction]]:
    """
    出馬表全体を race_id 単位でグループ化し、各レースの予想結果をまとめて返す。

    戻り値: { race_id: [HorsePrediction, ...] }
    """
    results: dict[str, list[HorsePrediction]] = {}
    for race_id, group in race_card_df.groupby("race_id", sort=False):
        results[str(race_id)] = predict_race(group, historical, rules=rules)
    return results


@st.cache_data(show_spinner="予想計算中…")
def predict_all_races_cached(
    race_card_hash: str,
    _race_card_df: pd.DataFrame,
    _historical: HistoricalData,
) -> dict[str, list[HorsePrediction]]:
    """
    predict_all_races のキャッシュ版。

    キャッシュキー:
        race_card_hash (例: アップロードバイト列の MD5) のみ。
        _race_card_df / _historical は接頭辞 _ で Streamlit のハッシュ対象から除外。
        - DataFrame は内容ハッシュが重い(行数が多いと数秒)
        - HistoricalData は dataclass で既定では hashable でない
        race_card_hash が同一なら DataFrame の内容も同一という前提なので、
        ハッシュキーから DataFrame を外しても安全。

    使い方(app.py 側):
        file_hash = hashlib.md5(uploaded_bytes).hexdigest()
        predictions = predict_all_races_cached(file_hash, race_card_df, historical)
    """
    return predict_all_races(_race_card_df, _historical)
