"""
過去レース DataFrame から、特定レース基準で各馬の直近 N 走を抽出するヘルパ。

「直近5走戦歴マトリクス」を描画するために、出走馬まとめて履歴をキャッシュ抽出する。
historical_df は約16万行と大きいので、レース毎に何度も filter するのではなく
1回 filter したものをグループ化して使い回す。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


# v1.4: ルール評価対象の過去走数を 5 → 10 に拡張(ベテラン馬の長期実績を拾う)。
# 脚質判定(determine_running_style)は別途 head5 で抑制(直近の傾向重視)。
DEFAULT_RECENT_N = 10


@st.cache_data(show_spinner=False)
def get_recent_runs_for_race(
    horse_ids: tuple[str, ...],
    target_date_iso: str,
    _historical_df: pd.DataFrame,
    n: int = DEFAULT_RECENT_N,
) -> dict[str, list[dict | None]]:
    """
    指定したレース日(target_date_iso)以前のレースで、各馬の直近 n 走を返す。

    引数:
        horse_ids: 馬IDのタプル。 Streamlit の cache_data はタプルをハッシュキーに使うため
                   list ではなく tuple で渡してもらう前提。
        target_date_iso: 基準レース日("YYYY-MM-DD" 文字列)。これより**前**の走のみ拾う。
        _historical_df: 過去レース DataFrame。先頭 _ で Streamlit のハッシュ対象から除外
                        (DataFrame はハッシュが重く、かつセッション内で同一インスタンス
                        を使い続ける前提なので除外しても安全)。
        n: 何走分まで取るか(既定 5)。

    戻り値:
        { horse_id: [run0, run1, ..., run{n-1}] }
        run0 が **直近(=前走)**、run{n-1} が **n 走前**(古い方)。
        該当走数が n に満たない馬は末尾を None でパディングして必ず長さ n にする。
        run の中身は historical_df の 1 行を to_dict("records") した dict。
    """
    # 馬がそもそも過去データに無い場合の早期リターン
    if not horse_ids:
        return {}

    relevant = _historical_df[_historical_df["horse_id"].isin(horse_ids)].copy()
    if relevant.empty:
        return {hid: [None] * n for hid in horse_ids}

    # 日付を正規化して target_date より前のみに絞る
    relevant["_race_date"] = pd.to_datetime(relevant["race_date"], errors="coerce")
    target = pd.Timestamp(target_date_iso)
    relevant = relevant[relevant["_race_date"] < target]
    relevant = relevant.sort_values("_race_date", ascending=False)

    # 馬ID 単位でグループ化してから先頭 n 件を取る方が、
    # 馬ごとに DataFrame を filter するより圧倒的に速い
    grouped = dict(list(relevant.groupby("horse_id")))

    result: dict[str, list[dict | None]] = {}
    for hid in horse_ids:
        if hid in grouped:
            past_n = grouped[hid].head(n).drop(columns=["_race_date"])
            runs: list[dict | None] = past_n.to_dict("records")
        else:
            runs = []
        # 不足分は None で末尾パディング
        while len(runs) < n:
            runs.append(None)
        result[hid] = runs
    return result


def get_recent_n_runs(
    horse_id: str,
    target_race_date,
    historical_df: pd.DataFrame,
    n: int = DEFAULT_RECENT_N,
) -> list[dict | None]:
    """
    単一馬の直近 N 走を返す(デバッグ・スポットチェック用ラッパ)。

    内部では get_recent_runs_for_race(キャッシュ済み) を呼ぶだけ。
    target_race_date は str / pd.Timestamp / datetime のいずれでも可。

    戻り値: [前走, 2走前, ..., N走前] 直近順、不足は None で末尾パディング。
    """
    target_iso = pd.Timestamp(target_race_date).strftime("%Y-%m-%d")
    return get_recent_runs_for_race(
        (str(horse_id),), target_iso, historical_df, n=n,
    )[str(horse_id)]


# =====================================================================
# 脚質判定(本ロジック v1.0 の Step 1 / Phase 2 / v1.9.1 で多段化)
# =====================================================================
# CLAUDE.md「推奨馬選定ロジック / 脚質の判定基準」より基本判定:
#   平均 1〜3番手   → 逃げ
#   平均 4〜6番手   → 先行
#   平均 7〜10番手  → 差し
#   平均 11番手以下 → 追込
#
# v1.9.1 追加: corner_1 は短距離戦で記録なしのケースが多い(historical 全体で
# 55.98% 欠損、特に 1600m 以下はほぼ全欠損)。corner_3 / corner_4 はほぼ全走で
# 記録されているため、corner_1 不足時に corner_3 → corner_4 へ順次フォール
# バックすることで判定不能を解消する。
#
# Tier 1a (high)   : corner_1 有効 ≥ 3 走 → corner_1 平均(既存ロジック完全不変)
# Tier 1b (high)   : corner_1 不足 + corner_3 有効 ≥ 3 走 → corner_3 平均
# Tier 1c (medium) : corner_1/3 不足 + corner_4 有効 ≥ 3 走 → corner_4 平均
# Tier 2  (medium) : 過去走 1-2 走しかなく上記すべて 3 未満 → 利用可能 corner
#                    平均で暫定判定(信頼度 medium)
# Tier 4  (default): 過去走 0 走 + distance あり → 短距離=先行 / 中長=差し
# Tier 5  (default): distance も不明 → 差し(中庸で誤判定時の影響最小)
#
# Tier 3(父系統テーブル)は本番 data/historical/races.parquet に sire 列が
# 存在しないため Phase 2 に延期(現状不可)。

RUNNING_STYLES = ("逃げ", "先行", "差し", "追込", "不明(先行扱い)")

CONFIDENCE_LEVELS = ("high", "medium", "low", "default")

# v1.9.1 で多段化した時の schema version。app.py の cache_hash に組み込んで、
# 旧バージョンで生成された @st.cache_data エントリを自動で無効化する。
# 今後 Tier 設計・閾値・default 値を変える時はこの文字列を bump する。
STYLE_SCHEMA_VERSION = "v1.9.1-multi-tier"


def _classify_by_avg(avg: float) -> str:
    """corner 順位平均から脚質を分類(全 corner で共通閾値)。"""
    if avg <= 3:
        return "逃げ"
    if avg <= 6:
        return "先行"
    if avg <= 10:
        return "差し"
    return "追込"


def _collect_valid_corners(
    head5: list[dict | None], corner_key: str,
) -> list[float]:
    """直近 5 走から corner_key の有効値(非 NaN)を抽出。"""
    out: list[float] = []
    for r in head5:
        if r is None:
            continue
        v = r.get(corner_key)
        if v is None:
            continue
        try:
            if pd.isna(v):
                continue
        except (TypeError, ValueError):
            pass  # int 等 NaN ではないので continue しない
        try:
            out.append(float(v))
        except (ValueError, TypeError):
            continue
    return out


def _default_style_by_distance(distance: int | None) -> str:
    """距離別デフォルト脚質。短距離は前残り傾向で「先行」、それ以外は「差し」。"""
    if distance and distance <= 1400:
        return "先行"
    return "差し"


def determine_running_style_with_confidence(
    past_runs: list[dict | None],
    distance: int | None = None,
) -> tuple[str, str]:
    """
    過去 N 走 + 当日距離から (脚質, confidence) を返す多段判定(v1.9.1)。

    引数:
        past_runs: [前走, 2走前, ..., N走前] のリスト。各要素は dict or None。
                   dict は corner_1〜corner_4 を含む historical の 1 行想定。
        distance: 当日レース距離(m)。Tier 4 のデフォルト判定で使う。

    戻り値:
        (脚質, confidence)。
        脚質   = "逃げ" / "先行" / "差し" / "追込" の 4 区分(不明は返さない)。
        confidence = "high" / "medium" / "default" のいずれか。
    """
    head5 = (past_runs or [])[:5]
    n_runs = sum(1 for r in head5 if r is not None)

    # 過去走ゼロ → Tier 4 / 5 へ直行
    if n_runs == 0:
        return _default_style_by_distance(distance), "default"

    c1 = _collect_valid_corners(head5, "corner_1")
    c3 = _collect_valid_corners(head5, "corner_3")
    c4 = _collect_valid_corners(head5, "corner_4")

    # Tier 1a: corner_1 ≥ 3 走(既存ロジックと完全一致)
    if len(c1) >= 3:
        return _classify_by_avg(sum(c1) / len(c1)), "high"

    # Tier 1b: corner_3 ≥ 3 走(短距離馬の主救済経路)
    if len(c3) >= 3:
        return _classify_by_avg(sum(c3) / len(c3)), "high"

    # Tier 1c: corner_4 ≥ 3 走(ゴール前なので順位収束気味、信頼度一段下げ)
    if len(c4) >= 3:
        return _classify_by_avg(sum(c4) / len(c4)), "medium"

    # Tier 2: 1-2 走のサンプル不足。利用可能な corner を順に拾う
    for corners in (c1, c3, c4):
        if corners:
            return _classify_by_avg(sum(corners) / len(corners)), "medium"

    # Tier 4 / 5: 過去走はあるが corner データが全く取れない → 距離別デフォルト
    return _default_style_by_distance(distance), "default"


def determine_running_style(past_runs: list[dict | None]) -> str:
    """
    後方互換シェル(v1.9.0 までの呼び出し側を変更せず維持)。

    戻り値は脚質文字列のみ。v1.9.1 で内部実装が多段化されたため、
    過去走が無い・corner_1 が取れない場合でも「不明(先行扱い)」は
    返さず、Tier 1b/1c/2 でフォールバック判定される。
    Tier 4-5 まで降りるのは distance 不明 + 過去走 0 走のみ(その場合
    は安全策で「差し」)。

    distance を渡したい新コードは determine_running_style_with_confidence
    を直接呼ぶ。
    """
    style, _ = determine_running_style_with_confidence(past_runs)
    return style
