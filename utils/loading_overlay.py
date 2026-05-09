"""
ローディング中の「走る馬」オーバーレイ演出(v1.6.4 — 状態機械 + 最小表示時間)。

【経緯】
- v1.6.0: `st.empty()` placeholder 方式 → rerun 中に消えて終盤チラ見えだけ
- v1.6.1: MutationObserver で DOM 注入を検知 → 新 DOM 後にしか発火せず同じ
- v1.6.2: CSS-first(`body:has([data-test-script-state="running"])`)
          → ページロード時 / 何でもない瞬間にも常時表示する致命バグ
- v1.6.3: ホワイトリスト方式(予想実行ボタン + 競馬場ラジオの click 検出)
          → click 自体が listener に届かない / 即座に hide される問題
- **v1.6.4(本実装)**: 状態機械 + 最小表示時間 800ms + 多重イベント検出
          + capture phase + DEBUG ログ + Python 側フォールバック

【設計】
1. **状態機械**: IDLE → SHOWN → MIN_TIME_PASSED → RUNNING_DETECTED → IDLE
   - 最低 800ms は必ず表示(点滅して見えない問題を回避)
   - running を観測しないままなら 5 秒で safety hide
   - running を観測したら running 終了まで hide しない

2. **イベント検出を多重化**:
   - `click` / `change` / `pointerdown` を **capture phase** で attach
     → React の `stopPropagation()` を回避(capture は React より先に発火)
   - ボタンテキストは whitespace 除去で「予想実行」マッチ(emoji 🎯 や
     全角空白に対応)
   - ラジオは `<input type="radio">` / `<label>` 親 / `[role="radio"]` /
     `[data-baseweb="radio"]` を全網羅
   - 競馬場フィルタかは親グループのテキストに「競馬場」を含むかで判定

3. **iframe 両対応**: `parent.document` を try で取得し、不可なら
   `document` にフォールバック。両方が同一 origin なら listener も両方に
   attach する案も検討したが、二重発火リスクのため doc 1 つに絞る。

4. **DEBUG ログ**: `console.log('[HorseOverlay]', ...)` で各段階を出力。
   実機 DevTools で「どこで止まっているか」を即座に判断可能。
   本番運用では `_DEBUG_DEFAULT` を `False` にして抑制(必要時に True)。

5. **Python 側フォールバック**: 予想実行ボタン押下時に
   `<script>window.__showHorseOverlay && window.__showHorseOverlay()</script>`
   を打って JS 経路が死んでいる時の保険を確保。状態機械の早期 return で
   二重発火しても無害。

API:
- render_running_horse_overlay(message, sub_message, debug=None)
    Streamlit script の **冒頭で 1 度だけ呼ぶ**。`__horseOverlayInstalled`
    フラグで二重注入防止。後続 rerun では JS が早期 return。
- trigger_overlay_inline(message=None)
    Python 側から `window.__showHorseOverlay()` を呼び出すヘルパ。
    予想実行ボタン押下時の保険として利用。
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
# CSS — `is-visible` クラスで明示的に表示制御(CSS-first :has() 方式は
# 廃止。常時表示の事故を絶対起こさないため、表示は JS が ON/OFF する)
# ---------------------------------------------------------------------------
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
# JS インストーラ:状態機械 + 最小表示時間 + capture phase event listener
# ---------------------------------------------------------------------------
# `__DEBUG__` プレースホルダで debug=True/False を埋め込む。
_INSTALLER_JS_TEMPLATE = """
(function() {
    const DEBUG = __DEBUG__;
    function log() {
        if (!DEBUG) return;
        try { console.log.apply(console, ['[HorseOverlay]'].concat(
            Array.prototype.slice.call(arguments))); } catch(e){}
    }

    // === doc 解決:iframe 両対応 ===
    let doc;
    let usingParent = false;
    try {
        if (window.parent && window.parent !== window
                && window.parent.document) {
            doc = window.parent.document;
            usingParent = true;
        } else {
            doc = document;
        }
    } catch (e) {
        doc = document;
        log('parent.document blocked, using document', e && e.message);
    }
    log('installed at', new Date().toISOString(),
        'doc:', usingParent ? 'parent' : 'self');

    // 二重注入防止(同一 doc で 1 回のみ)
    if (doc.__horseOverlayInstalled) {
        log('already installed, skipping');
        return;
    }
    doc.__horseOverlayInstalled = true;

    // === ① <head> に <style> ===
    const style = doc.createElement('style');
    style.id = 'custom-loading-overlay-css';
    style.textContent = `__OVERLAY_CSS__`;
    doc.head.appendChild(style);

    // === ② <body> 直下に overlay 要素 ===
    const overlay = doc.createElement('div');
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
    log('overlay element injected');

    // === 状態機械 ===
    const STATE = {
        IDLE: 0,
        SHOWN: 1,
        MIN_TIME_PASSED: 2,
        RUNNING_DETECTED: 3,
    };
    let state = STATE.IDLE;
    let shownAt = 0;
    let runObserver = null;
    let safetyTimer = null;
    let minTimeTimer = null;

    function showOverlay() {
        if (state !== STATE.IDLE) {
            log('showOverlay ignored, state=', state);
            return;
        }
        state = STATE.SHOWN;
        shownAt = Date.now();
        overlay.classList.add('is-visible');
        log('showOverlay → SHOWN');

        // 最小表示時間 800ms
        clearTimeout(minTimeTimer);
        minTimeTimer = setTimeout(function() {
            if (state === STATE.SHOWN) {
                state = STATE.MIN_TIME_PASSED;
                log('min time passed (800ms) → MIN_TIME_PASSED');
                tryHide();
            }
        }, 800);

        // running 状態の監視
        if (runObserver) try { runObserver.disconnect(); } catch(e){}
        runObserver = new MutationObserver(function() {
            const running = doc.querySelector(
                '[data-test-script-state="running"],'
                + '[data-test-script-state="rerunRequested"],'
                + '[data-stale="true"],'
                + '[data-testid="stStatusWidget"]'
            );
            if (running && state < STATE.RUNNING_DETECTED) {
                state = STATE.RUNNING_DETECTED;
                log('running detected, will keep overlay until done');
            } else if (!running && state === STATE.RUNNING_DETECTED) {
                log('running ended');
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
        // 即時 1 回チェック(showOverlay 直後に既に running の可能性)
        setTimeout(function() {
            const r = doc.querySelector(
                '[data-test-script-state="running"],'
                + '[data-test-script-state="rerunRequested"],'
                + '[data-stale="true"],'
                + '[data-testid="stStatusWidget"]'
            );
            if (r && state < STATE.RUNNING_DETECTED) {
                state = STATE.RUNNING_DETECTED;
                log('running detected (initial check)');
            }
        }, 50);

        // safety timer: 30 秒で必ず hide(暴走防止)
        clearTimeout(safetyTimer);
        safetyTimer = setTimeout(function() {
            log('safety hide (30s elapsed)');
            forceHide();
        }, 30000);
    }

    function tryHide() {
        const elapsed = Date.now() - shownAt;
        if (elapsed < 800) {
            log('tryHide: still under min time (' + elapsed + 'ms)');
            return;
        }
        if (state === STATE.RUNNING_DETECTED) {
            // running は監視中。MutationObserver が「ended」を見たら
            // また tryHide が呼ばれる。ここでは何もしない。
            return;
        }
        if (state === STATE.MIN_TIME_PASSED) {
            // running 観測しないまま min time 経過。
            // 5 秒で見切り hide(誤発火対策)。
            if (elapsed > 5000) {
                log('tryHide: no running observed within 5s, hiding');
                forceHide();
            } else {
                setTimeout(tryHide, 500);
            }
            return;
        }
        // SHOWN, IDLE: 直前にユーザ操作で showOverlay が呼ばれた直後など。
        // 状態遷移を待つ
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
        log('forceHide → IDLE');
    }

    // === ボタンクリック検出(capture phase で React より先に発火)===
    function isPredictBtnText(text) {
        // 全角空白・emoji 等を除去して「予想実行」を含むかチェック
        const norm = (text || '').replace(/\\s+/g, '');
        return norm.indexOf('予想実行') !== -1;
    }
    doc.body.addEventListener('click', function(e) {
        const target = e.target;
        if (!target || !target.closest) return;
        const btn = target.closest('button, [role="button"]');
        if (!btn) return;
        if (isPredictBtnText(btn.textContent)) {
            log('predict button clicked, text=',
                (btn.textContent || '').slice(0, 40));
            showOverlay();
        }
    }, { capture: true });

    // === ラジオ検出(複数パターン)===
    function findRadioFromTarget(target) {
        if (!target || !target.closest) return null;
        // <input type="radio">
        if (target.tagName === 'INPUT' && target.type === 'radio') {
            return target;
        }
        // <label> 親
        const label = target.closest('label');
        if (label) {
            const r = label.querySelector('input[type="radio"]');
            if (r) return r;
        }
        // [role="radio"](Streamlit 新版)
        const roleRadio = target.closest('[role="radio"]');
        if (roleRadio) return roleRadio;
        // [data-baseweb="radio"]
        const baseRadio = target.closest('[data-baseweb="radio"]');
        if (baseRadio) {
            const r = baseRadio.querySelector('input[type="radio"]')
                       || baseRadio;
            return r;
        }
        return null;
    }
    function checkAndShowForRadio(radio, eventName) {
        if (!radio || !radio.closest) return;
        // 親グループのテキストに「競馬場」を含むか
        const group = radio.closest(
            '[role="radiogroup"], [data-baseweb="radio-group"],'
            + ' [data-baseweb="radio"], [data-testid*="radio"],'
            + ' [data-testid*="Radio"]'
        );
        const groupText = ((group && group.textContent) || '')
                            .replace(/\\s+/g, '');
        if (groupText.indexOf('競馬場') !== -1
                || groupText.indexOf('表示する競馬場') !== -1) {
            log('course radio:', eventName,
                groupText.slice(0, 30));
            showOverlay();
        }
    }
    ['click', 'change', 'pointerdown'].forEach(function(evt) {
        doc.body.addEventListener(evt, function(e) {
            const radio = findRadioFromTarget(e.target);
            if (radio) checkAndShowForRadio(radio, evt);
        }, { capture: true });
    });

    // === 公開:Python 側からのフォールバック呼び出し用 ===
    window.__showHorseOverlay = showOverlay;
    window.__hideHorseOverlay = forceHide;
    if (usingParent) {
        // parent.document の場合は parent 側の window にも公開
        try {
            window.parent.__showHorseOverlay = showOverlay;
            window.parent.__hideHorseOverlay = forceHide;
        } catch (e) {}
    }

    log('event listeners attached (capture phase, click+change+pointerdown)');
})();
""".strip()


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------
_DEBUG_DEFAULT = False  # 本番デフォルト。実機トラブル時は True で再 deploy。


def render_running_horse_overlay(
    message: str = "予想を計算中…",
    sub_message: str = "馬たちが走っています 🏇",
    debug: bool | None = None,
) -> None:
    """走る馬オーバーレイをページに 1 度だけインストールする(v1.6.4)。

    呼び出し場所: app.py の `st.set_page_config()` の直後で 1 回だけ。
    インストール後、ユーザ操作(予想実行ボタン押下 / 競馬場ラジオ切替)を
    JS が capture phase で検出し、状態機械で最小 800ms 〜 running 終了まで
    overlay を表示する。

    引数:
        message: 大見出し(aria-label にも使用)
        sub_message: 補足メッセージ
        debug: True で console.log デバッグ出力。None なら _DEBUG_DEFAULT。
               実機トラブル時は debug=True で再 deploy → コンソール内容を
               確認 → 原因特定 → debug=False に戻す運用。
    """
    if debug is None:
        debug = _DEBUG_DEFAULT

    def _esc(s: str) -> str:
        # JS テンプレートリテラル + HTML 属性両用の最低限のエスケープ
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
    html = f"<script>{js}</script>"
    st.markdown(html, unsafe_allow_html=True)


def trigger_overlay_inline() -> None:
    """Python 側から overlay を即時表示するインライン script を打つ。

    使用シーン: 予想実行ボタンの handler 内で、JS event listener が
    何らかの理由で発火しないケースに備えた保険。状態機械の早期 return で
    二重発火しても無害。
    """
    st.markdown(
        '<script>'
        'try{'
        '(window.__showHorseOverlay||window.parent.__showHorseOverlay||'
        'function(){})()'
        '}catch(e){}'
        '</script>',
        unsafe_allow_html=True,
    )
