"""コース × 枠順 × 脚質 バイアス補正ルール(Phase 1: 11 ルール)

データ出典: keiba-course.com 2023.1.1〜2025.12.31 集計
レポート: JRA競馬場特性レポート_v1.md

v1.9.0 で新カテゴリ G(Geographic/Ground)を導入。既存の C/D/E/F カテゴリと
並列で配点。G-Frame(枠順補正)と G-Style(脚質補正)の 2 サブカテゴリ。

【設計方針】
- 配点は +5〜+12 の保守的設定(v1.8.0 で +15 でも穴馬過剰浮上が起きたため)
- 減点は導入しない(鉄板馬の取りこぼし防止)
- G-Frame 同士、G-Style 同士は排他(各馬で各カテゴリ最大 1 つの加点)
- G-Frame と G-Style は独立(両方発火可能、1 馬最大で +20 程度)

【Phase 1 で除外したルール】
- G11(新潟芝 1600-2200m 外回り + 差し/追込): historical/races.parquet に
  内回り/外回り情報が一切ない(列なし、race_name にも未表記)ため、
  course_inout 判別不可能。Phase 2 で判別ロジックを追加した後に実装する。

【サーフェス表記の注意】
historical の `surface` 列は **"ダ" / "芝"**(短縮形)。タスク本文で
「ダート」と書かれていても実装上は "ダ" でマッチする。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


COURSE_BIAS_SCHEMA_VERSION = "v1-phase1-11rules"


@dataclass(frozen=True)
class CourseBiasRule:
    rule_id: str           # "G1" .. "G12" (G11 は欠番)
    category: str          # "G-Frame" or "G-Style"
    description: str       # ロジック説明ページ用テキスト
    rate: int              # 加点(Phase 1 は +5〜+12 のみ、減点なし)
    matcher: Callable      # (course, surface, distance, frame, style) -> bool


# =====================================================================
# G-Frame: 枠順補正(コース × 枠範囲)
# =====================================================================
G1 = CourseBiasRule(
    rule_id="G1", category="G-Frame", rate=8,
    description="東京ダート 1400m/1600m + 6〜8 枠(芝スタートの外枠優位、8 枠複勝率 26.4%)",
    matcher=lambda c, s, d, f, st: (
        c == "東京" and s == "ダ" and d in (1400, 1600) and f in (6, 7, 8)
    ),
)

G2 = CourseBiasRule(
    rule_id="G2", category="G-Frame", rate=8,
    description="中山ダート 1800m + 7〜8 枠(8 枠複勝率 23.5% vs 1 枠 16.7%)",
    matcher=lambda c, s, d, f, st: (
        c == "中山" and s == "ダ" and d == 1800 and f in (7, 8)
    ),
)

G3 = CourseBiasRule(
    rule_id="G3", category="G-Frame", rate=8,
    description="中山芝 1200m/2000m + 1〜2 枠(1 枠複勝率 24-28%、内枠優位)",
    matcher=lambda c, s, d, f, st: (
        c == "中山" and s == "芝" and d in (1200, 2000) and f in (1, 2)
    ),
)

G4 = CourseBiasRule(
    rule_id="G4", category="G-Frame", rate=12,
    description="新潟芝 1000m(直線)+ 7〜8 枠(8 枠複勝率 36.1% vs 1 枠 6.4%、史上最強)",
    matcher=lambda c, s, d, f, st: (
        c == "新潟" and s == "芝" and d == 1000 and f in (7, 8)
    ),
)

G5 = CourseBiasRule(
    rule_id="G5", category="G-Frame", rate=8,
    description="福島芝 1200m + 1〜2 枠(1 枠複勝率 30.0% vs 5-8 枠 13-20%)",
    matcher=lambda c, s, d, f, st: (
        c == "福島" and s == "芝" and d == 1200 and f in (1, 2)
    ),
)

G6 = CourseBiasRule(
    rule_id="G6", category="G-Frame", rate=5,
    description="小倉芝 1200m + 6〜8 枠(7 枠複勝率 23.0%、外枠優位)",
    matcher=lambda c, s, d, f, st: (
        c == "小倉" and s == "芝" and d == 1200 and f in (6, 7, 8)
    ),
)


# =====================================================================
# G-Style: 脚質補正(コース × 脚質)
# =====================================================================
G7 = CourseBiasRule(
    rule_id="G7", category="G-Style", rate=10,
    description="函館芝 1200m + 逃げ脚質(逃げ複勝率 51.1%、JRA トップクラス)",
    matcher=lambda c, s, d, f, st: (
        c == "函館" and s == "芝" and d == 1200 and st == "逃げ"
    ),
)

G8 = CourseBiasRule(
    rule_id="G8", category="G-Style", rate=8,
    description="中山ダート 1800m + 逃げ/先行(逃げ 40.7% + 先行 45.0%、前残り極端)",
    matcher=lambda c, s, d, f, st: (
        c == "中山" and s == "ダ" and d == 1800 and st in ("逃げ", "先行")
    ),
)

G9 = CourseBiasRule(
    rule_id="G9", category="G-Style", rate=8,
    description="京都ダート 1800m + 逃げ/先行(逃げ 45.2% + 先行 44.6%)",
    matcher=lambda c, s, d, f, st: (
        c == "京都" and s == "ダ" and d == 1800 and st in ("逃げ", "先行")
    ),
)

G10 = CourseBiasRule(
    rule_id="G10", category="G-Style", rate=8,
    description="阪神ダート 1800m + 逃げ/先行(逃げ 45.3% + 先行 44.8%)",
    matcher=lambda c, s, d, f, st: (
        c == "阪神" and s == "ダ" and d == 1800 and st in ("逃げ", "先行")
    ),
)

# G11(新潟芝 1600-2200m 外回り + 差し/追込)は内外判別不能のため Phase 2 に延期

G12 = CourseBiasRule(
    rule_id="G12", category="G-Style", rate=8,
    description="福島芝 1200m + 逃げ脚質(逃げ複勝率 52.7%、外で内荒れ後)",
    matcher=lambda c, s, d, f, st: (
        c == "福島" and s == "芝" and d == 1200 and st == "逃げ"
    ),
)


G_FRAME_RULES: list[CourseBiasRule] = [G1, G2, G3, G4, G5, G6]
G_STYLE_RULES: list[CourseBiasRule] = [G7, G8, G9, G10, G12]
ALL_G_RULES: list[CourseBiasRule] = G_FRAME_RULES + G_STYLE_RULES


def evaluate_course_bias(
    course: str,           # "東京", "中山", "京都", "阪神", "中京",
                           # "札幌", "函館", "福島", "新潟", "小倉"
    surface: str,          # "ダ" or "芝"(historical 短縮形)
    distance: int,         # 1000, 1200, 1400, 1600, 1800, 2000, ...
    frame: int,            # 1〜8(出走頭数から横山方式で算出)
    style: str,            # "逃げ" / "先行" / "差し" / "追込" /
                           # "不明(先行扱い)"
) -> list[CourseBiasRule]:
    """マッチしたルールのリストを返す(G-Frame と G-Style から最大 1 つずつ)。

    排他規則:
      - G-Frame は最初にマッチしたルールのみ採用(同コース×枠範囲は一意)
      - G-Style も同様
      - G-Frame と G-Style は独立、両方発火可能
    """
    matched: list[CourseBiasRule] = []
    for rule in G_FRAME_RULES:
        try:
            if rule.matcher(course, surface, distance, frame, style):
                matched.append(rule)
                break  # G-Frame 排他
        except Exception:
            continue
    for rule in G_STYLE_RULES:
        try:
            if rule.matcher(course, surface, distance, frame, style):
                matched.append(rule)
                break  # G-Style 排他
        except Exception:
            continue
    return matched
