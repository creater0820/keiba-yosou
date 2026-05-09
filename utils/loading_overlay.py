"""
ローディング中の「走る馬」オーバーレイ演出(v1.6.2 — CSS-first 方式)。

【v1.6.0 / v1.6.1 の問題】
旧実装は `st.empty()` の placeholder に `st.markdown` で SVG/CSS を流し込む
方式だった。これだと placeholder は **Streamlit のメイン描画領域に属する**
ため:
  1. ラジオボタン押下 → Streamlit が rerun(暗転)を開始
  2. サーバ側で Python スクリプト全体が再実行(数秒)
  3. 新しい DOM が WebSocket で流れてきて DOM 置換 ← この時点で
     placeholder が一旦消えて、新しいスクリプトの先頭で再注入される
     までの間、overlay が見えない
  4. 馬が「描画完了の直前 1 秒」だけチラ見えする最悪 UX

【v1.6.2 の方針: CSS-first】
- 1 度だけ `document.head` に `<style>`、`document.body` 直下に
  `<div id="custom-loading-overlay">` を JS で注入
- 以降の Streamlit rerun では DOM 置換の対象外(<body> 直下なので
  Streamlit の描画 root <div id="root"> の外)→ 消えない
- 表示/非表示は **CSS の属性セレクタのみ** で決定:
    body:has([data-test-script-state="running"]) #custom-loading-overlay,
    body:has([data-test-script-state="rerunRequested"]) ...
    body:has([data-stale="true"]) ...
    body:has([data-testid="stStatusWidget"]) ...
  これで「rerun 開始の暗転と同時に表示 → ready で消える」をブラウザ
  ネイティブ速度で実現する(JS の MutationObserver より遥かに速い)。

【Streamlit 内部属性のカバレッジ】
Streamlit 公式 e2e テストや devtools 観察で確認できる主要属性:
  - `data-test-script-state`: "running" / "rerunRequested" / "notRunning"
    (Streamlit ≥ 1.20 系で使用)
  - `[data-testid="stStatusWidget"]`: 右上の "Running..." 状態バッジが
    存在する間だけ DOM にいる(Streamlit 全バージョン共通)
  - `[data-stale="true"]`: rerun 中に古い widget が一時的に持つ属性

【:has() ブラウザ対応】
- Chrome 105+ / Safari 15.4+ / Firefox 121+ でネイティブ対応
- 非対応環境向けに最小限の MutationObserver fallback で
  `body.__streamlit-running` クラスを toggle し、同 CSS で網羅

API:
- render_running_horse_overlay(message: str) -> None
    Streamlit script の **冒頭で 1 度だけ呼ぶ**。`window.__horseOverlay
    Installed` フラグで二重注入防止、後続 rerun では JS が早期 return。
"""

from __future__ import annotations

import streamlit as st


# ---------------------------------------------------------------------------
# 馬のシルエット SVG(JRA らしい黒〜茶系)
# ---------------------------------------------------------------------------
_HORSE_SVG = """
<svg class="running-horse-svg" viewBox="0 0 200 120" width="160" height="100"
     xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <ellipse cx="100" cy="60" rx="55" ry="22" fill="#5a3a22"/>
  <path d="M 145 50 L 175 25 L 180 35 L 155 65 Z" fill="#5a3a22"/>
  <ellipse cx="178" cy="28" rx="14" ry="9" fill="#5a3a22"/>
  <path d="M 184 18 L 188 10 L 190 18 Z" fill="#3a2412"/>
  <circle cx="183" cy="27" r="1.5" fill="#fff"/>
  <path d="M 155 35 Q 165 20, 170 32 Q 168 42, 158 45 Z" fill="#2a1808"/>
  <path d="M 45 55 Q 25 45, 15 65 Q 25 75, 45 70 Z" fill="#2a1808"
        class="horse-tail"/>
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


# ---------------------------------------------------------------------------
# CSS — Streamlit の rerun 状態に追従して即時表示する
# ---------------------------------------------------------------------------
_OVERLAY_CSS = """
#custom-loading-overlay {
    /* デフォルトは非表示。Streamlit が running 状態のときだけ flex 化 */
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(255, 255, 255, 0.92);
    backdrop-filter: blur(2px);
    -webkit-backdrop-filter: blur(2px);
    /* 最大級の z-index で純正暗転やヘッダの上に乗せる */
    z-index: 2147483647;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    pointer-events: all;
    overflow: hidden;
    user-select: none;
    -webkit-user-select: none;
}

