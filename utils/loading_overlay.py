"""
ローディング中の「走る馬」オーバーレイ演出(v1.6)。

予想実行や競馬場フィルタ切替の数秒待ちで、お父様が「動いているのか?」と
不安にならないよう、画面中央〜右へ駆け抜ける馬のアニメーションを表示する。

実装:
- 完全 inline(SVG + CSS keyframes)、外部 CDN / GIF への依存ゼロ
- `position: fixed` でフルスクリーンに半透明オーバーレイ
- 馬は SVG のシルエット(JRA らしい黒〜茶系)で、4 本足を交互に動かす
  keyframes と、画面横方向の translateX を組み合わせる
- 完了時は呼び出し側が `placeholder.empty()` で除去する想定
- `prefers-reduced-motion` ユーザは静止画 + テキストのみ表示

API:
- render_running_horse_overlay(message: str) -> None
    Streamlit の placeholder 内で呼び出す。1 度の `st.markdown` で全部を
    出力する単一 widget なので、@st.fragment scope に書き込んでも安全。

呼び出しパターン:
    placeholder = st.empty()
    with placeholder.container():
        render_running_horse_overlay("予想を計算中…")
    # ... 重い処理 ...
    placeholder.empty()
"""

from __future__ import annotations

import streamlit as st


# 馬のシルエット SVG。JRA らしい黒〜茶系。viewBox は 200x100 程度の馬体型。
# 4 本足を独立 path にして CSS で個別に rotate アニメーションをかける。
_HORSE_SVG = """
<svg class="running-horse-svg" viewBox="0 0 200 120" width="160" height="100"
     xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <!-- 体 (胴) -->
  <ellipse cx="100" cy="60" rx="55" ry="22" fill="#5a3a22"/>
  <!-- 首 -->
  <path d="M 145 50 L 175 25 L 180 35 L 155 65 Z" fill="#5a3a22"/>
  <!-- 頭 -->
  <ellipse cx="178" cy="28" rx="14" ry="9" fill="#5a3a22"/>
  <!-- 耳 -->
  <path d="M 184 18 L 188 10 L 190 18 Z" fill="#3a2412"/>
  <!-- 目 -->
  <circle cx="183" cy="27" r="1.5" fill="#fff"/>
  <!-- たてがみ -->
  <path d="M 155 35 Q 165 20, 170 32 Q 168 42, 158 45 Z" fill="#2a1808"/>
  <!-- しっぽ -->
  <path d="M 45 55 Q 25 45, 15 65 Q 25 75, 45 70 Z" fill="#2a1808"
        class="horse-tail"/>
  <!-- 4 本足: 前左 / 前右 / 後左 / 後右、それぞれ独立クラスで個別アニメ -->
  <rect x="135" y="75" width="6" height="35" fill="#5a3a22"
        class="leg leg-front-r" rx="2"/>
  <rect x="125" y="75" width="6" height="35" fill="#3a2412"
        class="leg leg-front-l" rx="2"/>
  <rect x="70" y="75" width="6" height="35" fill="#5a3a22"
        class="leg leg-back-r" rx="2"/>
  <rect x="60" y="75" width="6" height="35" fill="#3a2412"
        class="leg leg-back-l" rx="2"/>
</svg>
""".strip()


