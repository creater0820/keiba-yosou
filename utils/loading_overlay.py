"""
ローディング中の「走る馬」オーバーレイ演出(v1.6.5 — 診断版)。

【主因仮説の確定】
Streamlit 1.30+(本プロジェクト 1.57.0)では `st.markdown(unsafe_allow_html=
True)` 経由の `<script>` タグはサニタイザで除去 / 実行されない仕様変更が
入った。v1.6.0〜v1.6.4 の overlay 注入が実機で機能しなかった根本原因は
ほぼこれ。診断のため v1.6.5 では以下を全て同時投入する:

1. **`components.html()` 経路一本化**:
   `streamlit.components.v1.html(..., height=0)` で 0px の不可視 iframe を
   作り、その中で実行された `<script>` から `window.parent.document` に
   overlay を注入する。Streamlit が公式に保証する script 実行経路。

2. **可視デバッグバッジ**:
   画面右下に `#horse-debug-badge` を常時表示し、JS の各段階で textContent
   を更新する(`ready` / `click@btn` / `SHOWN` / `RUNNING_DETECTED` 等)。
   お父様が DevTools を開かなくても、画面の右下を見るだけで「どこまで
   動いているか」が判別できる。

3. **DEBUG=True デフォルト**:
   `console.log('[HorseOverlay]', ...)` を強制出力。インストール証明 +
   iframe 検出 + CSP テスト + DOM 要素カウントを 1 度に出力。

4. **5 階層 textContent 検査**:
   仮想スクロール対策として、クリック要素から親方向に 5 階層遡って
   textContent を結合し「予想実行」「競馬場」を含むかチェック。

5. **document + parent.document 両方に listener attach**:
   どちらの context でクリックが発火するか不明なので両方仕掛ける。

6. **診断用強制表示 API**:
   `window.parent.__showHorseOverlay()` でいつでも overlay を表示可能。
   サイドバーの「🔧 診断モード → 馬を強制表示テスト」ボタンから呼ぶ。

【期待される父の確認手順】
1. アプリを開く → 画面右下に **黒い小さなバッジ** が見えるか
   - 見えない → JS 自体が動いていない(CSP / iframe sandbox 等)
   - `overlay: ready` と表示される → JS インストール成功
2. サイドバーの「🔧 診断モード」をチェック → 「馬を強制表示テスト」を押す
   - 馬が出る → 注入は OK、残るは「click イベントが届いていない」
   - 馬が出ない → CSS / overlay 要素の場所問題
3. 「予想実行」ボタンを押した瞬間、右下バッジが `click@btn` に変わるか
   - 変わる → イベントは捕捉されている、状態機械の問題
   - 変わらない → React で stopPropagation されている可能性

【厳守事項】
- ロジック v1.5 不変
- session_state 既存キー破壊禁止
- ページロード時に overlay は **絶対に表示しない**(バッジは出す)
- 診断 UI は v1.6.5 限定、原因特定後 v1.7 で削除予定
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components


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


_OVERLAY_CSS = """
#custom-loading-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(255, 255, 255, 0.92);
    backdrop-filter: blur(2px);
    -webkit-backdrop-filter: blur(2px);
    z-index: 2147483647;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    pointer-events: all;
    overflow: hidden;
    user-select: none;
    -webkit-user-select: none;
}
#custom-loading-overlay.is-visible {
    display: flex !important;
}
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
#custom-loading-overlay .horse-tail {
    transform-origin: 45px 60px;
    animation: hov-tail-wave 0.6s ease-in-out infinite;
}
@keyframes hov-tail-wave {
    0%, 100% { transform: rotate(-6deg); }
    50%      { transform: rotate( 6deg); }
}
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
#custom-loading-overlay .running-horse-track {
    position: absolute;
    left: 0; right: 0; top: 50%;
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

/* === v1.6.5 診断バッジ(画面右下に常時表示)=== */
#horse-debug-badge {
    position: fixed;
    bottom: 8px;
    right: 8px;
    background: #1a1a1a;
    color: #66ff66;
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 4px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    z-index: 2147483646;
    opacity: 0.85;
    pointer-events: none;
    border: 1px solid #444;
    line-height: 1.4;
    max-width: 320px;
    word-break: break-all;
}

