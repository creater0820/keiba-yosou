"""
本ロジック v1.0 のロジック仕様を可視化するページ。

構成(上から):
  1) ロジック全体の 5 ステップ概要
  2) レース選択(セッション内 or テストデータの morning_race_card)
  3) 2 カラム本体:
        左 = ルール定義(CLAUDE.md / utils/logic_spec.py の SSoT)
        右 = 実データ適用例(選択中レース or 全レース横断)
  4) ◎ が出にくい問題の可視化(○マーク分布 + 本命確定率)

実装の単一情報源:
- ルール定義: utils/logic_spec.py
- 適用例の集計: utils/logic_examples.py
- 予想結果: prediction_logic.predict_all_races_v1
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from data_loader import HistoricalData, load_historical_data, load_race_card
from prediction_logic import RacePrediction, predict_all_races_v1
from utils.judgment_engine import HONMEI_MARK_THRESHOLD
from utils.logic_examples import (
    MarksDistribution,
    RuleApplication,
    collect_applications_for_race,
    compute_marks_distribution,
    index_by_rule,
)
from utils.logic_spec import (
    LOGIC_CATEGORIES,
    RUNNING_STYLE_SPEC,
    LogicCategory,
    LogicRule,
)


# =====================================================================
# ページ設定
# =====================================================================
st.set_page_config(
    page_title="ロジック説明 — 本ロジック v1.0",
    page_icon="🧭",
    layout="wide",
)


# =====================================================================
# データソース選択(セッション内 → テストデータ自動ロード)
# =====================================================================

TEST_RACE_CARD_PATH = Path("data/test/morning_race_card_20260503.csv")


@st.cache_data(show_spinner="テスト用出馬表を読み込み中…")
def _load_test_race_card() -> pd.DataFrame:
    """data/test/morning_race_card_20260503.csv を読み込む。"""
    raw = TEST_RACE_CARD_PATH.read_bytes()
    return load_race_card(io.BytesIO(raw))


@st.cache_data(show_spinner="過去データを読み込み中…")
def _load_historical_for_page() -> HistoricalData:
    return load_historical_data()


@st.cache_data(show_spinner="テストデータで予想計算中…")
def _predict_test_data() -> dict[str, RacePrediction]:
    """テストデータの morning race card を v1.0 ロジックで予想する。"""
    rc = _load_test_race_card()
    hist = _load_historical_for_page()
    return predict_all_races_v1(rc, hist)


def _get_predictions() -> tuple[dict[str, RacePrediction], str]:
    """予想結果と「データ出所」のラベルを返す。"""
    in_session = st.session_state.get("all_predictions")
    if in_session:
        return in_session, "セッション中の予想結果(app.py でアップロードしたもの)"

    if TEST_RACE_CARD_PATH.exists():
        try:
            preds = _predict_test_data()
            return preds, f"テストデータ自動読み込み({TEST_RACE_CARD_PATH.name})"
        except Exception as e:
            st.error(f"テストデータの読み込みに失敗: {e}")
            return {}, ""

    return {}, ""


# =====================================================================
# 描画ヘルパ
# =====================================================================

def _render_overview() -> None:
    """ロジック全体の 5 ステップ概要を描く。"""
    st.markdown(
        """
        本ロジック v1.0 は **5 ステップのエキスパートシステム** で構成されます。
        スコア値の単純なランキングではなく、複数のルールを多層的に適用して
        最終的な ◎本命 / ワイド候補 / 危険人気馬 / 推奨買い目 を生成します。

        ```
        Step 1  ○マーク収集     ← 過去走 (R9〜R22 + R24)
           │
        Step 2  ◎本命判定        ← ○ ≥ 5 + タイブレーク
           │
        Step 3  減点・除外        ← R6 (1番人気逃げ) / R7 (阪神1600外枠)
           │
        Step 4  ワイド候補抽出   ← R3 / R4 / R5 / R8(最大3頭)
           │
        Step 5  買い目戦略・補正 ← R2 (枠偶奇) / R23 (ダート不良補正)
        ```
        """
    )


def _render_rule_card(rule: LogicRule) -> None:
    """ルール 1 件を expander で描く(左カラム用)。"""
    with st.expander(f"**{rule.rule_id}** — {rule.title}", expanded=False):
        st.markdown(rule.description)
        if rule.notes:
            st.caption(f"📌 補足: {rule.notes}")
        if rule.code_refs:
            st.caption("**実装(トレーサビリティ)**:")
            for ref in rule.code_refs:
                st.code(ref, language="text")


def _render_left_column(categories: tuple[LogicCategory, ...]) -> None:
    """左カラム: 全ルール定義(カテゴリ順)を描く。"""
    st.markdown("### 📚 ルール定義(CLAUDE.md より)")
    st.caption("SSoT: `utils/logic_spec.py` / 実装: `utils/{onmark_rules,judgment_engine,betting_strategy}.py`")
    for cat in categories:
        st.markdown(f"#### {cat.title}")
        st.caption(cat.summary)
        for rule in cat.rules:
            _render_rule_card(rule)
        st.markdown("")  # spacing

    # 補足: 脚質判定基準
    st.markdown("#### 🐎 補足: 脚質判定")
    _render_rule_card(RUNNING_STYLE_SPEC)


def _format_application_line(app: RuleApplication, *, show_race: bool = True) -> str:
    """RuleApplication 1件を 1 行マークダウンに整形。"""
    head = f"馬番{app.horse_number} **{app.horse_name}**"
    if show_race:
        head += f"  〔{app.racecourse}{app.race_number}R {app.race_name}〕"
    return f"- {head}\n    - {app.detail}"


def _render_examples_for_race(
    selected_race: str | None,
    predictions: dict[str, RacePrediction],
    rule_index_all_races: dict[str, list[RuleApplication]],
    categories: tuple[LogicCategory, ...],
) -> None:
    """右カラム: 選択中レース or 全レース横断で各ルールの該当馬を表示。"""
    st.markdown("### 🎯 実データへの適用例")
    if not predictions:
        st.warning("予想結果が空です。app.py で出馬表をアップロードして「予想実行」してください。")
        return

    if selected_race and selected_race in predictions:
        # 選択レースに限定して描く
        pred = predictions[selected_race]
        rule_index = {}
        for app in collect_applications_for_race(pred):
            rule_index.setdefault(app.rule_id, []).append(app)
        scope_label = (
            f"{pred.race_meta.get('racecourse','')} "
            f"{pred.race_meta.get('race_number','')}R "
            f"{pred.race_meta.get('race_name','')}"
        )
        st.caption(f"対象: **{scope_label}**(レース限定表示)")
        show_race = False  # レース固定なので馬名行に場・R を出さない
    else:
        # 全レース横断
        rule_index = rule_index_all_races
        st.caption(f"対象: **全 {len(predictions)} レース横断**")
        show_race = True

    # カテゴリ順に「該当ルール → 馬」を出す
    for cat in categories:
        any_in_cat = any(rule.rule_id in rule_index for rule in cat.rules)
        st.markdown(f"#### {cat.title}")
        if not any_in_cat:
            st.caption("(このカテゴリで該当馬なし)")
            continue
        for rule in cat.rules:
            apps = rule_index.get(rule.rule_id, [])
            if not apps:
                with st.expander(f"**{rule.rule_id}** — 該当 0 頭", expanded=False):
                    st.caption("このスコープでは該当馬なし")
                continue
            with st.expander(
                f"**{rule.rule_id}** — 該当 {len(apps)} 頭", expanded=False
            ):
                for app in apps:
                    st.markdown(_format_application_line(app, show_race=show_race))


def _render_marks_distribution_section(dist: MarksDistribution) -> None:
    """◎が出にくい問題の可視化:○マーク分布 + 本命確定率。"""
    st.markdown("---")
    st.markdown("### 📈 ◎ 確定率の可視化(○マーク閾値の妥当性チェック)")

    col_metric1, col_metric2, col_metric3 = st.columns(3)
    main_rate = (
        dist.races_with_main_pick / dist.races_total * 100
        if dist.races_total else 0
    )
    sub_only_rate = (
        dist.races_with_sub_pick_only / dist.races_total * 100
        if dist.races_total else 0
    )
    col_metric1.metric(
        "◎本命確定レース",
        f"{dist.races_with_main_pick} / {dist.races_total}",
        f"{main_rate:.1f}%",
    )
    col_metric2.metric(
        "準◎ fallback のみ",
        f"{dist.races_with_sub_pick_only} / {dist.races_total}",
        f"{sub_only_rate:.1f}%",
    )
    col_metric3.metric(
        "現在の本命閾値",
        f"○ ≥ {HONMEI_MARK_THRESHOLD}",
        help="utils/judgment_engine.py:HONMEI_MARK_THRESHOLD で可変",
    )

    # ヒストグラム(○マーク数 → 頭数)
    hist_rows = []
    max_mark = max(dist.histogram.keys() or [0])
    for marks in range(0, max_mark + 1):
        count = dist.histogram.get(marks, 0)
        hist_rows.append({"○マーク数": marks, "馬の数": count})
    hist_df = pd.DataFrame(hist_rows)
    st.markdown("#### 出走馬全頭の ○マーク分布")
    st.bar_chart(hist_df.set_index("○マーク数"), height=200)

    # 本命候補(○≥5)の該当馬を全レース横断で出す
    st.markdown("#### 各レースの最大○マーク数")
    st.caption("○ < 閾値 のレースは準◎ fallback で運用される。閾値引き下げの判断材料。")
    rows_max = [
        {"race_id": rid, "最大○": m}
        for rid, m in sorted(dist.max_marks_per_race.items(), key=lambda x: -x[1])
    ]
    if rows_max:
        st.dataframe(
            pd.DataFrame(rows_max).head(40),
            hide_index=True,
            use_container_width=True,
            height=300,
        )


# =====================================================================
# メインレンダリング
# =====================================================================

st.title("🧭 本ロジック v1.0 ロジック説明")
st.caption(
    "CLAUDE.md「推奨馬選定ロジック(本ロジック v1.0)」の全ルールを、"
    "実データ(出馬表 + 過去走 Parquet)への適用結果と並べて表示します。"
)

_render_overview()

predictions, source_label = _get_predictions()

if not predictions:
    st.info(
        "予想結果が利用できません。\n"
        "- app.py で出馬表をアップロード → 予想実行 すると、ここでもセッション内の結果が見られます\n"
        "- それまでは `data/test/morning_race_card_20260503.csv` のテストデータが自動表示されます"
    )
    st.stop()

st.success(f"📦 データ出所: {source_label}")

# ----- レース選択 -----
race_options = ["(全レース横断)"]
race_label_to_id: dict[str, str] = {}
for rid, p in predictions.items():
    m = p.race_meta
    label = (
        f"{m.get('racecourse','')} {m.get('race_number','')}R "
        f"{m.get('race_name','')}"
        f"  〔{m.get('distance','')}m {m.get('surface','')} / {m.get('going','')}〕"
    )
    race_options.append(label)
    race_label_to_id[label] = rid

# 京都11R(天皇賞春)があればデフォルトに(検証で使った既知レース)
default_idx = 0
for i, label in enumerate(race_options):
    if "京都" in label and "11R" in label:
        default_idx = i
        break

selected_label = st.selectbox(
    "対象レースを選ぶ(右カラムの適用例の絞り込みに使う)",
    race_options,
    index=default_idx,
)
selected_race_id = race_label_to_id.get(selected_label)  # 全レース横断なら None

# ----- 全レース分の rule_id インデックス(常に必要) -----
rule_index_all = index_by_rule(predictions)

# ----- 2 カラム本体 -----
col_left, col_right = st.columns([1, 1])
with col_left:
    _render_left_column(LOGIC_CATEGORIES)

with col_right:
    _render_examples_for_race(
        selected_race=selected_race_id,
        predictions=predictions,
        rule_index_all_races=rule_index_all,
        categories=LOGIC_CATEGORIES,
    )

# ----- ◎が出にくい問題の可視化 -----
dist = compute_marks_distribution(predictions)
_render_marks_distribution_section(dist)