/* === Streamlit running 状態の検出 === */
/* (a) :has() 対応ブラウザ: Streamlit が body の子孫に "running" 系の */
/*     データ属性を持つ要素を載せている間 overlay を表示 */
body:has([data-test-script-state="running"]) #custom-loading-overlay,
body:has([data-test-script-state="rerunRequested"]) #custom-loading-overlay,
body:has([data-stale="true"]) #custom-loading-overlay,
body:has([data-testid="stStatusWidget"]) #custom-loading-overlay {
    display: flex !important;
}
/* (b) :has() 非対応ブラウザ向け: JS で body にクラスを付与 */
body.__streamlit-running #custom-loading-overlay {
    display: flex !important;
}

/* === 走る馬本体 === */
#custom-loading-overlay .running-horse-svg {
    animation: hov-gallop-translate 2.4s linear infinite,
               hov-gallop-bounce    0.4s ease-in-out infinite;
    will-change: transform;
}
@keyframes hov-gallop-translate {
    0%   { transform: translateX(-220px); }
    100% { transform: translateX(calc(100vw + 220px)); }
}
@keyframes hov-gallop-bounce {
    0%, 100% { margin-top: 0; }
    50%      { margin-top: -8px; }
}

/* 4 本足を独立 keyframe で交互に */
#custom-loading-overlay .leg { transform-origin: top center; }
#custom-loading-overlay .leg-front-r { animation: hov-leg-a 0.4s linear infinite; }
#custom-loading-overlay .leg-front-l { animation: hov-leg-b 0.4s linear infinite; }
#custom-loading-overlay .leg-back-r  { animation: hov-leg-b 0.4s linear infinite; }
#custom-loading-overlay .leg-back-l  { animation: hov-leg-a 0.4s linear infinite; }
@keyframes hov-leg-a {
    0%, 100% { transform: rotate( 25deg); }
    50%      { transform: rotate(-25deg); }
}
@keyframes hov-leg-b {
    0%, 100% { transform: rotate(-25deg); }
    50%      { transform: rotate( 25deg); }
}

/* しっぽが軽くなびく */
#custom-loading-overlay .horse-tail {
    transform-origin: 45px 60px;
    animation: hov-tail-wave 0.6s ease-in-out infinite;
}
@keyframes hov-tail-wave {
    0%, 100% { transform: rotate(-6deg); }
    50%      { transform: rotate( 6deg); }
}

/* メッセージ */
#custom-loading-overlay .running-horse-message {
    margin-top: 1.4em;
    font-size: 1.4em;
    font-weight: 700;
    color: #2a2a2a;
    text-shadow: 0 1px 2px rgba(255, 255, 255, 0.9);
    letter-spacing: 0.05em;
}
#custom-loading-overlay .running-horse-sub {
    margin-top: 0.4em;
    font-size: 0.95em;
    color: #6a6a6a;
}

/* 走る軌跡(地面ライン) */
#custom-loading-overlay .running-horse-track {
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

/* prefers-reduced-motion: アニメ全停止 */
@media (prefers-reduced-motion: reduce) {
    #custom-loading-overlay .running-horse-svg,
    #custom-loading-overlay .running-horse-svg * {
        animation: none !important;
        margin-top: 0 !important;
    }
}

