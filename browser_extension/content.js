// Zotify Bridge — content script pour Spotify Web Player.
//
// Strategie d'affichage (par ordre de preference) :
//   1. Bouton INLINE dans la barre d'action a cote du bouton Play (UX
//      premium, mais selecteurs DOM Spotify peuvent changer).
//   2. Bouton FLOTTANT (fallback robuste, toujours visible) en haut a
//      droite de la zone de contenu.
//
// Le bouton n'apparait que sur les pages /track/, /album/, /playlist/
// (avec eventuel prefixe locale comme /intl-fr/).

(function () {
    "use strict";

    const INLINE_BTN_ID = "zotify-inline-btn";
    const FLOAT_BTN_ID  = "zotify-float-btn";
    const TOAST_ID      = "zotify-toast";

    const PAGE_RE = /\/(track|album|playlist)\/([A-Za-z0-9]{20,32})/;

    function detectPage() {
        const m = PAGE_RE.exec(location.pathname);
        return m ? { kind: m[1], id: m[2] } : null;
    }

    function canonicalUrl(page) {
        return `https://open.spotify.com/${page.kind}/${page.id}`;
    }

    // ----- TOAST -------------------------------------------------------------
    function showToast(msg, ok) {
        let toast = document.getElementById(TOAST_ID);
        if (!toast) {
            toast = document.createElement("div");
            toast.id = TOAST_ID;
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.classList.remove("zotify-toast-ok", "zotify-toast-err");
        toast.classList.add(ok ? "zotify-toast-ok" : "zotify-toast-err");
        toast.classList.add("zotify-toast-visible");
        clearTimeout(toast._hideTimer);
        toast._hideTimer = setTimeout(() => {
            toast.classList.remove("zotify-toast-visible");
        }, 3500);
    }

    // ----- ACTION -----------------------------------------------------------
    async function triggerDownload(targetBtn) {
        const page = detectPage();
        if (!page) {
            showToast("Page non supportee.", false);
            return;
        }
        const url = canonicalUrl(page);

        if (targetBtn) {
            targetBtn.classList.add("zotify-loading");
            targetBtn.disabled = true;
        }
        try {
            const response = await chrome.runtime.sendMessage({
                type: "zotify-download",
                url,
            });
            if (response && response.ok) {
                showToast("Telechargement lance dans Zotify.", true);
            } else {
                const err = (response && response.error) || "Echec inconnu.";
                showToast(err, false);
            }
        } catch (exc) {
            showToast(`Erreur : ${exc.message || exc}`, false);
        } finally {
            if (targetBtn) {
                targetBtn.classList.remove("zotify-loading");
                targetBtn.disabled = false;
            }
        }
    }

    // ----- BUTTON FACTORIES -------------------------------------------------
    function makeInlineButton() {
        const btn = document.createElement("button");
        btn.id = INLINE_BTN_ID;
        btn.type = "button";
        btn.setAttribute("aria-label", "Telecharger via Zotify");
        btn.title = "Telecharger via Zotify";
        btn.innerHTML = `
            <svg class="zotify-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="7 10 12 15 17 10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            <span class="zotify-label">Zotify</span>
        `;
        btn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            triggerDownload(btn);
        });
        return btn;
    }

    function makeFloatingButton() {
        const btn = document.createElement("button");
        btn.id = FLOAT_BTN_ID;
        btn.type = "button";
        btn.setAttribute("aria-label", "Telecharger via Zotify");
        btn.title = "Telecharger via Zotify";
        btn.innerHTML = `
            <svg class="zotify-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="7 10 12 15 17 10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            <span class="zotify-label">Telecharger via Zotify</span>
        `;
        btn.addEventListener("click", () => triggerDownload(btn));
        return btn;
    }

    // ----- INJECTION STRATEGIES ---------------------------------------------
    function findActionBar() {
        // 1) testid stable historiquement
        const byTestid = document.querySelector('[data-testid="action-bar-row"]');
        if (byTestid) return byTestid;

        // 2) remonter depuis le bouton Play (testid tres stable)
        const playBtn = document.querySelector('button[data-testid="play-button"]');
        if (playBtn) {
            let parent = playBtn.parentElement;
            for (let i = 0; i < 6 && parent; i++) {
                const btnCount = parent.querySelectorAll(":scope > * button").length;
                if (btnCount >= 2) return parent;
                parent = parent.parentElement;
            }
            return playBtn.parentElement;
        }

        // 3) fallback : zone "entityHeader"
        const header = document.querySelector('[data-testid="entityTitle"]');
        if (header && header.parentElement) return header.parentElement.parentElement;

        return null;
    }

    function injectInline() {
        const bar = findActionBar();
        if (!bar) return false;
        // Deja injecte ?
        if (bar.querySelector("#" + INLINE_BTN_ID)) return true;
        const btn = makeInlineButton();
        bar.appendChild(btn);
        return true;
    }

    function ensureFloating() {
        let btn = document.getElementById(FLOAT_BTN_ID);
        if (!btn) {
            btn = makeFloatingButton();
            document.body.appendChild(btn);
        }
        return btn;
    }

    function removeAll() {
        document.getElementById(INLINE_BTN_ID)?.remove();
        document.getElementById(FLOAT_BTN_ID)?.remove();
    }

    // ----- MAIN LOOP --------------------------------------------------------
    function refresh() {
        const page = detectPage();
        if (!page) {
            removeAll();
            return;
        }
        const inlineOk = injectInline();
        if (inlineOk) {
            document.getElementById(FLOAT_BTN_ID)?.remove();
        } else {
            ensureFloating();
        }
    }

    // Re-essaie de placer le bouton inline pendant 8s apres navigation
    // (Spotify monte sa barre d'action de facon asynchrone).
    let retryTimer = null;
    function startRetry() {
        if (retryTimer) clearInterval(retryTimer);
        let attempts = 0;
        retryTimer = setInterval(() => {
            attempts++;
            refresh();
            if (attempts > 20 || document.getElementById(INLINE_BTN_ID)) {
                clearInterval(retryTimer);
                retryTimer = null;
            }
        }, 400);
    }

    // Surveille les changements d'URL (Spotify est une SPA)
    let lastHref = location.href;
    const observer = new MutationObserver(() => {
        if (location.href !== lastHref) {
            lastHref = location.href;
            removeAll();
            startRetry();
        } else if (detectPage() && !document.getElementById(INLINE_BTN_ID) && !document.getElementById(FLOAT_BTN_ID)) {
            // Si l'action bar se reconstruit (changement d'onglet interne),
            // on remet le bouton.
            refresh();
        }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });

    // Lancement initial
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", startRetry, { once: true });
    } else {
        startRetry();
    }
})();
