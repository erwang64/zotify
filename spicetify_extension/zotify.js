// Zotify Bridge — extension Spicetify pour l'app desktop Spotify.
//
// Ajoute :
//   1. Un item "Telecharger via Zotify" dans le menu contextuel
//      (clic droit) des tracks, albums et playlists.
//   2. Un raccourci clavier Ctrl+Shift+D qui telecharge le track
//      en cours de lecture.
//
// Le GUI Zotify doit etre lance (il expose un serveur HTTP local).

(async function ZotifyBridge() {
    "use strict";

    // Attend que les API Spicetify soient pretes.
    while (!Spicetify?.ContextMenu || !Spicetify?.URI || !Spicetify?.Player) {
        await new Promise((r) => setTimeout(r, 200));
    }

    const PORTS = [43219, 43220, 43221];
    const SUPPORTED_TYPES = new Set(["track", "album", "playlist"]);
    let cachedPort = null;

    function uriToUrl(uri) {
        const parsed = Spicetify.URI.fromString(uri);
        if (!parsed || !SUPPORTED_TYPES.has(parsed.type)) return null;
        const id = parsed.id;
        return `https://open.spotify.com/${parsed.type}/${id}`;
    }

    async function fetchWithTimeout(url, timeoutMs) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        try {
            return await fetch(url, { signal: controller.signal });
        } finally {
            clearTimeout(timer);
        }
    }

    async function discoverPort() {
        if (cachedPort) {
            try {
                const r = await fetchWithTimeout(`http://127.0.0.1:${cachedPort}/ping`, 1200);
                if (r.ok) return cachedPort;
            } catch (_) {}
            cachedPort = null;
        }
        for (const port of PORTS) {
            try {
                const r = await fetchWithTimeout(`http://127.0.0.1:${port}/ping`, 1200);
                if (r.ok) {
                    const data = await r.json();
                    if (data?.service === "zotify-gui") {
                        cachedPort = port;
                        return port;
                    }
                }
            } catch (_) {}
        }
        return null;
    }

    async function sendOne(url) {
        const port = await discoverPort();
        if (!port) return { ok: false, error: "Zotify GUI non lance" };
        try {
            const target = `http://127.0.0.1:${port}/download?url=${encodeURIComponent(url)}`;
            const r = await fetchWithTimeout(target, 4000);
            const data = await r.json().catch(() => ({}));
            return r.ok ? { ok: true } : { ok: false, error: data?.error || `HTTP ${r.status}` };
        } catch (exc) {
            return { ok: false, error: exc?.message || String(exc) };
        }
    }

    async function downloadUris(uris) {
        let ok = 0;
        let fail = 0;
        let lastErr = "";
        for (const uri of uris) {
            const url = uriToUrl(uri);
            if (!url) { fail++; continue; }
            const res = await sendOne(url);
            if (res.ok) ok++;
            else { fail++; lastErr = res.error || lastErr; }
        }
        if (fail === 0) {
            Spicetify.showNotification(`Zotify : ${ok} envoi${ok > 1 ? "s" : ""} OK`);
        } else if (ok === 0) {
            Spicetify.showNotification(`Zotify : echec (${lastErr})`, true);
        } else {
            Spicetify.showNotification(`Zotify : ${ok} OK / ${fail} echec (${lastErr})`);
        }
    }

    function shouldShow(uris) {
        if (!uris || uris.length === 0) return false;
        return uris.every((uri) => {
            const p = Spicetify.URI.fromString(uri);
            return p && SUPPORTED_TYPES.has(p.type);
        });
    }

    // -- Item du menu contextuel (clic droit) --------------------------------
    new Spicetify.ContextMenu.Item(
        "Telecharger via Zotify",
        downloadUris,
        shouldShow,
        "download"  // icone Spicetify integre
    ).register();

    // -- Raccourci clavier : Ctrl+Shift+D pour le morceau en cours -----------
    Spicetify.Keyboard.registerShortcut(
        {
            key: "d",
            ctrl: true,
            shift: true,
            alt: false,
            meta: false,
        },
        async () => {
            const data = Spicetify.Player.data;
            const uri = data?.item?.uri || data?.track?.uri;
            if (!uri) {
                Spicetify.showNotification("Aucun morceau en lecture.", true);
                return;
            }
            await downloadUris([uri]);
        }
    );

    console.log("[Zotify Bridge] Extension Spicetify chargee.");
})();
