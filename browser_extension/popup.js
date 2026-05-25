const statusEl = document.getElementById("status");

async function refresh() {
    try {
        const response = await new Promise((resolve) => {
            chrome.runtime.sendMessage({ type: "zotify-ping" }, resolve);
        });
        if (response && response.ok) {
            const parts = [`Connecte (port ${response.port})`];
            if (response.active > 0 || response.queued > 0) {
                parts.push(`${response.active} en cours, ${response.queued} en file`);
            }
            statusEl.textContent = parts.join(" - ");
            statusEl.classList.remove("err");
            statusEl.classList.add("ok");
        } else {
            statusEl.textContent = "GUI ferme";
            statusEl.classList.remove("ok");
            statusEl.classList.add("err");
        }
    } catch (e) {
        statusEl.textContent = "Erreur de communication";
        statusEl.classList.add("err");
    }
}

refresh();
setInterval(refresh, 1500);