/* スマホ細幅 */
@media (max-width: 480px) {
    #custom-loading-overlay .running-horse-svg { width: 110px; height: 70px; }
    #custom-loading-overlay .running-horse-message { font-size: 1.1em; }
}
""".strip()


# ---------------------------------------------------------------------------
# JS インストーラ(冪等)
# ---------------------------------------------------------------------------
# Streamlit は `st.markdown(unsafe_allow_html=True)` で渡された
# `<script>` を **iframe ではなく親ドキュメントで実行** する場合と、
# `components.html` で iframe 内実行する場合がある。前者なら
# document.head/body に直接注入できる。後者だと iframe 内に注入され
# 親ドキュメントに届かない。
#
# v1.6.2 では `st.markdown` 経由 + `<script>` で `parent.document` を
# 試行した上で `document` にフォールバックする両対応の堅牢実装にする。
_INSTALLER_JS_TEMPLATE = """
(function() {
    // 注入先ドキュメントを決定。Streamlit が iframe 内で動く場合は
    // window.parent.document、通常実行時は document。
    var doc = document;
    try {
        if (window.parent && window.parent.document
            && window.parent !== window) {
            // parent からは同一オリジン制約で読めない場合あり、try/catch
            doc = window.parent.document;
        }
    } catch (e) {
        doc = document;
    }

    // 二重注入防止フラグ(同一 doc 上で 1 回のみ install)
    if (doc.__horseOverlayInstalled) return;
    doc.__horseOverlayInstalled = true;

    // ---- ① <head> に <style> を 1 度だけ注入 ----
    var style = doc.createElement('style');
    style.id = 'custom-loading-overlay-css';
    style.textContent = `__OVERLAY_CSS__`;
    doc.head.appendChild(style);

    // ---- ② <body> 直下に overlay 要素を 1 度だけ生成 ----
    var overlay = doc.createElement('div');
    overlay.id = 'custom-loading-overlay';
    overlay.setAttribute('role', 'status');
    overlay.setAttribute('aria-live', 'polite');
    overlay.setAttribute('aria-label', `__MESSAGE__`);
    overlay.innerHTML = `
        <div class="running-horse-track"></div>
        __HORSE_SVG__
        <div class="running-horse-message">__MESSAGE__</div>
        <div class="running-horse-sub">__SUB_MESSAGE__</div>
    `;
    doc.body.appendChild(overlay);

    // ---- ③ :has() 非対応ブラウザ向け fallback ----
    // body.__streamlit-running を Streamlit の状態に応じて toggle する。
    // Chrome 105+ / Safari 15.4+ / Firefox 121+ では :has() ネイティブ
    // セレクタが効くので、この観測は冗長だが安全のため常時有効化。
    var hasSupports = false;
    try {
        hasSupports = CSS.supports('selector(body:has(*))');
    } catch (e) {
        hasSupports = false;
    }
    function refreshRunningClass() {
        var running = doc.querySelector(
            '[data-test-script-state="running"],'
            + '[data-test-script-state="rerunRequested"],'
            + '[data-stale="true"],'
            + '[data-testid="stStatusWidget"]'
        );
        doc.body.classList.toggle('__streamlit-running', !!running);
    }
    refreshRunningClass();
    var obs = new MutationObserver(refreshRunningClass);
    obs.observe(doc.documentElement, {
        attributes: true,
        subtree: true,
        childList: true,
        attributeFilter: [
            'data-test-script-state',
            'data-stale',
            'data-testid',
        ],
    });
})();
""".strip()


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------
def render_running_horse_overlay(
    message: str = "予想を計算中…",
    sub_message: str = "馬たちが走っています 🏇",
) -> None:
    """走る馬オーバーレイをページに 1 度だけインストールする。

    呼び出し場所: app.py の `st.set_page_config()` の直後で 1 回だけ。
    インストール後は Streamlit の rerun 状態(`data-test-script-state` 等)
    に応じて CSS が自動で表示/非表示を切り替えるため、後続のボタン押下や
    ラジオ操作で追加の Python 処理は **一切不要**。

    引数:
        message: 大見出し(例: 「予想を計算中…」)。aria-label にも使用。
        sub_message: 補足メッセージ。

    安全性:
    - JS は IIFE で `__horseOverlayInstalled` フラグ管理 → 二重注入なし
    - JS の文字列補間は textContent / innerHTML 経由で文字列リテラルに
      埋め込むため、`message` に `<script>` を入れても閉じタグで escape
      されない限り再パースされない。とはいえ XSS 防止のため `<` `>` を
      事前置換する。
    """
    # 文字列リテラル内に直接埋め込むので、最低限のエスケープを実施。
    def _esc(s: str) -> str:
        return (
            (s or "")
            .replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    js = (
        _INSTALLER_JS_TEMPLATE
        .replace("__OVERLAY_CSS__", _OVERLAY_CSS)
        .replace("__HORSE_SVG__", _HORSE_SVG)
        .replace("__MESSAGE__", _esc(message))
        .replace("__SUB_MESSAGE__", _esc(sub_message))
    )
    html = f"<script>{js}</script>"
    st.markdown(html, unsafe_allow_html=True)
