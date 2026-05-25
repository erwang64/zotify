const statusEl = document.getElementById("status");

chrome.runtime.sendMessage({ type: "zotify-ping" }, (response) => {
    if (response && response.ok) {
        statusEl.textContent = `Connecte (port ${response.port})`;
        statusEl.classList.remove("err");
        statusEl.classList.add("ok");
    } else {
        statusEl.textContent = "GUI ferme";
        statusEl.classList.remove("ok");
        statusEl.classList.add("err");
    }
});
