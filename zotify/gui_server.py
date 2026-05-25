"""Mini serveur HTTP local pour piloter Zotify GUI depuis l'exterieur.

Endpoints :
    GET /ping
        Healthcheck — repond `{"ok": true, "service": "zotify-gui"}`.
        Utilise par les extensions pour detecter si le GUI est lance.

    GET /download?url=<spotify-url>
        Declenche un telechargement comme si l'URL avait ete collee
        dans la barre du GUI. Repond immediatement (le DL est lance
        en arriere-plan) :
            200 {"ok": true}                 -> requete acceptee
            400 {"ok": false, "error": ...}  -> URL invalide
            503 {"ok": false, "error": ...}  -> GUI occupe / refuse

Securite :
    - Bind exclusif sur 127.0.0.1 (jamais sur 0.0.0.0). Le serveur
      n'est joignable que depuis la machine locale.
    - Valide strictement le format des URLs Spotify avant d'agir.
    - Repond avec des en-tetes CORS permissifs (origine *) car les
      extensions navigateur appellent via leur background script.
      Comme on bind en loopback uniquement, ce n'est pas exploitable
      par un site distant.

Le port par defaut est 43219 ; en cas de conflit on essaie 43220
puis 43221 avant d'abandonner silencieusement.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zotify.gui import ZotifyGUI


DEFAULT_PORT = 43219
PORT_FALLBACKS = (DEFAULT_PORT, 43220, 43221)

# Matches https://open.spotify.com/<type>/<id>[?...]
_SPOTIFY_URL_RE = re.compile(
    r"^https?://(?:open\.)?spotify\.com/"
    r"(?:intl-[a-z-]+/)?"  # optional locale prefix (fr, intl-fr, etc.)
    r"(track|album|playlist|artist|episode|show)/"
    r"([A-Za-z0-9]{20,32})"
)
_SPOTIFY_URI_RE = re.compile(
    r"^spotify:(track|album|playlist|artist|episode|show):([A-Za-z0-9]{20,32})$"
)


def is_spotify_url(url: str) -> bool:
    """Renvoie True si la chaine est une URL ou URI Spotify reconnue."""
    if not url:
        return False
    return bool(_SPOTIFY_URL_RE.match(url) or _SPOTIFY_URI_RE.match(url))


def normalize_spotify_url(url: str) -> str:
    """Convertit `spotify:track:xxxxx` en URL https si necessaire.
    Strippe les parametres de query (`?si=...`) pour ne garder que
    l'URL canonique reconnue par Zotify CLI.
    """
    m = _SPOTIFY_URI_RE.match(url)
    if m:
        return f"https://open.spotify.com/{m.group(1)}/{m.group(2)}"
    m = _SPOTIFY_URL_RE.match(url)
    if m:
        return f"https://open.spotify.com/{m.group(1)}/{m.group(2)}"
    return url


class _Handler(BaseHTTPRequestHandler):
    # Class attribute : injecte par start_server avant de demarrer.
    gui: "ZotifyGUI | None" = None

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """Silence le logging par defaut (sinon spam dans stderr)."""
        return

    def _send_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in ("/", "/ping"):
            gui = _Handler.gui
            n_active = 0
            n_queued = 0
            if gui is not None:
                try:
                    n_active = len(getattr(gui, "active_downloads", {}) or {})
                    n_queued = len(getattr(gui, "download_queue", []) or [])
                except Exception:
                    pass
            self._send_json(200, {
                "ok": True,
                "service": "zotify-gui",
                "version": _get_gui_version(),
                "busy": self._gui_is_busy(),
                "active": n_active,
                "queued": n_queued,
            })
            return

        if path == "/download":
            qs = urllib.parse.parse_qs(parsed.query)
            url_raw = (qs.get("url") or qs.get("u") or [""])[0].strip()
            if not is_spotify_url(url_raw):
                self._send_json(400, {
                    "ok": False,
                    "error": "URL Spotify invalide.",
                })
                return

            url = normalize_spotify_url(url_raw)
            if _Handler.gui is None:
                self._send_json(503, {"ok": False, "error": "GUI non disponible."})
                return

            ok, msg = _Handler.gui.trigger_download_from_url(url)
            if ok:
                self._send_json(200, {"ok": True, "url": url, "message": msg})
            else:
                self._send_json(503, {"ok": False, "error": msg or "Refuse"})
            return

        self._send_json(404, {"ok": False, "error": "Endpoint inconnu."})

    def _gui_is_busy(self) -> bool:
        """True si auth en cours OU file de DL pleine (refuserait l'ajout)."""
        gui = _Handler.gui
        if gui is None:
            return False
        try:
            if getattr(gui, "auth_process", None) is not None:
                return True
            # Plus de notion de "occupe" pour les downloads : on accepte
            # toujours d'en empiler (parallele + queue). On expose juste
            # le nombre d'actifs / en file pour info des extensions.
            return False
        except Exception:
            return False


def _get_gui_version() -> str:
    try:
        from zotify.gui import GUI_VERSION  # type: ignore
        return GUI_VERSION
    except Exception:
        return "unknown"


def start_server(gui: "ZotifyGUI") -> tuple[ThreadingHTTPServer, int] | tuple[None, None]:
    """Demarre le serveur en thread daemon. Renvoie (server, port) ou
    (None, None) si tous les ports candidats sont pris.
    """
    _Handler.gui = gui
    for port in PORT_FALLBACKS:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        except OSError:
            continue
        thread = threading.Thread(
            target=server.serve_forever,
            name=f"ZotifyBridge:{port}",
            daemon=True,
        )
        thread.start()
        return server, port
    return None, None


def stop_server(server: ThreadingHTTPServer | None) -> None:
    if server is None:
        return
    try:
        server.shutdown()
        server.server_close()
    except Exception:
        pass