# CSS。keyframes で
# - .running-horse-svg を画面外左から右へ translateX で疾走
# - 4 本足を独立 keyframe で交互に rotate(ギャロップ風)
# - しっぽが軽くなびく
# - 馬本体は ground line に対して上下バウンス
_OVERLAY_CSS = """
<style>
.running-horse-overlay {
    position: fixed;
    inset: 0;
    background: rgba(255, 255, 255, 0.72);
    backdrop-filter: blur(2px);
    -webkit-backdrop-filter: blur(2px);
    z-index: 9999;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    pointer-events: auto;
    overflow: hidden;
}

/* 馬本体: 画面外左 → 画面外右へ走り抜けてループ */
.running-horse-overlay .running-horse-svg {
    animation: gallop-translate 2.4s linear infinite,
               gallop-bounce    0.4s ease-in-out infinite;
    will-change: transform;
}

@keyframes gallop-translate {
    0%   { transform: translateX(-220px) translateY(0); }
    100% { transform: translateX(calc(100vw + 220px)) translateY(0); }
}
@keyframes gallop-bounce {
    0%, 100% { margin-top: 0; }
    50%      { margin-top: -8px; }
}

/* 4 本足 transform-origin = 上端で rotate */
.running-horse-overlay .leg {
    transform-origin: top center;
}
.running-horse-overlay .leg-front-r { animation: gallop-leg-a 0.4s linear infinite; }
.running-horse-overlay .leg-front-l { animation: gallop-leg-b 0.4s linear infinite; }
.running-horse-overlay .leg-back-r  { animation: gallop-leg-b 0.4s linear infinite; }
.running-horse-overlay .leg-back-l  { animation: gallop-leg-a 0.4s linear infinite; }

@keyframes gallop-leg-a {
    0%, 100% { transform: rotate( 25deg); }
    50%      { transform: rotate(-25deg); }
}
@keyframes gallop-leg-b {
    0%, 100% { transform: rotate(-25deg); }
    50%      { transform: rotate( 25deg); }
}

/* しっぽが軽くなびく */
.running-horse-overlay .horse-tail {
    transform-origin: 45px 60px;
    animation: tail-wave 0.6s ease-in-out infinite;
}
@keyframes tail-wave {
    0%, 100% { transform: rotate(-6deg); }
    50%      { transform: rotate( 6deg); }
}

/* メッセージ */
.running-horse-overlay .running-horse-message {
    margin-top: 1.4em;
    font-size: 1.4em;
    font-weight: 700;
    color: #2a2a2a;
    text-shadow: 0 1px 2px rgba(255,255,255,0.9);
    letter-spacing: 0.05em;
}
.running-horse-overlay .running-horse-sub {
    margin-top: 0.4em;
    font-size: 0.95em;
    color: #6a6a6a;
}

/* 走る軌跡(地面ライン) */
.running-horse-overlay .running-horse-track {
    position: absolute;
    left: 0;
    right: 0;
    top: 50%;
    height: 2px;
    background: linear-gradient(
        to right,
        transparent,
        rgba(60, 80, 60, 0.35) 20%,
        rgba(60, 80, 60, 0.35) 80%,
        transparent
    );
    transform: translateY(40px);
}

/* prefers-reduced-motion: アニメーション完全停止 + 静止馬 + テキストのみ */
@media (prefers-reduced-motion: reduce) {
    .running-horse-overlay .running-horse-svg {
        animation: none;
        margin-top: 0;
    }
    .running-horse-overlay .leg,
    .running-horse-overlay .horse-tail {
        animation: none;
    }
}

/* スマホ等の細幅: 馬と文字を縮小 */
@media (max-width: 480px) {
    .running-horse-overlay .running-horse-svg { width: 110px; height: 70px; }
    .running-horse-overlay .running-horse-message { font-size: 1.1em; }
}
</style>
""".strip()


def render_running_horse_overlay(
    message: str = "予想を計算中…",
    sub_message: str = "馬たちが走っています 🏇",
) -> None:
    """画面中央に走る馬のオーバーレイを表示する。

    呼び出し側で `st.empty()` の placeholder.container() 内で呼び出し、
    重い処理が終わったら placeholder.empty() で除去する想定。

    引数:
        message: 大きな見出しメッセージ(例: 「予想を計算中…」)
        sub_message: サブメッセージ(任意、軽い補足文)
    """
    # 安全のため最低限のエスケープ(Streamlit の通常運用では制御文字なし)
    msg = (message or "").replace("<", "&lt;").replace(">", "&gt;")
    sub = (sub_message or "").replace("<", "&lt;").replace(">", "&gt;")
    html = f"""
{_OVERLAY_CSS}
<div class="running-horse-overlay" role="status" aria-live="polite"
     aria-label="{msg}">
  <div class="running-horse-track"></div>
  {_HORSE_SVG}
  <div class="running-horse-message">{msg}</div>
  <div class="running-horse-sub">{sub}</div>
</div>
""".strip()
    st.markdown(html, unsafe_allow_html=True)
