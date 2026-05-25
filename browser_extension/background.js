// Service worker : centralise les appels HTTP vers le GUI Zotify local.
// On essaie plusieurs ports car le GUI peut basculer sur 43220/43221
// si 43219 est deja pris.

const ZOTIFY_PORTS = [43219, 43220, 43221];
const REQUEST_TIMEOUT_MS = 4000;

async function fetchWithTimeout(url, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const resp = await fetch(url, { signal: controller.signal });
        return resp;
    } finally {
        clearTimeout(timer);
    }
}

async function discoverGuiPort() {
    const info = await discoverGuiInfo();
    return info ? info.port : null;
}

async function discoverGuiInfo() {
    for (const port of ZOTIFY_PORTS) {
        try {
            const resp = await fetchWithTimeout(`http://127.0.0.1:${port}/ping`, 1500);
            if (resp.ok) {
                const data = await resp.json();
                if (data && data.service === "zotify-gui") {
                    return { port, active: data.active || 0, queued: data.queued || 0 };
                }
            }
        } catch (_) { /* try next */ }
    }
    return null;
}

async function sendDownload(url) {
    const port = await discoverGuiPort();
    if (!port) {
        return { ok: false, error: "Zotify GUI introuvable. Lance le GUI puis reessaie." };
    }
    try {
        const target = `http://127.0.0.1:${port}/download?url=${encodeURIComponent(url)}`;
        const resp = await fetchWithTimeout(target, REQUEST_TIMEOUT_MS);
        const data = await resp.json().catch(() => ({}));
        if (resp.ok) {
            return { ok: true, message: data.message || "OK" };
        }
        return { ok: false, error: data.error || `HTTP ${resp.status}` };
    } catch (exc) {
        return { ok: false, error: `Erreur reseau : ${exc.message || exc}` };
    }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "zotify-download" && typeof msg.url === "string") {
        sendDownload(msg.url).then(sendResponse);
        return true;  // keep channel open for async response
    }
    if (msg && msg.type === "zotify-ping") {
        discoverGuiInfo().then((info) => {
            sendResponse(info ? { ok: true, ...info } : { ok: false });
        });
        return true;
    }
});