@media (prefers-reduced-motion: reduce) {
    #custom-loading-overlay .running-horse-svg,
    #custom-loading-overlay .running-horse-svg * {
        animation: none !important;
        margin-top: 0 !important;
    }
}
@media (max-width: 480px) {
    #custom-loading-overlay .running-horse-svg { width: 110px; height: 70px; }
    #custom-loading-overlay .running-horse-message { font-size: 1.1em; }
}
""".strip()


# ---------------------------------------------------------------------------
# JS インストーラ:components.html(iframe)から window.parent.document に注入
# ---------------------------------------------------------------------------
_INSTALLER_JS_TEMPLATE = """
(function() {
    const DEBUG = __DEBUG__;
    function log() {
        if (!DEBUG) return;
        try {
            console.log.apply(
                console,
                ['[HorseOverlay]'].concat(
                    Array.prototype.slice.call(arguments)
                )
            );
        } catch(e){}
    }

    // === doc 解決:components.html は iframe で実行されるので必ず parent ===
    let doc = null;
    let docKind = '?';
    try {
        if (window.parent && window.parent !== window
                && window.parent.document) {
            doc = window.parent.document;
            docKind = 'parent';
        }
    } catch (e) {
        log('parent.document blocked:', e && e.message);
    }
    if (!doc) {
        // フォールバック: 最後の手段として self
        doc = document;
        docKind = 'self';
    }
    log('=== install start ===');
    log('doc kind:', docKind);
    log('window === window.parent:', window === window.parent);
    log('navigator.userAgent:', navigator.userAgent);

    // 二重注入防止フラグ
    if (doc.__horseOverlayInstalled) {
        log('already installed via', doc.__horseOverlayInstalledVia,
            ', skipping new install');
        return;
    }
    doc.__horseOverlayInstalled = true;
    doc.__horseOverlayInstalledVia = 'components.html';

    // === 環境調査ログ ===
    log('document.body exists:', !!doc.body);
    try {
        log('CSP test (eval):', (function(){
            try { eval('1'); return 'OK'; } catch(e){ return 'BLOCKED'; }
        })());
    } catch(e){}
    try {
        log('matched elements: button=' + doc.querySelectorAll('button').length
            + ', input[radio]=' + doc.querySelectorAll('input[type=\"radio\"]').length
            + ', role-radio=' + doc.querySelectorAll('[role=\"radio\"]').length
            + ', baseweb-radio=' + doc.querySelectorAll('[data-baseweb=\"radio\"]').length);
    } catch(e){}

    // === ① <head> に <style> ===
    var style = doc.createElement('style');
    style.id = 'custom-loading-overlay-css';
    style.textContent = `__OVERLAY_CSS__`;
    doc.head.appendChild(style);

    // === ② <body> 直下に overlay 要素 ===
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

    // === ③ 診断バッジ(画面右下に常時表示)===
    var badge = doc.createElement('div');
    badge.id = 'horse-debug-badge';
    badge.textContent = 'overlay: ready (' + docKind + ')';
    doc.body.appendChild(badge);
    log('overlay + badge injected');

    function setBadge(text) {
        try { badge.textContent = 'overlay: ' + text; } catch(e){}
        log('badge → ' + text);
    }

    // === 状態機械 ===
    var STATE = {
        IDLE: 0, SHOWN: 1, MIN_TIME_PASSED: 2, RUNNING_DETECTED: 3,
    };
    var state = STATE.IDLE;
    var shownAt = 0;
    var runObserver = null;
    var safetyTimer = null;
    var minTimeTimer = null;

    function showOverlay(reason) {
        if (state !== STATE.IDLE) {
            log('showOverlay ignored, state=', state);
            return;
        }
        state = STATE.SHOWN;
        shownAt = Date.now();
        overlay.classList.add('is-visible');
        setBadge('SHOWN(' + (reason || '?') + ')');

        clearTimeout(minTimeTimer);
        minTimeTimer = setTimeout(function() {
            if (state === STATE.SHOWN) {
                state = STATE.MIN_TIME_PASSED;
                setBadge('MIN_TIME_PASSED');
                tryHide();
            }
        }, 800);

        if (runObserver) try { runObserver.disconnect(); } catch(e){}
        runObserver = new MutationObserver(function() {
            var running = doc.querySelector(
                '[data-test-script-state="running"],'
                + '[data-test-script-state="rerunRequested"],'
                + '[data-stale="true"],'
                + '[data-testid="stStatusWidget"]'
            );
            if (running && state < STATE.RUNNING_DETECTED) {
                state = STATE.RUNNING_DETECTED;
                setBadge('RUNNING_DETECTED');
            } else if (!running && state === STATE.RUNNING_DETECTED) {
                setBadge('running ended');
                tryHide();
            }
        });
        runObserver.observe(doc.body, {
            attributes: true,
            subtree: true,
            attributeFilter: [
                'data-test-script-state', 'data-stale', 'data-testid',
            ],
        });
        // 即時 1 回チェック
        setTimeout(function() {
            var r = doc.querySelector(
                '[data-test-script-state="running"],'
                + '[data-test-script-state="rerunRequested"],'
                + '[data-stale="true"],'
                + '[data-testid="stStatusWidget"]'
            );
            if (r && state < STATE.RUNNING_DETECTED) {
                state = STATE.RUNNING_DETECTED;
                setBadge('RUNNING_DETECTED (initial)');
            }
        }, 50);

        clearTimeout(safetyTimer);
        safetyTimer = setTimeout(function() {
            setBadge('safety hide (30s)');
            forceHide();
        }, 30000);
    }

    function tryHide() {
        var elapsed = Date.now() - shownAt;
        if (elapsed < 800) return;
        if (state === STATE.RUNNING_DETECTED) {
            // observer の running ended 通知待ち
            return;
        }
        if (state === STATE.MIN_TIME_PASSED) {
            if (elapsed > 5000) {
                setBadge('hide (no-running 5s)');
                forceHide();
            } else {
                setTimeout(tryHide, 500);
            }
        }
    }

    function forceHide() {
        overlay.classList.remove('is-visible');
        state = STATE.IDLE;
        if (runObserver) {
            try { runObserver.disconnect(); } catch(e){}
            runObserver = null;
        }
        clearTimeout(safetyTimer);
        clearTimeout(minTimeTimer);
        setBadge('IDLE');
    }

    // === 5 階層 textContent 検査(仮想スクロール対策)===
    function getNearbyText(el, maxDepth) {
        if (!el) return '';
        maxDepth = maxDepth || 5;
        var text = '';
        var cur = el;
        for (var i = 0; i < maxDepth && cur; i++) {
            try { text += (cur.textContent || '') + ' '; } catch(e){}
            cur = cur.parentElement;
        }
        return text.replace(/\\s+/g, '');
    }

    // === ボタン + ラジオ検出(capture phase、複数イベント)===
    function handleEvent(e, evtName) {
        var t = e.target;
        if (!t || !t.closest) return;

        // (a) 「予想実行」ボタン
        var btn = t.closest('button, [role="button"]');
        if (btn) {
            var btext = (btn.textContent || '').replace(/\\s+/g, '');
            if (btext.indexOf('予想実行') !== -1) {
                setBadge('click@btn:予想実行');
                showOverlay('predict');
                return;
            }
        }

        // (b) 競馬場ラジオ — 5 階層 textContent で判定
        var nearby = getNearbyText(t, 5);
        if (nearby.indexOf('競馬場') !== -1) {
            // ラジオ要素の存在確認(誤発火防止)
            var radio = (
                (t.tagName === 'INPUT' && t.type === 'radio') ? t :
                (t.closest('label') ? t.closest('label').querySelector(
                    'input[type="radio"]') : null)
                || t.closest('[role="radio"]')
                || t.closest('[data-baseweb="radio"]')
            );
            if (radio) {
                setBadge('click@radio:競馬場(' + evtName + ')');
                showOverlay('course');
            }
        }
    }

    var EVENTS = ['click', 'change', 'pointerdown'];
    EVENTS.forEach(function(evt) {
        try {
            doc.body.addEventListener(evt, function(e) {
                handleEvent(e, evt);
            }, { capture: true });
        } catch(e) { log('attach failed for', evt, e); }
    });
    // self.document にも(iframe context)念のため
    if (docKind === 'parent') {
        try {
            EVENTS.forEach(function(evt) {
                document.body.addEventListener(evt, function(e) {
                    handleEvent(e, evt);
                }, { capture: true });
            });
        } catch(e) {}
    }
    log('event listeners attached on', docKind, 'doc.body');

    // === 公開 API(Python 側 / 診断ボタンから呼ぶ用)===
    doc.__showHorseOverlay = function(reason) {
        showOverlay(reason || 'manual');
    };
    doc.__hideHorseOverlay = forceHide;
    doc.__horseOverlayBadge = function(text) { setBadge(text); };
    // window 経由でも公開
    try {
        var w = (docKind === 'parent') ? window.parent : window;
        w.__showHorseOverlay = doc.__showHorseOverlay;
        w.__hideHorseOverlay = doc.__hideHorseOverlay;
        w.__horseOverlayBadge = doc.__horseOverlayBadge;
    } catch(e) {}

    log('=== install complete ===');
})();
""".strip()


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------
_DEBUG_DEFAULT = True  # v1.6.5 診断版: デフォルト True で出荷


def _build_html(
    message: str,
    sub_message: str,
    debug: bool,
) -> str:
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
        .replace("__DEBUG__", "true" if debug else "false")
        .replace("__OVERLAY_CSS__", _OVERLAY_CSS)
        .replace("__HORSE_SVG__", _HORSE_SVG)
        .replace("__MESSAGE__", _esc(message))
        .replace("__SUB_MESSAGE__", _esc(sub_message))
    )
    return f"<script>{js}</script>"


def render_running_horse_overlay(
    message: str = "予想を計算中…",
    sub_message: str = "馬たちが走っています 🏇",
    debug: bool | None = None,
) -> None:
    """走る馬オーバーレイを `components.html()` 経由で 1 度だけインストール。

    v1.6.5: Streamlit 1.30+ で `st.markdown(unsafe_allow_html=True)` 経由の
    `<script>` がサニタイズされて実行されない既知問題への対応。
    `streamlit.components.v1.html()` で 0px の不可視 iframe を作成し、
    その中で実行された `<script>` から `window.parent.document` に overlay
    を注入する。これは Streamlit が公式に保証する script 実行経路。
    """
    if debug is None:
        debug = _DEBUG_DEFAULT
    html = _build_html(message, sub_message, debug)
    # height=0 で見た目上は何も表示しないが、内部で iframe が作られて script
    # が確実に実行される。Streamlit が DOM にこの iframe を maintain する間
    # 効果が持続する(rerun を跨いだ二重注入は __horseOverlayInstalled で防御)
    components.html(html, height=0, width=0)


def trigger_overlay_inline(reason: str = "predict") -> None:
    """Python 側から overlay を即時表示する。

    `components.html()` 経由で `window.parent.__showHorseOverlay(reason)` を
    呼ぶ。JS event listener が何らかの理由で発火しないケースの保険。
    """
    js = (
        "(function(){try{"
        "var w=window.parent||window;"
        f"if(w.__showHorseOverlay)w.__showHorseOverlay({reason!r});"
        "else{try{w.__horseOverlayBadge&&w.__horseOverlayBadge('show NOT FOUND');}catch(e){}"
        "if(typeof alert==='function')alert('__showHorseOverlay not found');}"
        "}catch(e){}})();"
    )
    components.html(f"<script>{js}</script>", height=0, width=0)


def diagnostic_force_show() -> None:
    """サイドバーの「馬を強制表示テスト」ボタンから呼ばれる診断用。

    overlay show + alert で「JS 経路が生きているか」を即座に判別可能にする。
    """
    js = (
        "(function(){try{"
        "var w=window.parent||window;"
        "var ok=!!w.__showHorseOverlay;"
        "if(ok){w.__showHorseOverlay('manual-test');}"
        "if(typeof alert==='function'){"
        "alert('__showHorseOverlay='+(ok?'FOUND, called':'NOT FOUND'));"
        "}"
        "}catch(e){if(typeof alert==='function')alert('ERROR: '+e.message);}})();"
    )
    components.html(f"<script>{js}</script>", height=0, width=0)


def diagnostic_status() -> None:
    """サイドバーの「listener 状況確認」ボタンから呼ばれる診断用。

    バッジに「installed = true/false」を出して、お父様が右下を見るだけで
    JS インストール成否を判別できるようにする。
    """
    js = (
        "(function(){try{"
        "var doc=window.parent?window.parent.document:document;"
        "var inst=!!doc.__horseOverlayInstalled;"
        "var via=doc.__horseOverlayInstalledVia||'?';"
        "var w=window.parent||window;"
        "if(w.__horseOverlayBadge){"
        " w.__horseOverlayBadge('installed='+inst+' via='+via);"
        "}"
        "if(typeof alert==='function'){"
        "alert('horseOverlayInstalled='+inst+', via='+via+', "
        "showFn='+(typeof w.__showHorseOverlay));}"
        "}catch(e){if(typeof alert==='function')alert('STATUS ERR: '+e.message);}})();"
    )
    components.html(f"<script>{js}</script>", height=0, width=0)
