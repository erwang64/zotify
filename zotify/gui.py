from __future__ import annotations

import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from collections import deque
from queue import Empty, Queue
from typing import Final
import math
from io import BytesIO
import base64

try:
    from mutagen.oggvorbis import OggVorbis
    from mutagen.flac import Picture
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

import customtkinter as ctk
from PIL import Image
from tkinter import filedialog, Menu


WINDOW_WIDTH: Final[int] = 1024
WINDOW_HEIGHT: Final[int] = 700
GUI_VERSION: Final[str] = "1.2.1"
# Nombre max de telechargements lances en parallele. Au-dela, les
# demandes supplementaires sont mises en file et demarrees au fur
# et a mesure que les workers se liberent. 3 est un bon compromis
# (Spotify rate-limite assez vite si on monte plus haut).
MAX_PARALLEL_DOWNLOADS: Final[int] = 3
# Clés config.json obsolètes (ignorées par Zotify, génèrent des avertissements au lancement).
GUI_DEPRECATED_CONFIG_KEYS: Final[tuple[str, ...]] = (
    "SONG_ARCHIVE",
    "DOWNLOAD_LYRICS",
    "OVERRIDE_AUTO_WAIT",
    "DOWNLOAD_REAL_TIME",
)


class ZotifyGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        self.title("Zotify GUI")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(900, 620)
        self._center_window(WINDOW_WIDTH, WINDOW_HEIGHT)

        self.settings_path = self._resolve_settings_path()
        self.gui_settings = self._load_gui_settings()

        # Auth subprocess (login/logout) : strictement exclusif.
        self.auth_process: subprocess.Popen | None = None
        # Downloads paralleles : jusqu'a MAX_PARALLEL_DOWNLOADS actifs
        # simultanement, le reste est mis en file dans download_queue.
        self.active_downloads: dict[int, subprocess.Popen] = {}
        self.download_queue: deque[tuple[str, list[str]]] = deque()
        self.next_dl_id: int = 0
        self._dl_lock = threading.Lock()
        # Compteur global pour declencher le batch convert UNE fois
        # quand toute la batch (actifs + file) est terminee.
        self.last_dl_exit_codes: dict[int, int] = {}
        self.output_queue: Queue[str] = Queue()
        self.current_page = "Accueil"
        self.pending_oauth_url: str | None = None
        self.oauth_url_opened = False
        self.login_flow_active = False
        self.login_success_detected = False
        self.last_console_line = ""
        self.current_action = "idle"
        self.current_mode = ""
        self.last_process_exit_code: int | None = None
        self.last_downloaded_path: Path | None = None
        self.all_downloaded_paths: list[Path] = []
        self.last_download_metadata: dict[str, str] = {}
        # Liste de TOUS les titres telecharges dans la batch courante
        # (alimentee par les lignes "Track Name ==" du sous-process Zotify).
        # Sert a peupler la page Success en mode multi-DL parallele.
        self.batch_track_titles: list[str] = []
        self.batch_track_artists: list[str] = []
        self.batch_convert_stats: dict[str, int] = {"total": 0, "converted": 0, "failed": 0}
        self.current_cover_image: ctk.CTkImage | None = None
        self.dl_progress_current: int = 0
        self.dl_progress_total: int = 0
        self.failed_tracks: list[tuple[str, str]] = []
        self.conv_progress_current: int = 0
        self.conv_progress_total: int = 0
        self._batch_convert_lock = threading.Lock()

        self.configure(fg_color="#121212")
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_navbar()
        self._build_pages()
        self.after(100, self._drain_output_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Demarre le mini serveur HTTP qui permet aux extensions
        # (navigateur Spotify Web Player + Spicetify desktop)
        # d'envoyer des URLs directement au GUI.
        self.bridge_server = None
        self.bridge_port: int | None = None
        try:
            from zotify.gui_server import start_server
            self.bridge_server, self.bridge_port = start_server(self)
            if self.bridge_port:
                self._append_console(
                    f"[Bridge] Serveur local actif sur http://127.0.0.1:{self.bridge_port}\n"
                    f"[Bridge] Installe l'extension navigateur ou Spicetify pour\n"
                    f"[Bridge] declencher les telechargements depuis Spotify.\n"
                )
        except Exception as exc:
            self._append_console(f"[Bridge] Echec demarrage serveur local : {exc}\n")

    def _build_navbar(self) -> None:
        nav = ctk.CTkFrame(self, width=240, fg_color="#000000", corner_radius=0)
        nav.grid(row=0, column=0, sticky="nsew")
        nav.grid_propagate(False)
        nav.grid_columnconfigure(0, weight=1)

        logo_path = self._resolve_resource_path("logo", "logo.png")
        if logo_path.exists():
            pil_img = Image.open(logo_path)
            self.logo_image = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(160, 52))
            logo_label = ctk.CTkLabel(nav, image=self.logo_image, text="")
            logo_label.grid(row=0, column=0, padx=24, pady=(32, 32), sticky="w")
        else:
            title = ctk.CTkLabel(nav, text="Zotify", font=ctk.CTkFont(size=32, weight="bold"), text_color="#1DB954")
            title.grid(row=0, column=0, padx=24, pady=(32, 32), sticky="w")

        btn_kwargs = {
            "corner_radius": 4, "anchor": "w", "font": ctk.CTkFont(size=15, weight="bold"),
            "height": 40, "hover_color": "#282828", "fg_color": "transparent", "text_color": "#B3B3B3"
        }
        self.home_nav_btn = ctk.CTkButton(nav, text="Accueil", command=lambda: self.show_page("Accueil"), **btn_kwargs)
        self.home_nav_btn.grid(row=1, column=0, padx=12, pady=4, sticky="ew")

        self.download_nav_btn = ctk.CTkButton(nav, text="Téléchargement", command=lambda: self.show_page("Download"), **btn_kwargs)
        self.download_nav_btn.grid(row=2, column=0, padx=12, pady=4, sticky="ew")

        self.settings_nav_btn = ctk.CTkButton(nav, text="Paramètres", command=lambda: self.show_page("Settings"), **btn_kwargs)
        self.settings_nav_btn.grid(row=3, column=0, padx=12, pady=4, sticky="ew")

        nav.grid_rowconfigure(4, weight=1)

        auth_frame = ctk.CTkFrame(nav, fg_color="#181818", corner_radius=8)
        auth_frame.grid(row=5, column=0, padx=12, pady=24, sticky="ew")
        auth_frame.grid_columnconfigure(0, weight=1)
        
        self.nav_auth_status = ctk.CTkLabel(auth_frame, text="Spotify: déconnecté", text_color="#B3B3B3", font=ctk.CTkFont(size=12, weight="bold"))
        self.nav_auth_status.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")

        self.nav_auth_button = ctk.CTkButton(auth_frame, text="Se connecter", command=self.toggle_spotify_auth,
                                             fg_color="transparent", border_width=1, border_color="#B3B3B3", 
                                             text_color="#FFFFFF", hover_color="#282828", corner_radius=20, height=32,
                                             font=ctk.CTkFont(weight="bold"))
        self.nav_auth_button.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")

        version_label = ctk.CTkLabel(
            nav,
            text=f"Version {GUI_VERSION}",
            text_color="#7A7A7A",
            font=ctk.CTkFont(size=12),
        )
        version_label.grid(row=6, column=0, padx=16, pady=(0, 14), sticky="w")

        self._sync_nav_style()

    def _build_pages(self) -> None:
        self.pages_container = ctk.CTkFrame(self, fg_color="transparent")
        self.pages_container.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.pages_container.grid_columnconfigure(0, weight=1)
        self.pages_container.grid_rowconfigure(0, weight=1)

        self.home_page = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        self.home_page.grid(row=0, column=0, sticky="nsew")
        self.home_page.grid_columnconfigure(0, weight=1)
        self.home_page.grid_rowconfigure(0, weight=1)

        self.download_page = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        self.download_page.grid(row=0, column=0, sticky="nsew")
        self.download_page.grid_columnconfigure(0, weight=1)
        self.download_page.grid_rowconfigure(0, weight=0)
        self.download_page.grid_rowconfigure(1, weight=1)

        self.settings_page = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        self.settings_page.grid(row=0, column=0, sticky="nsew")
        self.settings_page.grid_columnconfigure(0, weight=1)
        self.settings_page.grid_rowconfigure(0, weight=1)
        
        self.success_page = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        self.success_page.grid(row=0, column=0, sticky="nsew")
        self.success_page.grid_columnconfigure(0, weight=1)
        self.success_page.grid_rowconfigure(0, weight=1)
        
        self._build_home_page(self.home_page)
        self._build_settings_page(self.settings_page)
        self._build_success_page(self.success_page)
        self._build_top_controls(self.download_page)
        self._build_output_panel(self.download_page)

        self.show_page("Accueil")

    def _build_home_page(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(parent, fg_color="#181818", corner_radius=12)
        card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.8, relheight=0.8)

        title = ctk.CTkLabel(card, text="Bienvenue sur Zotify", font=ctk.CTkFont(size=36, weight="bold"), text_color="#1DB954")
        title.pack(pady=(60, 20))

        info_text = (
            "Zotify est un outil puissant pour télécharger ta musique depuis Spotify.\n\n"
            "Commence par lier ton compte dans les Paramètres si ce n'est pas déjà fait,\n"
            "puis rends-toi dans l'onglet Téléchargement pour entrer tes liens.\n\n"
            "Bonne écoute !"
        )
        desc = ctk.CTkLabel(card, text=info_text, font=ctk.CTkFont(size=16), text_color="#FFFFFF", justify="center")
        desc.pack(pady=20)

        start_btn = ctk.CTkButton(card, text="Aller au téléchargement", command=lambda: self.show_page("Download"), fg_color="#1DB954", text_color="#000000", hover_color="#1ED760", font=ctk.CTkFont(weight="bold", size=15), height=44, corner_radius=22)
        start_btn.pack(pady=40)

    def _build_settings_page(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkScrollableFrame(parent, fg_color="#181818", corner_radius=12)
        card.grid(row=0, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(card, text="Paramètres", font=ctk.CTkFont(size=32, weight="bold"), text_color="#FFFFFF")
        title.grid(row=0, column=0, padx=32, pady=(32, 8), sticky="w")
        
        subtitle = ctk.CTkLabel(
            card,
            text="Configure les dossiers, le client API, l'authentification et le format de conversion.",
            text_color="#B3B3B3",
        )
        subtitle.grid(row=1, column=0, padx=32, pady=(0, 32), sticky="w")

        row_idx = 2
        def add_entry(label_text, placeholder="", has_browse=False, browse_cmd=None, is_password=False):
            nonlocal row_idx
            ctk.CTkLabel(card, text=label_text, font=ctk.CTkFont(weight="bold", size=14), text_color="#FFFFFF").grid(row=row_idx, column=0, padx=32, pady=(16, 8), sticky="w")
            row_idx += 1
            row_frame = ctk.CTkFrame(card, fg_color="transparent")
            row_frame.grid(row=row_idx, column=0, padx=32, pady=(0, 8), sticky="ew")
            row_frame.grid_columnconfigure(0, weight=1)
            row_idx += 1
            entry = ctk.CTkEntry(row_frame, placeholder_text=placeholder, fg_color="#282828", border_width=0, height=40, show="*" if is_password else "")
            entry.grid(row=0, column=0, sticky="ew", padx=(0, 16 if has_browse else 0))
            if has_browse:
                ctk.CTkButton(row_frame, text="Parcourir", command=browse_cmd, fg_color="transparent", hover_color="#3E3E3E", border_width=1, border_color="#B3B3B3", text_color="#FFFFFF", width=100, height=40).grid(row=0, column=1)
            return entry

        self.download_dir_entry = add_entry("Dossier Musique (ROOT_PATH)", "Ex: D:/Music/Zotify", True, lambda: self._browse_directory(self.download_dir_entry))
        self.download_dir_entry.insert(0, self.gui_settings.get("download_dir", ""))

        self.podcast_dir_entry = add_entry("Dossier Podcasts (ROOT_PODCAST_PATH)", "Ex: D:/Music/Zotify Podcasts", True, lambda: self._browse_directory(self.podcast_dir_entry))
        self.podcast_dir_entry.insert(0, self.gui_settings.get("podcast_dir", ""))

        self.default_config_entry = add_entry("Fichier de Configuration JSON", "Dossier ou fichier config.json", True, self._browse_default_config)
        self.default_config_entry.insert(0, self.gui_settings.get("default_config_path", ""))

        self.client_id_entry = add_entry("Client ID API Spotify", "Laisser vide pour le client interne")
        self.client_id_entry.insert(0, self.gui_settings.get("api_client_id", ""))

        saved_fmt = str(self.gui_settings.get("conversion_format", "wav")).strip().lower()
        ctk.CTkLabel(
            card,
            text="Format de conversion",
            font=ctk.CTkFont(weight="bold", size=14),
            text_color="#FFFFFF",
        ).grid(row=row_idx, column=0, padx=32, pady=(16, 8), sticky="w")
        row_idx += 1
        self.conversion_format_var = ctk.StringVar(value="MP3" if saved_fmt == "mp3" else "WAV")
        self.conversion_format_menu = ctk.CTkOptionMenu(
            card,
            values=["WAV", "MP3"],
            variable=self.conversion_format_var,
            fg_color="#282828",
            button_color="#3E3E3E",
            button_hover_color="#535353",
            dropdown_fg_color="#282828",
            height=40,
            width=200,
        )
        self.conversion_format_menu.grid(row=row_idx, column=0, padx=32, pady=(0, 4), sticky="w")
        row_idx += 1
        ctk.CTkLabel(
            card,
            text="WAV : sans perte (PCM 16 bits)  |  MP3 : 320 kbps (très haute qualité)",
            text_color="#B3B3B3",
            font=ctk.CTkFont(size=12),
        ).grid(row=row_idx, column=0, padx=32, pady=(0, 8), sticky="w")
        row_idx += 1

        ctk.CTkLabel(
            card,
            text="Options obsolètes (config.json)",
            font=ctk.CTkFont(weight="bold", size=14),
            text_color="#FFFFFF",
        ).grid(row=row_idx, column=0, padx=32, pady=(16, 8), sticky="w")
        row_idx += 1
        ctk.CTkLabel(
            card,
            text=(
                "Ces entrées sont ignorées par Zotify et provoquent des avertissements. "
                "Coche celles à retirer du config.json, puis sauvegarde ou clique sur Nettoyer."
            ),
            text_color="#B3B3B3",
            font=ctk.CTkFont(size=12),
            justify="left",
            wraplength=700,
        ).grid(row=row_idx, column=0, padx=32, pady=(0, 8), sticky="w")
        row_idx += 1

        present_deprecated = self._find_deprecated_keys_in_config()
        saved_cleanup = self.gui_settings.get("deprecated_cleanup", {})
        if not isinstance(saved_cleanup, dict):
            saved_cleanup = {}
        deprec_cb_kwargs = {
            "border_color": "#535353",
            "hover_color": "#1ED760",
            "checkmark_color": "#000000",
            "font": ctk.CTkFont(size=13),
        }
        self.deprec_key_vars: dict[str, ctk.BooleanVar] = {}
        self.deprec_key_labels: dict[str, ctk.CTkLabel] = {}
        for key in GUI_DEPRECATED_CONFIG_KEYS:
            if key in saved_cleanup:
                default_checked = bool(saved_cleanup[key])
            else:
                default_checked = key in present_deprecated
            var = ctk.BooleanVar(value=default_checked)
            self.deprec_key_vars[key] = var
            row_frame = ctk.CTkFrame(card, fg_color="transparent")
            row_frame.grid(row=row_idx, column=0, padx=32, pady=2, sticky="w")
            row_idx += 1
            ctk.CTkCheckBox(
                row_frame,
                text=f"Supprimer « {key} »",
                variable=var,
                **deprec_cb_kwargs,
            ).grid(row=0, column=0, sticky="w")
            status = "présente" if key in present_deprecated else "absente"
            status_color = "#E8A838" if key in present_deprecated else "#6B6B6B"
            status_lbl = ctk.CTkLabel(
                row_frame,
                text=f"({status} dans config.json)",
                text_color=status_color,
                font=ctk.CTkFont(size=11),
            )
            status_lbl.grid(row=0, column=1, padx=(12, 0), sticky="w")
            self.deprec_key_labels[key] = status_lbl

        cleanup_btn = ctk.CTkButton(
            card,
            text="Nettoyer le config.json",
            command=self._apply_deprecated_config_cleanup,
            fg_color="transparent",
            hover_color="#3E3E3E",
            border_width=1,
            border_color="#B3B3B3",
            text_color="#FFFFFF",
            height=36,
            corner_radius=18,
        )
        cleanup_btn.grid(row=row_idx, column=0, padx=32, pady=(8, 8), sticky="w")
        row_idx += 1

        self.settings_info = ctk.CTkLabel(card, text="", text_color="#B3B3B3")
        self.settings_info.grid(row=row_idx, column=0, padx=32, pady=(24, 8), sticky="w")
        row_idx += 1
        
        save_btn = ctk.CTkButton(card, text="Sauvegarder", command=self.save_settings, fg_color="#FFFFFF", text_color="#000000", hover_color="#B3B3B3", font=ctk.CTkFont(weight="bold", size=15), height=48, corner_radius=24, width=200)
        save_btn.grid(row=row_idx, column=0, padx=32, pady=(0, 32), sticky="w")

    def _center_window(self, width: int, height: int) -> None:
        self.update_idletasks()
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_success_page(self, parent: ctk.CTkFrame) -> None:
        self.success_card = ctk.CTkFrame(parent, fg_color="#181818", corner_radius=12)
        self.success_card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.8, relheight=0.8)
        self.success_card.grid_columnconfigure(0, weight=1)
        self.success_card.grid_rowconfigure(1, weight=1)

        self.success_header = ctk.CTkFrame(self.success_card, fg_color="transparent")
        self.success_header.grid(row=0, column=0, pady=(40, 20))
        self.success_header.grid_columnconfigure(0, weight=1)
        
        self.anim_canvas = ctk.CTkCanvas(self.success_header, width=100, height=100, bg="#181818", highlightthickness=0)
        self.anim_canvas.grid(row=0, column=0, pady=(0, 20))
        
        self.success_title = ctk.CTkLabel(self.success_header, text="Téléchargement \u0026 Conversion Terminés", font=ctk.CTkFont(size=24, weight="bold"), text_color="#1DB954")
        self.success_title.grid(row=1, column=0)

        self.success_subtitle = ctk.CTkLabel(self.success_header, text="", font=ctk.CTkFont(size=14), text_color="#B3B3B3")
        self.success_subtitle.grid(row=2, column=0, pady=(4, 0))

        self.track_info_frame = ctk.CTkFrame(self.success_card, fg_color="#282828", corner_radius=8)
        self.track_info_frame.grid(row=1, column=0, padx=40, pady=20, sticky="nsew")
        self.track_info_frame.grid_columnconfigure(1, weight=1)
        self.track_info_frame.grid_rowconfigure(0, weight=1)

        self.cover_label = ctk.CTkLabel(self.track_info_frame, text="", width=150, height=150, fg_color="#121212", corner_radius=8)
        self.cover_label.grid(row=0, column=0, padx=20, pady=20)

        info_inner = ctk.CTkFrame(self.track_info_frame, fg_color="transparent")
        info_inner.grid(row=0, column=1, padx=(0, 20), pady=20, sticky="w")

        self.success_track_title = ctk.CTkLabel(info_inner, text="Titre", font=ctk.CTkFont(size=20, weight="bold"), text_color="#FFFFFF", anchor="w", justify="left")
        self.success_track_title.pack(anchor="w", pady=(0, 5))
        self.success_track_artist = ctk.CTkLabel(info_inner, text="Artiste", font=ctk.CTkFont(size=16), text_color="#B3B3B3", anchor="w", justify="left")
        self.success_track_artist.pack(anchor="w", pady=(0, 5))
        self.success_track_album = ctk.CTkLabel(info_inner, text="Album", font=ctk.CTkFont(size=14), text_color="#B3B3B3", anchor="w", justify="left")
        self.success_track_album.pack(anchor="w")

        self.failed_frame = ctk.CTkFrame(self.success_card, fg_color="#2A1F1F", corner_radius=8, border_width=1, border_color="#E22134")
        self.failed_frame.grid(row=2, column=0, padx=40, pady=(0, 10), sticky="ew")
        self.failed_frame.grid_columnconfigure(0, weight=1)
        self.failed_header = ctk.CTkLabel(
            self.failed_frame,
            text="",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#FF6B6B",
            anchor="w",
            justify="left",
        )
        self.failed_header.grid(row=0, column=0, padx=14, pady=(10, 4), sticky="ew")
        self.failed_list_label = ctk.CTkLabel(
            self.failed_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#E8B4B4",
            anchor="w",
            justify="left",
        )
        self.failed_list_label.grid(row=1, column=0, padx=14, pady=(0, 10), sticky="ew")
        self.failed_frame.grid_remove()

        buttons_frame = ctk.CTkFrame(self.success_card, fg_color="transparent")
        buttons_frame.grid(row=3, column=0, pady=(0, 40))

        self.open_folder_btn = ctk.CTkButton(buttons_frame, text="Ouvrir le dossier", command=self._open_download_folder, fg_color="transparent", border_width=1, border_color="#B3B3B3", text_color="#FFFFFF", hover_color="#3E3E3E", font=ctk.CTkFont(weight="bold", size=15), height=40, corner_radius=20)
        self.open_folder_btn.grid(row=0, column=0, padx=10)

        self.new_download_btn = ctk.CTkButton(buttons_frame, text="Nouveau téléchargement", command=lambda: self.show_page("Download"), fg_color="#1DB954", text_color="#000000", hover_color="#1ED760", font=ctk.CTkFont(weight="bold", size=15), height=40, corner_radius=20)
        self.new_download_btn.grid(row=0, column=1, padx=10)

    def _open_download_folder(self) -> None:
        directory = self._resolve_download_directory_to_open()
        if directory is None:
            self._append_console("Impossible d'ouvrir un dossier: aucun chemin valide trouve.\n")
            return

        self._append_console(f"Tentative d'ouverture du dossier: {directory}\n")
        try:
            self._open_path_in_file_manager(directory)
            self._append_console("Dossier ouvert avec succes.\n")
        except Exception as exc:
            self._append_console(f"Erreur d'ouverture du dossier: {exc}\n")

    def _resolve_download_directory_to_open(self) -> Path | None:
        # Priorite: dossier du dernier telechargement, puis dossier configure.
        candidates: list[Path] = []

        if self.last_downloaded_path:
            resolved = self.last_downloaded_path.expanduser()
            if not resolved.is_absolute():
                root_raw = self.download_dir_entry.get().strip()
                if root_raw:
                    resolved = Path(root_raw).expanduser() / resolved
                else:
                    resolved = Path.cwd() / resolved
            candidates.append(resolved)

        root_raw = self.download_dir_entry.get().strip()
        if root_raw:
            candidates.append(Path(root_raw).expanduser())

        for candidate in candidates:
            if candidate.exists():
                return candidate if candidate.is_dir() else candidate.parent
            if candidate.parent.exists():
                return candidate.parent

        return None

    def _open_path_in_file_manager(self, path: Path) -> None:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _format_track_titles_preview(self, titles: list[str], max_visible: int = 3) -> str:
        """Resume une liste de titres pour affichage compact dans la page Success."""
        if not titles:
            return ""
        visible = titles[:max_visible]
        remaining = len(titles) - len(visible)
        text = ", ".join(visible)
        if remaining > 0:
            text += f"  +{remaining} autre{'s' if remaining > 1 else ''}"
        return text

    def _animate_success_page(self) -> None:
        self.anim_canvas.delete("all")

        # === Detection du type de batch =====================================
        # n_dl_processes : nb de subprocess Zotify qui ont tourne dans cette
        #                  batch (= nb d'URLs distinctes telechargees).
        # n_files        : nb total de fichiers ecrits sur disque.
        # n_titles       : nb de titres distincts captes via "Track Name ==".
        n_dl_processes = len(self.last_dl_exit_codes)
        n_dl_ok = sum(1 for code in self.last_dl_exit_codes.values() if code == 0)
        n_files = len(self.all_downloaded_paths)
        n_titles = len(self.batch_track_titles)
        n_dl_failed = len(self.failed_tracks)

        is_multi_dl     = n_dl_processes > 1
        is_playlist     = (not is_multi_dl) and (n_files > 1 or n_titles > 1
                                                 or self.batch_convert_stats["total"] > 1)
        # else : single track / single URL

        fmt_label = self._conversion_label()
        n_conv_total = self.batch_convert_stats["total"]
        n_conv_ok    = self.batch_convert_stats["converted"]
        n_conv_fail  = self.batch_convert_stats["failed"]

        # ----- CAS A : telechargements PARALLELES (N URLs distinctes) --------
        if is_multi_dl:
            self.success_title.configure(
                text=f"{n_dl_ok}/{n_dl_processes} téléchargements terminés"
            )
            subtitle_bits = []
            if n_titles:
                subtitle_bits.append(
                    f"{n_titles} morceau{'x' if n_titles > 1 else ''} en {fmt_label}"
                )
            elif n_conv_total:
                subtitle_bits.append(f"{n_conv_ok}/{n_conv_total} fichiers convertis en {fmt_label}")
            if n_conv_fail > 0:
                subtitle_bits.append(f"({n_conv_fail} échec{'s' if n_conv_fail > 1 else ''} ffmpeg)")
            if n_dl_failed > 0:
                subtitle_bits.append(
                    f"- {n_dl_failed} indispo{'s' if n_dl_failed > 1 else ''} sur Spotify"
                )
            self.success_subtitle.configure(text=" ".join(subtitle_bits))

            # Zone preview : on resume la batch en mode "X morceaux"
            preview_title = (
                f"{n_titles} morceau{'x' if n_titles > 1 else ''} téléchargé{'s' if n_titles > 1 else ''}"
                if n_titles else
                f"{n_dl_ok} téléchargement{'s' if n_dl_ok > 1 else ''}"
            )
            self.success_track_title.configure(text=preview_title)

            unique_artists = list(dict.fromkeys(self.batch_track_artists))
            if len(unique_artists) == 1:
                preview_artist = unique_artists[0]
            elif len(unique_artists) == 0:
                preview_artist = "Multi-artistes"
            elif len(unique_artists) <= 3:
                preview_artist = ", ".join(unique_artists)
            else:
                preview_artist = f"{', '.join(unique_artists[:3])} +{len(unique_artists) - 3} autres"
            self.success_track_artist.configure(text=preview_artist)

            # Affiche la liste des titres dans le champ "album" (truncated)
            self.success_track_album.configure(
                text=self._format_track_titles_preview(self.batch_track_titles)
            )

        # ----- CAS B : 1 URL contenant N morceaux (album / playlist) ---------
        elif is_playlist:
            n_total = max(n_conv_total, n_titles, n_files)
            self.success_title.configure(text="Playlist Téléchargée \u0026 Convertie")
            subtitle_parts = [f"{n_conv_ok}/{n_total} morceaux convertis en {fmt_label}"]
            if n_conv_fail > 0:
                subtitle_parts.append(f"({n_conv_fail} échec{'s' if n_conv_fail > 1 else ''} ffmpeg)")
            if n_dl_failed > 0:
                subtitle_parts.append(
                    f"- {n_dl_failed} indispo{'s' if n_dl_failed > 1 else ''} sur Spotify"
                )
            self.success_subtitle.configure(text=" ".join(subtitle_parts))
            self.success_track_title.configure(
                text=self.last_download_metadata.get("title", f"{n_total} morceaux")
            )
            self.success_track_artist.configure(text=self.last_download_metadata.get("artist", ""))
            self.success_track_album.configure(text=self.last_download_metadata.get("album", ""))

        # ----- CAS C : 1 morceau, 1 URL --------------------------------------
        else:
            self.success_title.configure(text="Téléchargement \u0026 Conversion Terminés")
            if n_dl_failed > 0:
                self.success_subtitle.configure(
                    text=f"{n_dl_failed} morceau{'x' if n_dl_failed > 1 else ''} indispo{'s' if n_dl_failed > 1 else ''} sur Spotify"
                )
            else:
                self.success_subtitle.configure(text="")
            self.success_track_title.configure(
                text=self.last_download_metadata.get("title", "Titre Inconnu")
            )
            self.success_track_artist.configure(
                text=self.last_download_metadata.get("artist", "Artiste Inconnu")
            )
            self.success_track_album.configure(
                text=self.last_download_metadata.get("album", "Album Inconnu")
            )
        
        if n_dl_failed > 0:
            self.failed_header.configure(
                text=f"{n_dl_failed} morceau{'x' if n_dl_failed > 1 else ''} non disponible{'s' if n_dl_failed > 1 else ''} sur Spotify :"
            )
            max_visible = 5
            visible = self.failed_tracks[:max_visible]
            lines = [f"  • {name}" for name, _uri in visible]
            remaining = n_dl_failed - len(visible)
            if remaining > 0:
                lines.append(f"  ... et {remaining} autre{'s' if remaining > 1 else ''}")
            self.failed_list_label.configure(text="\n".join(lines))
            self.failed_frame.grid()
        else:
            self.failed_frame.grid_remove()
        
        self.current_cover_image = None
        self.cover_label.configure(image=None, text="Pas de pochette")
        
        # Try to extract cover art from the last converted file (WAV/MP3)
        cover_source = None
        if self.last_downloaded_path and MUTAGEN_AVAILABLE:
            # Try converted file first (WAV or MP3), then fall back to OGG if it exists
            target_ext = self._conversion_ext()
            converted = self.last_downloaded_path.with_suffix(target_ext)
            if converted.exists():
                cover_source = converted
            else:
                # Fallback: try to find the OGG file
                for suffix in [".ogg", self.last_downloaded_path.suffix]:
                    candidate = self.last_downloaded_path.with_suffix(suffix)
                    if candidate.exists():
                        cover_source = candidate
                        break
        
        if cover_source:
            try:
                pil_img = self._extract_cover_from_audio(cover_source)
                if pil_img is not None:
                    self.current_cover_image = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(150, 150))
                    self.cover_label.configure(image=self.current_cover_image, text="")
            except Exception as e:
                self._append_console(f"Erreur extraction pochette: {e}\n")

        self._anim_angle = 0
        self._anim_check_progress = 0
        self._draw_circle_frame()

    def _draw_circle_frame(self) -> None:
        self.anim_canvas.delete("circle")
        size = 100
        margin = 6
        color = "#1DB954"
        
        self.anim_canvas.create_arc(
            margin, margin, size - margin, size - margin,
            start=90, extent=-self._anim_angle, style="arc", outline=color, width=6, tags="circle"
        )
        if self._anim_angle < 359:
            self._anim_angle += 15
            self.after(16, self._draw_circle_frame)
        else:
            self._anim_angle = 359
            self.anim_canvas.delete("circle")
            self.anim_canvas.create_arc(
                margin, margin, size - margin, size - margin,
                start=90, extent=-359.99, style="arc", outline=color, width=6, tags="circle"
            )
            self._draw_check_frame()

    def _draw_check_frame(self) -> None:
        color = "#1DB954"
        start_x, start_y = 28, 52
        mid_x, mid_y = 45, 68
        end_x, end_y = 72, 35
        
        seg1_len = math.hypot(mid_x - start_x, mid_y - start_y)
        seg2_len = math.hypot(end_x - mid_x, end_y - mid_y)
        total_len = seg1_len + seg2_len
        
        self.anim_canvas.delete("check")
        
        if self._anim_check_progress < seg1_len:
            ratio = self._anim_check_progress / seg1_len
            cur_x = start_x + (mid_x - start_x) * ratio
            cur_y = start_y + (mid_y - start_y) * ratio
            self.anim_canvas.create_line(start_x, start_y, cur_x, cur_y, fill=color, width=6, capstyle="round", joinstyle="round", tags="check")
        else:
            self.anim_canvas.create_line(start_x, start_y, mid_x, mid_y, fill=color, width=6, capstyle="round", joinstyle="round", tags="check")
            ratio = (self._anim_check_progress - seg1_len) / seg2_len
            if ratio > 1: ratio = 1
            cur_x = mid_x + (end_x - mid_x) * ratio
            cur_y = mid_y + (end_y - mid_y) * ratio
            if ratio > 0:
                self.anim_canvas.create_line(mid_x, mid_y, cur_x, cur_y, fill=color, width=6, capstyle="round", joinstyle="round", tags="check")
        
        if self._anim_check_progress < total_len:
            self._anim_check_progress += total_len / 15
            self.after(16, self._draw_check_frame)

    def _extract_cover_from_ogg(self, ogg_path: Path) -> Image.Image | None:
        audio = OggVorbis(ogg_path)

        def _to_image(value) -> Image.Image | None:
            raw: bytes
            try:
                raw = value if isinstance(value, bytes) else base64.b64decode(value)
            except Exception:
                return None

            # Cas standard OGG Vorbis: FLAC picture serialisee en base64.
            try:
                pic = Picture(raw)
                if pic.data:
                    img = Image.open(BytesIO(pic.data))
                    img.load()
                    return img
            except Exception:
                pass

            # Fallback: certains tags "coverart" contiennent directement l'image.
            try:
                img = Image.open(BytesIO(raw))
                img.load()
                return img
            except Exception:
                return None

        for key in ("metadata_block_picture", "coverart"):
            for value in audio.get(key, []):
                image = _to_image(value)
                if image is not None:
                    return image
        return None

    def _extract_cover_from_audio(self, audio_path: Path) -> Image.Image | None:
        """Extract cover art from any audio format (OGG, MP3, WAV)."""
        try:
            suffix = audio_path.suffix.lower()
            
            # Try using music_tag first (works for all formats)
            try:
                import music_tag
                audio = music_tag.load_file(str(audio_path))
                if audio['artwork'].value:
                    artwork_obj = audio['artwork'].value
                    if hasattr(artwork_obj, 'data'):
                        img = Image.open(BytesIO(artwork_obj.data))
                        img.load()
                        return img
            except Exception:
                pass
            
            # Fallback for OGG files
            if suffix == ".ogg":
                return self._extract_cover_from_ogg(audio_path)
            
            # Fallback for MP3 files using mutagen.id3
            if suffix == ".mp3":
                try:
                    from mutagen.id3 import ID3
                    id3 = ID3(str(audio_path))
                    for frame in id3.getall('APIC'):
                        if frame.data:
                            img = Image.open(BytesIO(frame.data))
                            img.load()
                            return img
                except Exception:
                    pass
            
            # Fallback for WAV files
            if suffix == ".wav":
                try:
                    from mutagen.wave import WAVE
                    wave = WAVE(str(audio_path))
                    for frame in wave.get('ID3', []):
                        for apic_frame in frame.getall('APIC'):
                            if apic_frame.data:
                                img = Image.open(BytesIO(apic_frame.data))
                                img.load()
                                return img
                except Exception:
                    pass
        except Exception:
            pass
        
        return None

    def show_page(self, page: str) -> None:
        self.current_page = page
        if page == "Accueil":
            self.home_page.tkraise()
        elif page == "Download":
            self.download_page.tkraise()
        elif page == "Success":
            self.success_page.tkraise()
            self._animate_success_page()
        else:
            self.settings_page.tkraise()
        self._sync_nav_style()

    def _sync_nav_style(self) -> None:
        selected_bg = "#282828"
        selected_text = "#FFFFFF"
        default_bg = "transparent"
        default_text = "#B3B3B3"
        
        self.home_nav_btn.configure(fg_color=default_bg, text_color=default_text)
        self.download_nav_btn.configure(fg_color=default_bg, text_color=default_text)
        self.settings_nav_btn.configure(fg_color=default_bg, text_color=default_text)

        if self.current_page == "Accueil":
            self.home_nav_btn.configure(fg_color=selected_bg, text_color=selected_text)
        elif self.current_page == "Download":
            self.download_nav_btn.configure(fg_color=selected_bg, text_color=selected_text)
        elif self.current_page == "Settings":
            self.settings_nav_btn.configure(fg_color=selected_bg, text_color=selected_text)

    def _build_top_controls(self, parent: ctk.CTkFrame) -> None:
        panel = ctk.CTkFrame(parent, fg_color="#181818", corner_radius=12)
        panel.grid(row=0, column=0, sticky="nsew", pady=(0, 20))
        panel.grid_columnconfigure(0, weight=1)

        # Split panel into two columns: Left for input, Right for advanced & actions
        content = ctk.CTkFrame(panel, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        content.grid_columnconfigure(0, weight=2)
        content.grid_columnconfigure(1, weight=1)

        # --- Left Column ---
        left_frame = ctk.CTkFrame(content, fg_color="transparent")
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left_frame.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(left_frame, text="Téléchargement", font=ctk.CTkFont(size=24, weight="bold"), text_color="#FFFFFF")
        title.grid(row=0, column=0, pady=(0, 16), sticky="w")

        url_label = ctk.CTkLabel(left_frame, text="Titre / URL playlist", font=ctk.CTkFont(weight="bold"), text_color="#B3B3B3")
        url_label.grid(row=1, column=0, pady=(0, 4), sticky="w")

        self.input_hint = ctk.CTkLabel(
            left_frame,
            text="Collez une ou plusieurs URL Spotify (morceau, album, playlist…), séparées par un espace",
            text_color="#B3B3B3",
            font=ctk.CTkFont(size=12),
            justify="left",
        )
        self.input_hint.grid(row=2, column=0, pady=(0, 12), sticky="w")

        self.query_entry = ctk.CTkEntry(
            left_frame,
            placeholder_text="https://open.spotify.com/...",
            fg_color="#282828",
            border_width=0,
            height=40,
        )
        self.query_entry.grid(row=3, column=0, pady=(0, 8), sticky="ew")

        # --- Right Column ---
        right_frame = ctk.CTkFrame(content, fg_color="transparent")
        right_frame.grid(row=0, column=1, sticky="nsew")
        right_frame.grid_columnconfigure(0, weight=1)

        adv_label = ctk.CTkLabel(right_frame, text="Options avancées", font=ctk.CTkFont(weight="bold"), text_color="#FFFFFF")
        adv_label.grid(row=0, column=0, pady=(0, 12), sticky="w")

        self.persist_var = ctk.BooleanVar(value=self._get_gui_bool("persist", False))
        self.debug_var = ctk.BooleanVar(value=self._get_gui_bool("debug", False))
        self.no_splash_var = ctk.BooleanVar(value=self._get_gui_bool("no_splash", True))
        
        cb_kwargs = {"border_color": "#535353", "hover_color": "#1ED760", "checkmark_color": "#000000"}
        ctk.CTkCheckBox(right_frame, text="Session persistante (--persist)", variable=self.persist_var, **cb_kwargs).grid(row=1, column=0, pady=4, sticky="w")
        ctk.CTkCheckBox(right_frame, text="Mode debug (--debug)", variable=self.debug_var, **cb_kwargs).grid(row=2, column=0, pady=4, sticky="w")
        ctk.CTkCheckBox(right_frame, text="Masquer splash (--no-splash)", variable=self.no_splash_var, **cb_kwargs).grid(row=3, column=0, pady=(4, 16), sticky="w")

        actions_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        actions_frame.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        actions_frame.grid_columnconfigure((0, 1), weight=1)

        self.status_label = ctk.CTkLabel(actions_frame, text="Prêt", text_color="#1DB954", font=ctk.CTkFont(weight="bold"))
        self.status_label.grid(row=0, column=0, columnspan=2, pady=(0, 4), sticky="w")

        self.progress = ctk.CTkProgressBar(actions_frame, mode="indeterminate", progress_color="#1DB954", fg_color="#3E3E3E")
        self.progress.grid(row=1, column=0, columnspan=2, pady=(0, 4), sticky="ew")
        self.progress.set(0)

        self.progress_counter = ctk.CTkLabel(actions_frame, text="", text_color="#B3B3B3", font=ctk.CTkFont(size=12))
        self.progress_counter.grid(row=2, column=0, columnspan=2, pady=(0, 8), sticky="w")

        self.run_button = ctk.CTkButton(actions_frame, text="Lancer", command=self.run_command, fg_color="#1DB954", text_color="#000000", hover_color="#1ED760", font=ctk.CTkFont(weight="bold", size=15), height=40, corner_radius=20)
        self.run_button.grid(row=3, column=0, padx=(0, 4), sticky="ew")
        
        self.stop_button = ctk.CTkButton(actions_frame, text="Arrêter", command=self.stop_command, fg_color="transparent", text_color="#FFFFFF", hover_color="#282828", border_width=1, border_color="#B3B3B3", font=ctk.CTkFont(weight="bold", size=15), height=40, corner_radius=20)
        self.stop_button.grid(row=3, column=1, padx=(4, 0), sticky="ew")
        self.stop_button.configure(state="disabled")

        self._refresh_auth_status()

    def _build_output_panel(self, parent: ctk.CTkFrame) -> None:
        output_panel = ctk.CTkFrame(parent, fg_color="#181818", corner_radius=12)
        output_panel.grid(row=1, column=0, sticky="nsew")
        output_panel.grid_columnconfigure(0, weight=1)
        output_panel.grid_rowconfigure(1, weight=1)

        header_frame = ctk.CTkFrame(output_panel, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 12))
        header_frame.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(header_frame, text="Console", font=ctk.CTkFont(size=20, weight="bold"), text_color="#FFFFFF")
        title.grid(row=0, column=0, sticky="w")

        clear_button = ctk.CTkButton(header_frame, text="Effacer", command=self._clear_console, width=80, height=32, fg_color="transparent", text_color="#B3B3B3", hover_color="#282828", border_width=1, border_color="#535353", corner_radius=16)
        clear_button.grid(row=0, column=1, sticky="e")

        self.console = ctk.CTkTextbox(output_panel, wrap="word", font=("Consolas", 13), fg_color="#121212", text_color="#1DB954", corner_radius=8)
        self.console.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")
        self.console.insert("end", "Prêt pour le téléchargement.\n")
        self._setup_console_clipboard()

    def _clear_console(self) -> None:
        self.console.delete("1.0", "end")

    def _append_console(self, message: str) -> None:
        self.console.insert("end", message)
        self.console.see("end")

    def _setup_console_clipboard(self) -> None:
        def select_all(_event=None):
            self.console.focus_set()
            self.console.tag_add("sel", "1.0", "end-1c")
            return "break"

        def copy_selection(_event=None):
            self.console.focus_set()
            try:
                selected = self.console.get("sel.first", "sel.last")
            except Exception:
                return "break"
            self.clipboard_clear()
            self.clipboard_append(selected)
            return "break"

        def paste_clipboard(_event=None):
            self.console.focus_set()
            try:
                text = self.clipboard_get()
            except Exception:
                return "break"
            self.console.insert("insert", text)
            return "break"

        # Empêche l'édition accidentelle tout en gardant la selection/copie active.
        def block_typing(event):
            ctrl = (event.state & 0x4) != 0
            if ctrl and event.keysym.lower() in {"a", "c", "v"}:
                return None
            if event.keysym in {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"}:
                return None
            return "break"

        self.console.bind("<Control-a>", select_all)
        self.console.bind("<Control-c>", copy_selection)
        self.console.bind("<Control-v>", paste_clipboard)
        self.console.bind("<Key>", block_typing)

        menu = Menu(self, tearoff=0)
        menu.add_command(label="Copier", command=copy_selection)
        menu.add_command(label="Coller", command=paste_clipboard)
        menu.add_separator()
        menu.add_command(label="Tout selectionner", command=select_all)

        def show_context_menu(event) -> None:
            menu.tk_popup(event.x_root, event.y_root)

        self.console.bind("<Button-3>", show_context_menu)

    def _try_extract_login_url(self, line: str) -> str | None:
        lowered = line.lower()
        if "http://" not in lowered and "https://" not in lowered:
            return None
        parts = line.replace("\n", " ").split()
        for token in parts:
            if token.startswith("http://") or token.startswith("https://"):
                return token.strip(" \t\r\n'\"")
        return None

    def _open_oauth_url(self, url: str) -> None:
        if self.oauth_url_opened:
            return
        self.oauth_url_opened = True
        webbrowser.open(url)
        self._append_console("Lien de connexion ouvert automatiquement dans le navigateur.\n")

    def _show_login_success_popup(self) -> None:
        popup = ctk.CTkToplevel(self)
        popup.title("Connexion reussie")
        popup.geometry("420x170")
        popup.transient(self)
        popup.grab_set()

        ctk.CTkLabel(
            popup,
            text="Connexion Spotify reussie.",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#22C55E",
        ).pack(padx=20, pady=(20, 8), anchor="w")
        ctk.CTkLabel(
            popup,
            text="Tu peux maintenant lancer tes telechargements.",
            text_color="#9AA6B2",
        ).pack(padx=20, pady=(0, 14), anchor="w")

        ctk.CTkButton(popup, text="OK", command=popup.destroy).pack(padx=20, pady=(0, 16), anchor="e")

    def _batch_convert(self) -> None:
        """Convert ALL convertible audio files after a batch/playlist download.

        Strategy:
          1. Sweep the playlist destination folder(s) recursively for every audio file
             whose .wav sibling does not already exist (catches files from previous
             runs too, in addition to the current session).
          2. Convert in parallel using a ThreadPoolExecutor (4 workers by default).
          3. Emit a ZOTIFY_CONV_PROGRESS line after every completion so the GUI
             updates its live progress bar.
          4. Delete the source .ogg after a successful conversion.
          5. Emit __ALL_DONE__ only after every conversion has finished.
        """
        fmt_label = self._conversion_label()
        if shutil.which("ffmpeg") is None:
            self._append_console("FFmpeg est introuvable. Installe-le ou ajoute-le au PATH.\n")
            self._append_console(f"Conversion {fmt_label} annulee pour tous les fichiers.\n")
            self.output_queue.put("__ALL_DONE__")
            return

        files_to_convert = self._collect_files_to_convert()

        if not files_to_convert:
            self._append_console(f"Aucun fichier audio a convertir en {fmt_label}.\n")
            self.batch_convert_stats = {"total": 0, "converted": 0, "failed": 0}
            self.output_queue.put("__ALL_DONE__")
            return

        n_total = len(files_to_convert)
        # Slightly conservative parallelism: 4 ffmpeg in parallel is enough,
        # going higher just thrashes disk/CPU without speed gain.
        max_workers = min(4, max(1, (os.cpu_count() or 4) // 2 + 1))
        self._append_console(f"\n{'='*50}\n")
        self._append_console(
            f"  Conversion {fmt_label} : {n_total} fichier{'s' if n_total > 1 else ''} a convertir "
            f"({max_workers} workers en parallele, timeout 10 min/fichier)\n"
        )
        self._append_console(f"{'='*50}\n\n")
        self.batch_convert_stats = {"total": n_total, "converted": 0, "failed": 0}
        self.conv_progress_current = 0
        self.conv_progress_total = n_total

        # Per-file ffmpeg timeout. Catches genuine ffmpeg hangs (corrupt file,
        # unexpected prompt, driver lock) instead of letting the whole batch stall.
        ffmpeg_timeout_s = 600

        # Windows: avoid spawning an ephemeral cmd.exe window for every ffmpeg call.
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        def convert_one(src: Path) -> tuple[Path, bool, str]:
            """Convertit un fichier audio en MP3 320k CBR ou WAV 24-bit PCM.

            Pipeline qualite maximale :
              1. Lecture des tags + pochette du fichier source via mutagen.
              2. Conversion ffmpeg en passant les metadonnees via -metadata
                 et la pochette comme 2eme input (attached_pic). FFmpeg
                 ecrit alors les chunks RIFF LIST/INFO (lus par
                 l'Explorateur Windows) ET les frames ID3v2.
              3. Finalisation mutagen : re-stamp ID3v2.3 UTF-8 + APIC
                 garanti -> compatibilite maximale (Windows Explorer,
                 WMP, foobar2000, MusicBee, iTunes...).

            Flags critiques pour eviter les blocages en parallele :
              -nostdin        : ffmpeg ne lit jamais stdin (sinon deadlock
                                avec plusieurs ffmpeg en parallele sur Windows).
              stdin=DEVNULL   : ceinture/bretelles.
              stderr=PIPE     : on garde la fin du log si erreur.
              timeout=10min   : empeche qu'un fichier bloque toute la batch.
            """
            dst = src.with_suffix(self._conversion_ext())
            is_mp3 = self._get_conversion_format() == "mp3"

            src_tags = self._read_source_tags(src)
            cover_bytes = self._extract_source_cover(src)

            # La pochette n'est passee a ffmpeg que pour MP3 (le muxeur
            # WAV de ffmpeg refuse les flux video et produirait un fichier
            # corrompu). Pour WAV elle est integree post-conversion via
            # mutagen dans le chunk ID3.
            cover_tmp: Path | None = None
            if cover_bytes and is_mp3:
                cover_ext = ".png" if cover_bytes.startswith(b"\x89PNG") else ".jpg"
                cover_tmp = dst.with_name(f"._zotify_cover_{os.getpid()}_{src.stem}{cover_ext}")
                try:
                    cover_tmp.write_bytes(cover_bytes)
                except OSError:
                    cover_tmp = None

            cmd: list[str] = [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-y", "-i", str(src),
            ]
            if cover_tmp is not None:
                cmd += [
                    "-i", str(cover_tmp),
                    "-map", "0:a",
                    "-map", "1:v",
                    "-c:v", "copy",
                    "-disposition:v", "attached_pic",
                    "-metadata:s:v", "title=Album cover",
                    "-metadata:s:v", "comment=Cover (front)",
                ]
            else:
                cmd += ["-vn"]

            metadata_map = {
                "title":        src_tags.get("title"),
                "artist":       src_tags.get("artist"),
                "album":        src_tags.get("album"),
                "album_artist": src_tags.get("albumartist"),
                "date":         src_tags.get("date"),
                "year":         src_tags.get("date"),
                "track":        src_tags.get("track"),
                "disc":         src_tags.get("disc"),
                "genre":        src_tags.get("genre"),
                "composer":     src_tags.get("composer"),
                "compilation":  src_tags.get("compilation"),
                "TRACKTOTAL":   src_tags.get("totaltracks"),
                "DISCTOTAL":    src_tags.get("totaldiscs"),
            }
            for key, val in metadata_map.items():
                if val:
                    cmd += ["-metadata", f"{key}={val}"]
            if src_tags.get("lyrics"):
                cmd += ["-metadata", f"lyrics-eng={src_tags['lyrics']}"]

            cmd += self._ffmpeg_encode_args()
            cmd.append(str(dst))

            try:
                try:
                    proc = subprocess.run(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                        timeout=ffmpeg_timeout_s,
                        creationflags=creationflags,
                    )
                except subprocess.TimeoutExpired:
                    try:
                        if dst.exists():
                            dst.unlink()
                    except OSError:
                        pass
                    return (src, False, f"Timeout apres {ffmpeg_timeout_s}s")
                except OSError as exc:
                    return (src, False, f"Erreur ffmpeg: {exc}")

                if proc.returncode != 0:
                    tail = ""
                    if proc.stderr:
                        tail = "\n".join(proc.stderr.strip().splitlines()[-3:])
                    return (src, False, tail)

                try:
                    self._finalize_tags(dst, src_tags, cover_bytes)
                except Exception as meta_exc:
                    self.output_queue.put(
                        f"  [Avertissement] Echec finalisation tags pour {dst.name}: {meta_exc}\n"
                    )

                if src.suffix.lower() == ".ogg":
                    try:
                        src.unlink()
                    except OSError:
                        pass

                return (src, True, "")
            finally:
                if cover_tmp is not None:
                    try:
                        cover_tmp.unlink()
                    except OSError:
                        pass

        def supervisor() -> None:
            converted = 0
            failed = 0
            done = 0
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                # Submit ALL jobs; queue submission immediately so workers
                # are saturated from the start.
                futures = {pool.submit(convert_one, src): src for src in files_to_convert}
                # Announce which files are queued up - useful so the user
                # sees activity even before the first conversion completes.
                self.output_queue.put(
                    f"  {len(futures)} fichier(s) en file, {max_workers} en cours en parallele...\n"
                )
                for future in as_completed(futures):
                    src, ok, err = future.result()
                    done += 1
                    with self._batch_convert_lock:
                        if ok:
                            converted += 1
                            self.batch_convert_stats["converted"] = converted
                            self.output_queue.put(f"  [{done}/{n_total}] OK  {src.name}\n")
                        else:
                            failed += 1
                            self.batch_convert_stats["failed"] = failed
                            self.output_queue.put(f"  [{done}/{n_total}] !!! ECHEC {src.name}\n")
                            if err:
                                for line in err.splitlines():
                                    self.output_queue.put(f"      {line}\n")
                    # Live progress signal for the GUI progress bar
                    self.output_queue.put(f"ZOTIFY_CONV_PROGRESS: {done}/{n_total}\n")

            self.batch_convert_stats = {"total": n_total, "converted": converted, "failed": failed}
            self.output_queue.put(f"\n{'='*50}\n")
            self.output_queue.put(f"  Conversion terminee: {converted}/{n_total} reussi{'s' if converted > 1 else ''}")
            if failed > 0:
                self.output_queue.put(f" ({failed} echec{'s' if failed > 1 else ''})")
            self.output_queue.put(f"\n{'='*50}\n\n")
            self.output_queue.put("__ALL_DONE__")

        threading.Thread(target=supervisor, daemon=True).start()

    def _collect_files_to_convert(self) -> list[Path]:
        """Build the list of source audio files that still need a converted sibling.

        Sources merged:
          - Paths captured during this session via ZOTIFY_DL_COMPLETE.
          - Recursive sweep of each parent folder of those captured paths.
          - Fallback recursive sweep of the configured download_dir if nothing
            was captured this session.

        Files already converted (target sibling exists with non-zero size) are
        skipped, so we never re-do work and we never leave anything behind.
        """
        target_ext = self._conversion_ext()
        audio_exts = {".ogg", ".m4a", ".flac", ".opus", ".aac"}
        if target_ext != ".mp3":
            audio_exts.add(".mp3")
        if target_ext != ".wav":
            audio_exts.add(".wav")
        candidates: set[Path] = set()
        sweep_roots: set[Path] = set()

        for p in self.all_downloaded_paths:
            if not isinstance(p, Path):
                continue
            resolved = p.expanduser()
            if not resolved.is_absolute():
                root_raw = self.download_dir_entry.get().strip()
                if root_raw:
                    resolved = Path(root_raw).expanduser() / resolved
                else:
                    resolved = Path.cwd() / resolved
            if resolved.exists() and resolved.is_file() and resolved.suffix.lower() in audio_exts:
                candidates.add(resolved)
            if resolved.parent.exists() and resolved.parent.is_dir():
                sweep_roots.add(resolved.parent)

        if not sweep_roots:
            root_raw = self.download_dir_entry.get().strip()
            if root_raw:
                root = Path(root_raw).expanduser()
                if root.exists() and root.is_dir():
                    sweep_roots.add(root)

        for root in sweep_roots:
            try:
                for path in root.rglob("*"):
                    if path.is_file() and path.suffix.lower() in audio_exts:
                        candidates.add(path)
            except OSError:
                continue

        files_to_convert: list[Path] = []
        for src in candidates:
            dst = src.with_suffix(target_ext)
            try:
                if dst.exists() and dst.is_file() and dst.stat().st_size > 0:
                    continue
            except OSError:
                pass
            files_to_convert.append(src)

        files_to_convert.sort(key=lambda p: str(p).lower())
        return files_to_convert

    def _resolve_last_downloaded_audio_path(self) -> Path | None:
        if self.last_downloaded_path:
            candidate = self.last_downloaded_path
            if not candidate.is_absolute():
                root_raw = self.download_dir_entry.get().strip()
                if root_raw:
                    candidate = Path(root_raw).expanduser() / candidate
                else:
                    candidate = Path.cwd() / candidate
            if candidate.exists():
                return candidate

        root_raw = self.download_dir_entry.get().strip()
        if not root_raw:
            return None
        root = Path(root_raw).expanduser()
        if not root.exists():
            return None

        audio_exts = {".ogg", ".m4a", ".mp3", ".flac", ".opus", ".aac", ".wav"}
        candidates = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in audio_exts]
        if not candidates:
            return None

        return max(candidates, key=lambda p: p.stat().st_mtime)

    def _extract_download_metadata(self, msg: str) -> None:
        # Capture explicit final-failure signal (MANDATORY) emitted by api.py after retries
        dl_failed_match = re.search(r'ZOTIFY_DL_FAILED:\s*"([^"]+)"\s*\(([^)]+)\)', msg)
        if dl_failed_match:
            name = dl_failed_match.group(1).strip()
            uri = dl_failed_match.group(2).strip()
            if (name, uri) not in self.failed_tracks:
                self.failed_tracks.append((name, uri))

        # Capture conversion progress signal (emitted from the batch worker thread itself)
        conv_match = re.search(r'ZOTIFY_CONV_PROGRESS:\s*(\d+)/(\d+)', msg)
        if conv_match:
            self.conv_progress_current = int(conv_match.group(1))
            self.conv_progress_total = int(conv_match.group(2))
            self._update_conv_progress()

        # Capture download paths from the ZOTIFY_DL_COMPLETE signal (MANDATORY channel, always visible)
        dl_complete_match = re.search(r'ZOTIFY_DL_COMPLETE:\s*"([^"]+)"', msg)
        if dl_complete_match:
            raw_path = dl_complete_match.group(1).strip()
            parsed = Path(raw_path).expanduser()
            if not parsed.is_absolute():
                root_raw = self.download_dir_entry.get().strip()
                if root_raw:
                    parsed = Path(root_raw).expanduser() / parsed
            self.last_downloaded_path = parsed
            # Accumulate all downloaded paths for batch conversion
            if parsed not in self.all_downloaded_paths:
                self.all_downloaded_paths.append(parsed)

        # Also try the old format (for non-standard-interface mode)
        downloaded_match = re.search(r'(?:DOWNLOADED|SKIPPING):\s*"([^"]+)"', msg)
        if downloaded_match:
            raw_path = downloaded_match.group(1).strip()
            parsed = Path(raw_path).expanduser()
            if not parsed.is_absolute():
                root_raw = self.download_dir_entry.get().strip()
                if root_raw:
                    parsed = Path(root_raw).expanduser() / parsed
            self.last_downloaded_path = parsed
            if parsed not in self.all_downloaded_paths:
                self.all_downloaded_paths.append(parsed)

        # Capture progress updates from ZOTIFY_PROGRESS signal
        progress_match = re.search(r'ZOTIFY_PROGRESS:\s*(\d+)/(\d+)', msg)
        if progress_match:
            self.dl_progress_current = int(progress_match.group(1))
            self.dl_progress_total = int(progress_match.group(2))
            self._update_download_progress()

        patterns = {
            "title": r"Track Name ==\s*(.+)",
            "artist": r"Artist Name ==\s*(.+)",
            "album": r"Album Name ==\s*(.+)",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, msg)
            if match:
                value = match.group(1).strip()
                self.last_download_metadata[key] = value
                # Accumule la liste pour la page Success en mode multi-DL
                if key == "title" and value and value not in self.batch_track_titles:
                    self.batch_track_titles.append(value)
                elif key == "artist" and value and value not in self.batch_track_artists:
                    self.batch_track_artists.append(value)

    def _update_download_progress(self) -> None:
        """Update the progress bar during download.

        Note : on n'affiche PAS de compteur "X/Y morceaux telecharges" car
        avec les telechargements paralleles (plusieurs subprocess Zotify
        actifs simultanement), les signaux ZOTIFY_PROGRESS de chaque process
        ecrasent la valeur du precedent et le compteur affiche un nombre
        totalement faux. Le bandeau de statut (_update_dl_ui_state) affiche
        deja le nombre de DL actifs/en file, ce qui est l'info utile.
        """
        if self.dl_progress_total > 0:
            self.progress.configure(mode="determinate")
            fraction = self.dl_progress_current / self.dl_progress_total
            self.progress.set(fraction)
        # On purge tout texte residuel du compteur, qu'il ait ete pose par
        # une ancienne version ou par la phase de conversion.
        self.progress_counter.configure(text="")

    def _update_conv_progress(self) -> None:
        """Update the progress bar and counter label during audio conversion."""
        if self.conv_progress_total > 0:
            fmt_label = self._conversion_label()
            self.progress.configure(mode="determinate")
            fraction = self.conv_progress_current / self.conv_progress_total
            self.progress.set(fraction)
            self.progress_counter.configure(
                text=f"{self.conv_progress_current}/{self.conv_progress_total} fichiers convertis en {fmt_label}"
            )
            self.status_label.configure(
                text=f"Conversion {fmt_label} ({self.conv_progress_current}/{self.conv_progress_total})",
                text_color="#1DB954",
            )

    def _browse_default_config(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choisir un fichier config",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not selected:
            selected = filedialog.askdirectory(title="Ou choisir un dossier de config")
        if selected:
            self.default_config_entry.delete(0, "end")
            self.default_config_entry.insert(0, selected)

    def _browse_directory(self, entry: ctk.CTkEntry) -> None:
        selected = filedialog.askdirectory(title="Choisir un dossier")
        if selected:
            entry.delete(0, "end")
            entry.insert(0, selected)

    def _build_base_cli_args(self) -> list[str]:
        if getattr(sys, "frozen", False):
            # En mode executable, on relance l'exe lui-meme sans --gui.
            args = [sys.executable]
        else:
            args = [sys.executable, "-u", "-m", "zotify"]
        config_path = self.default_config_entry.get().strip()
        download_dir = self.download_dir_entry.get().strip()
        podcast_dir = self.podcast_dir_entry.get().strip()

        if self.debug_var.get():
            args.append("--debug")
        if self.no_splash_var.get():
            args.append("--no-splash")
        # Use standard interface to avoid ANSI loader animations
        # that corrupt subprocess stdout in the GUI console.
        args.append("--standard-interface")
        args.append("True")
        if config_path:
            args.extend(["--config-location", config_path])
        if download_dir:
            args.extend(["--root-path", download_dir])
        if podcast_dir:
            args.extend(["--root-podcast-path", podcast_dir])
            
        # Desactive la generation des fichiers .lrc et des archives cache (.song_ids) localement
        args.extend(["--lyrics-to-file", "False"])
        args.extend(["--disable-directory-archives", "True"])
        
        client_id = self.client_id_entry.get().strip()
        if client_id:
            args.extend(["--client-id", client_id])
        return args

    def _resolve_resource_path(self, *parts: str) -> Path:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        return base.joinpath(*parts)

    def _resolve_config_json_path(self) -> Path:
        """Chemin du config.json (même logique que Config.load)."""
        if hasattr(self, "default_config_entry"):
            config_input = self.default_config_entry.get().strip()
        else:
            config_input = str(self.gui_settings.get("default_config_path", "")).strip()

        if config_input:
            config_dir_or_file = Path(config_input).expanduser()
        else:
            system_paths = {
                "win32": Path.home() / "AppData" / "Roaming" / "Zotify",
                "linux": Path.home() / ".config" / "zotify",
                "darwin": Path.home() / "Library" / "Application Support" / "Zotify",
            }
            config_dir_or_file = system_paths.get(sys.platform, Path.cwd() / ".zotify")
        return config_dir_or_file if config_dir_or_file.suffix else config_dir_or_file / "config.json"

    def _load_config_json_dict(self) -> dict | None:
        config_path = self._resolve_config_json_path()
        if not config_path.exists():
            return None
        try:
            with open(config_path, "r", encoding="utf-8") as config_file:
                data = json.load(config_file)
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _find_deprecated_keys_in_config(self) -> set[str]:
        data = self._load_config_json_dict()
        if not data:
            return set()
        return {key for key in GUI_DEPRECATED_CONFIG_KEYS if key in data}

    def _refresh_deprecated_key_labels(self) -> None:
        if not hasattr(self, "deprec_key_labels"):
            return
        present = self._find_deprecated_keys_in_config()
        for key, label in self.deprec_key_labels.items():
            if key in present:
                label.configure(text="(présente dans config.json)", text_color="#E8A838")
            else:
                label.configure(text="(absente dans config.json)", text_color="#6B6B6B")

    def _remove_deprecated_keys_from_config(self, keys: list[str]) -> tuple[int, str]:
        """Supprime les clés cochées du config.json. Retourne (nombre supprimé, message)."""
        config_path = self._resolve_config_json_path()
        if not config_path.exists():
            return 0, f"Fichier introuvable : {config_path}"

        try:
            with open(config_path, "r", encoding="utf-8") as config_file:
                data = json.load(config_file)
        except (OSError, json.JSONDecodeError) as exc:
            return 0, f"Impossible de lire config.json : {exc}"

        if not isinstance(data, dict):
            return 0, "config.json invalide (objet JSON attendu)."

        removed: list[str] = []
        for key in keys:
            if key in data:
                del data[key]
                removed.append(key)

        if not removed:
            return 0, "Aucune des clés cochées n'était présente dans config.json."

        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as config_file:
                json.dump(data, config_file, indent=4, ensure_ascii=False)
        except OSError as exc:
            return 0, f"Impossible d'écrire config.json : {exc}"

        names = ", ".join(removed)
        return len(removed), f"{len(removed)} clé(s) supprimée(s) : {names}"

    def _apply_deprecated_config_cleanup(self) -> None:
        keys_to_remove = [key for key, var in self.deprec_key_vars.items() if var.get()]
        if not keys_to_remove:
            self.settings_info.configure(
                text="Coche au moins une option obsolète à supprimer.",
                text_color="#E8A838",
            )
            return

        count, message = self._remove_deprecated_keys_from_config(keys_to_remove)
        if count > 0:
            self.settings_info.configure(text=message, text_color="#1DB954")
            self._append_console(f"Nettoyage config.json : {message}\n")
            self._refresh_deprecated_key_labels()
            for key in keys_to_remove:
                if key not in self._find_deprecated_keys_in_config():
                    self.deprec_key_vars[key].set(False)
        else:
            self.settings_info.configure(text=message, text_color="#E8A838")
            self._append_console(f"Nettoyage config.json : {message}\n")

    def _resolve_credentials_path(self) -> Path:
        """Resolve credentials path matching the CLI's Config.get_credentials_location() logic.
        
        The CLI determines the credentials path by:
        1. Reading CREDENTIALS_LOCATION from config.json
        2. If empty, using the platform default directory
        3. Appending 'credentials.json' if the path has no suffix
        
        The GUI must replicate this so the auth status display is accurate.
        """
        config_json = self._resolve_config_json_path()

        # Step 2: Try to read CREDENTIALS_LOCATION from config.json
        cred_location = ""
        if config_json.exists():
            try:
                with open(config_json, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if isinstance(cfg, dict):
                    cred_location = cfg.get("CREDENTIALS_LOCATION", "")
            except (OSError, json.JSONDecodeError):
                pass

        # Step 3: Resolve the credentials path (same logic as Config.get_credentials_location)
        if not cred_location:
            system_paths = {
                "win32": Path.home() / "AppData" / "Roaming" / "Zotify",
                "linux": Path.home() / ".local" / "share" / "zotify",
                "darwin": Path.home() / "Library" / "Application Support" / "Zotify",
            }
            cred_dir_or_file = system_paths.get(sys.platform, Path.cwd() / ".zotify")
        else:
            cred_dir_or_file = Path(cred_location).expanduser()

        credentials = cred_dir_or_file if cred_dir_or_file.suffix else cred_dir_or_file / "credentials.json"
        return credentials

    def _refresh_auth_status(self) -> None:
        cred_path = self._resolve_credentials_path()
        if cred_path.exists():
            self.nav_auth_status.configure(text="Spotify: Connecté", text_color="#1DB954")
            self.nav_auth_button.configure(text="Se déconnecter", fg_color="transparent", border_color="#535353")
        else:
            self.nav_auth_status.configure(text="Spotify: Déconnecté", text_color="#B3B3B3")
            self.nav_auth_button.configure(text="Se connecter", fg_color="transparent", border_color="#B3B3B3")

    def _resolve_settings_path(self) -> Path:
        if sys.platform == "win32":
            appdata = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
            settings_dir = appdata / "Zotify"
        elif sys.platform == "darwin":
            settings_dir = Path.home() / "Library" / "Application Support" / "Zotify"
        else:
            settings_dir = Path.home() / ".config" / "zotify"
        settings_dir.mkdir(parents=True, exist_ok=True)
        return settings_dir / "gui_settings.json"

    def _load_gui_settings(self) -> dict[str, str | bool]:
        if not self.settings_path.exists():
            return {}
        try:
            with open(self.settings_path, "r", encoding="utf-8") as settings_file:
                loaded = json.load(settings_file)
                if isinstance(loaded, dict):
                    return loaded
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _get_gui_bool(self, key: str, default: bool) -> bool:
        value = self.gui_settings.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _get_conversion_format(self) -> str:
        if hasattr(self, "conversion_format_var"):
            fmt = self.conversion_format_var.get().strip().lower()
        else:
            fmt = str(self.gui_settings.get("conversion_format", "wav")).strip().lower()
        return "mp3" if fmt == "mp3" else "wav"

    def _conversion_ext(self) -> str:
        return ".mp3" if self._get_conversion_format() == "mp3" else ".wav"

    def _conversion_label(self) -> str:
        return "MP3" if self._get_conversion_format() == "mp3" else "WAV"

    def _ffmpeg_encode_args(self) -> list[str]:
        """Arguments d'encodage haute qualite (sans chemin de sortie).

        MP3 : LAME 320 kbps CBR + ``compression_level 0`` (algorithme
              psycho-acoustique le plus precis / lent) + ID3v2.3 +
              ID3v1 en complement pour compatibilite maximale.
        WAV : PCM 24-bit little-endian, sans resampling (preserve
              integralement le signal decode depuis l'OGG source).
              On NE force PAS ``-rf64`` ni ``-write_id3v2`` : ffmpeg
              ecrit alors un WAV RIFF standard avec chunks LIST/INFO
              pour les metadonnees (lus par l'Explorateur Windows).
              Les tags ID3v2.3 + APIC (pochette) sont ajoutes en
              post-traitement par mutagen pour plus de robustesse.
        """
        if self._get_conversion_format() == "mp3":
            return [
                "-c:a", "libmp3lame",
                "-b:a", "320k",
                "-compression_level", "0",
                "-id3v2_version", "3",
                "-write_id3v1", "1",
            ]
        return [
            "-c:a", "pcm_s24le",
        ]

    def _read_source_tags(self, src: Path) -> dict[str, str]:
        """Lit les tags textuels du fichier source (OGG/MP3/M4A/FLAC/WAV).

        Retourne un dict normalise (titre/artiste/album/...). Les valeurs
        absentes sont omises pour eviter d'ecraser quoi que ce soit avec
        une chaine vide cote ffmpeg/mutagen.
        """
        out: dict[str, str] = {}
        try:
            import music_tag  # already a Zotify dep
            tags = music_tag.load_file(str(src))

            def grab(key: str) -> str:
                try:
                    val = tags[key].value
                except Exception:
                    return ""
                if val is None:
                    return ""
                if isinstance(val, (list, tuple)):
                    val = val[0] if val else ""
                return str(val).strip()

            mapping = {
                "title":        "tracktitle",
                "artist":       "artist",
                "album":        "album",
                "albumartist":  "albumartist",
                "date":         "year",
                "track":        "tracknumber",
                "disc":         "discnumber",
                "totaltracks":  "totaltracks",
                "totaldiscs":   "totaldiscs",
                "genre":        "genre",
                "composer":     "composer",
                "compilation":  "compilation",
                "lyrics":       "lyrics",
            }
            for out_key, mt_key in mapping.items():
                val = grab(mt_key)
                if val:
                    out[out_key] = val
        except Exception:
            pass
        return out

    def _extract_source_cover(self, src: Path) -> bytes | None:
        """Extrait la pochette embarquee dans le fichier source.

        Supporte OGG (metadata_block_picture / coverart), MP3 (APIC),
        M4A (covr), FLAC (pictures) et WAV (APIC dans ID3). Retourne
        les octets bruts (JPEG ou PNG) ou ``None``.
        """
        suffix = src.suffix.lower()
        try:
            if suffix == ".ogg":
                from mutagen.oggvorbis import OggVorbis
                from mutagen.flac import Picture
                audio = OggVorbis(str(src))
                for b64data in audio.get("metadata_block_picture", []):
                    try:
                        return Picture(base64.b64decode(b64data)).data
                    except Exception:
                        continue
                for b64data in audio.get("coverart", []):
                    try:
                        return base64.b64decode(b64data)
                    except Exception:
                        continue
            elif suffix == ".mp3":
                from mutagen.id3 import ID3, ID3NoHeaderError
                try:
                    id3 = ID3(str(src))
                except ID3NoHeaderError:
                    return None
                for frame in id3.getall("APIC"):
                    if frame.data:
                        return frame.data
            elif suffix == ".m4a":
                from mutagen.mp4 import MP4
                mp4 = MP4(str(src))
                covers = mp4.tags.get("covr", []) if mp4.tags else []
                if covers:
                    return bytes(covers[0])
            elif suffix == ".flac":
                from mutagen.flac import FLAC
                flac = FLAC(str(src))
                if flac.pictures:
                    return flac.pictures[0].data
            elif suffix == ".wav":
                from mutagen.wave import WAVE
                wav = WAVE(str(src))
                if wav.tags is not None:
                    try:
                        frames = wav.tags.getall("APIC")
                    except Exception:
                        frames = []
                    for frame in frames:
                        if frame.data:
                            return frame.data
        except Exception:
            pass
        return None

    def _finalize_tags(
        self,
        dst: Path,
        src_tags: dict[str, str],
        cover_bytes: bytes | None,
    ) -> None:
        """Re-stamp robuste des tags ID3v2.3 + pochette via mutagen.

        ffmpeg ecrit deja la majorite des metadonnees lors de la
        conversion (LIST/INFO pour WAV + ID3 pour MP3/WAV), mais
        certaines frames (TPE2 album_artist, TPOS, USLT) et surtout
        l'APIC (pochette) ne sont pas toujours preservees correctement.
        On force ici l'etat final en ID3v2.3 UTF-8, format universel.
        """
        suffix = dst.suffix.lower()
        if suffix not in (".mp3", ".wav"):
            return

        from mutagen.id3 import (
            ID3, ID3NoHeaderError,
            TIT2, TPE1, TPE2, TALB, TRCK, TPOS, TCON, TDRC, TYER,
            TCOM, TCMP, USLT, APIC,
        )

        wav = None
        if suffix == ".mp3":
            try:
                id3 = ID3(str(dst))
            except ID3NoHeaderError:
                id3 = ID3()
        else:
            from mutagen.wave import WAVE
            wav = WAVE(str(dst))
            if wav.tags is None:
                wav.add_tags()
            id3 = wav.tags

        def set_frame(frame_cls, value):
            if not value:
                return
            try:
                id3.delall(frame_cls.__name__)
            except Exception:
                pass
            try:
                id3.add(frame_cls(encoding=3, text=[str(value)]))
            except Exception:
                pass

        set_frame(TIT2, src_tags.get("title"))
        set_frame(TPE1, src_tags.get("artist"))
        set_frame(TPE2, src_tags.get("albumartist"))
        set_frame(TALB, src_tags.get("album"))
        set_frame(TCON, src_tags.get("genre"))
        set_frame(TCOM, src_tags.get("composer"))

        date_val = src_tags.get("date")
        if date_val:
            set_frame(TDRC, date_val)
            year_str = date_val[:4] if len(date_val) >= 4 else date_val
            set_frame(TYER, year_str)

        track_val = src_tags.get("track")
        if track_val:
            trck = f"{track_val}/{src_tags['totaltracks']}" if src_tags.get("totaltracks") else track_val
            try:
                id3.delall("TRCK")
                id3.add(TRCK(encoding=3, text=[trck]))
            except Exception:
                pass

        disc_val = src_tags.get("disc")
        if disc_val:
            tpos = f"{disc_val}/{src_tags['totaldiscs']}" if src_tags.get("totaldiscs") else disc_val
            try:
                id3.delall("TPOS")
                id3.add(TPOS(encoding=3, text=[tpos]))
            except Exception:
                pass

        comp_val = src_tags.get("compilation")
        if comp_val:
            try:
                id3.delall("TCMP")
                truthy = str(comp_val).strip().lower() in ("1", "true", "yes", "y")
                id3.add(TCMP(encoding=3, text=["1" if truthy else "0"]))
            except Exception:
                pass

        lyrics_val = src_tags.get("lyrics")
        if lyrics_val:
            try:
                id3.delall("USLT")
                id3.add(USLT(encoding=3, lang="eng", desc="", text=lyrics_val))
            except Exception:
                pass

        if cover_bytes:
            mime = "image/png" if cover_bytes.startswith(b"\x89PNG") else "image/jpeg"
            try:
                id3.delall("APIC")
                id3.add(APIC(
                    encoding=3,
                    mime=mime,
                    type=3,  # cover (front)
                    desc="Cover",
                    data=cover_bytes,
                ))
            except Exception:
                pass

        # Force ID3v2.3 strict (UTF-16 au lieu de UTF-8, mono-valeur par
        # frame texte). Recommande par la doc mutagen pour la
        # compatibilite maximale avec les lecteurs anciens & Windows.
        try:
            id3.update_to_v23()
        except Exception:
            pass

        if suffix == ".mp3":
            id3.save(str(dst), v2_version=3)
        else:
            try:
                wav.save(v2_version=3)
            except TypeError:
                wav.save()
            # Reorganise les chunks pour maximum compatibilite :
            #   1. Ajoute un chunk RIFF LIST/INFO frais (lu par
            #      foobar2000, MusicBee, VLC, WMP via Media Foundation).
            #   2. Repositionne le chunk 'id3 ' ECRIT par mutagen pour
            #      qu'il soit AVANT le chunk 'data' (certains lecteurs
            #      ne scannent pas la zone post-data).
            try:
                self._write_wav_info_chunk(dst, src_tags)
            except Exception:
                pass

    def _write_wav_info_chunk(self, dst: Path, src_tags: dict[str, str]) -> None:
        """Insere/remplace le chunk RIFF ``LIST/INFO`` dans un WAV.

        Format officiel RIFF pour les metadonnees WAV, lu par :
          - l'Explorateur Windows (colonnes Titre/Artiste/Album/N°)
          - Windows Media Player / Lecteur multimedia
          - foobar2000, MusicBee, VLC, etc.

        On strippe tout LIST/INFO existant puis on en ecrit un frais
        juste avant le chunk ``data`` pour compatibilite maximale
        (certains lecteurs ne scannent pas la zone post-data).
        """
        info_map = {
            "INAM": src_tags.get("title"),         # nom du morceau
            "IART": src_tags.get("artist"),        # artiste principal
            "IPRD": src_tags.get("album"),         # produit / album
            "ICRD": src_tags.get("date"),          # date de creation / annee
            "IGNR": src_tags.get("genre"),         # genre
            "ITRK": src_tags.get("track"),         # numero de piste
            "IENG": src_tags.get("albumartist"),   # ingenieur => artiste de l'album
            "IMUS": src_tags.get("composer"),      # compositeur
            "ISFT": "Zotify GUI",                  # logiciel ayant cree le fichier
            "ICMT": src_tags.get("lyrics"),        # commentaire (parfois lyrics)
        }

        info_payload = b"INFO"
        for code, val in info_map.items():
            if not val:
                continue
            # RIFF INFO sub-chunks : data = chaine NULL-terminee.
            # ATTENTION spec : le champ size compte la donnee SANS le pad
            # d'alignement (1 octet ajoute si la donnee a une taille
            # impaire pour aligner le chunk suivant sur 2 octets).
            # Reporter une size incluant le pad rend le chunk illisible
            # pour le Property Handler WAV de Windows.
            encoded = str(val).encode("utf-8", errors="replace") + b"\x00"
            size = len(encoded)
            payload = encoded if size % 2 == 0 else encoded + b"\x00"
            info_payload += code.encode("ascii") + struct.pack("<I", size) + payload

        if info_payload == b"INFO":
            return

        # Le size du LIST chunk = taille totale de info_payload (qui
        # inclut deja le mot "INFO" et tous les sous-chunks alignes).
        list_chunk = b"LIST" + struct.pack("<I", len(info_payload)) + info_payload
        if len(list_chunk) % 2:
            list_chunk += b"\x00"

        try:
            data = dst.read_bytes()
        except OSError:
            return

        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return

        # Parse tous les chunks. On capture separement le chunk
        # 'data' (audio brut) et le chunk 'id3 ' (ecrit par mutagen)
        # pour pouvoir les reordonner. Les autres chunks (fmt, fact,
        # bext, etc.) conservent leur ordre original.
        chunks_before: list[bytes] = []
        chunks_after: list[bytes] = []
        data_chunk: bytes | None = None
        id3_chunk: bytes | None = None
        seen_data = False
        pos = 12
        while pos + 8 <= len(data):
            cid = data[pos:pos + 4]
            csize = struct.unpack("<I", data[pos + 4:pos + 8])[0]
            chunk_end = pos + 8 + csize + (csize & 1)
            if chunk_end > len(data):
                chunk_end = len(data)
            chunk_bytes = data[pos:chunk_end]
            # Saute toute LIST/INFO existante (on en ecrit une fraiche)
            if cid == b"LIST" and pos + 12 <= len(data) and data[pos + 8:pos + 12] == b"INFO":
                pos = chunk_end
                continue
            # Capture le chunk audio
            if cid == b"data":
                data_chunk = chunk_bytes
                seen_data = True
                pos = chunk_end
                continue
            # Capture le chunk id3 (peu importe sa position) pour
            # le repositionner AVANT data
            if cid in (b"id3 ", b"ID3 "):
                id3_chunk = chunk_bytes
                pos = chunk_end
                continue
            # Autres chunks : on conserve la position relative a data
            if seen_data:
                chunks_after.append(chunk_bytes)
            else:
                chunks_before.append(chunk_bytes)
            pos = chunk_end

        if data_chunk is None:
            # Fichier malforme : on ne touche pas pour ne pas l'aggraver
            return

        # Reassemble dans l'ordre cible :
        # RIFF header / fmt + autres pre-data / LIST INFO / id3 / data / chunks post-data
        out = bytearray(data[:12])
        for c in chunks_before:
            out += c
        out += list_chunk
        if id3_chunk is not None:
            out += id3_chunk
        out += data_chunk
        for c in chunks_after:
            out += c

        new_size = len(out) - 8
        out[4:8] = struct.pack("<I", new_size)

        try:
            dst.write_bytes(bytes(out))
        except OSError:
            pass

    def save_settings(self) -> None:
        conv_fmt = self.conversion_format_var.get().strip().lower()
        deprecated_cleanup = {
            key: var.get() for key, var in self.deprec_key_vars.items()
        }
        self.gui_settings = {
            "download_dir": self.download_dir_entry.get().strip(),
            "podcast_dir": self.podcast_dir_entry.get().strip(),
            "default_config_path": self.default_config_entry.get().strip(),
            "api_client_id": self.client_id_entry.get().strip(),
            "conversion_format": "mp3" if conv_fmt == "mp3" else "wav",
            "deprecated_cleanup": deprecated_cleanup,
            "persist": self.persist_var.get(),
            "debug": self.debug_var.get(),
            "no_splash": self.no_splash_var.get(),
        }
        cleanup_messages: list[str] = []
        keys_to_remove = [key for key, checked in deprecated_cleanup.items() if checked]
        if keys_to_remove:
            count, message = self._remove_deprecated_keys_from_config(keys_to_remove)
            if count > 0:
                cleanup_messages.append(message)
                self._refresh_deprecated_key_labels()
                for key in keys_to_remove:
                    if key not in self._find_deprecated_keys_in_config():
                        self.deprec_key_vars[key].set(False)

        try:
            with open(self.settings_path, "w", encoding="utf-8") as settings_file:
                json.dump(self.gui_settings, settings_file, indent=2)
            status = "Paramètres sauvegardés."
            if cleanup_messages:
                status += " " + cleanup_messages[0]
            self.settings_info.configure(text=status, text_color="#1DB954")
            self._append_console(f"Paramètres sauvegardés : {self.settings_path}\n")
            for msg in cleanup_messages:
                self._append_console(f"Nettoyage config.json : {msg}\n")
        except OSError as exc:
            self.settings_info.configure(text=f"Erreur sauvegarde : {exc}", text_color="#E22134")
            self._append_console(f"Erreur sauvegarde paramètres: {exc}\n")

    def _build_cli_args(self, query_override: str | None = None) -> list[str]:
        args = self._build_base_cli_args()
        query = (
            query_override
            if query_override is not None
            else self.query_entry.get().strip()
        )

        if self.persist_var.get():
            args.append("--persist")

        if query:
            args.extend(query.split())
        args.extend(["--codec", "ogg"])

        return args

    # =====================================================================
    # GESTION DES TELECHARGEMENTS PARALLELES
    #
    # Architecture :
    #   * `auth_process`         : login/logout, strictement exclusif.
    #   * `active_downloads`     : dict {dl_id -> Popen}, jusqu'a
    #                              MAX_PARALLEL_DOWNLOADS simultanes.
    #   * `download_queue`       : deque[(url, command)] pour les
    #                              demandes en surnombre, traitee FIFO
    #                              au fur et a mesure que les workers
    #                              se liberent.
    #   * `_dl_lock`             : protege les structures ci-dessus.
    #
    # Signal a base d'`__DOWNLOAD_DONE__:{id}:{exit_code}` envoye par
    # chaque worker quand il termine, capture par `_drain_output_queue`.
    # =====================================================================

    def _is_batch_idle(self) -> bool:
        """True quand aucun telechargement n'est actif ni en file."""
        with self._dl_lock:
            return not self.active_downloads and not self.download_queue

    def _dl_status_text(self) -> str:
        n_active = len(self.active_downloads)
        n_queued = len(self.download_queue)
        if n_active == 0 and n_queued == 0:
            return "Pret"
        parts = []
        if n_active:
            parts.append(
                f"{n_active} telechargement{'s' if n_active > 1 else ''} en cours"
            )
        if n_queued:
            parts.append(f"{n_queued} en file")
        return "  |  ".join(parts)

    def run_command(self, url_override: str | None = None) -> None:
        """Lance un nouveau telechargement.

        Peut etre appele plusieurs fois consecutivement : les demandes
        excedant ``MAX_PARALLEL_DOWNLOADS`` sont mises en file et
        demarrees automatiquement quand un slot se libere.
        """
        if self.auth_process is not None:
            self._append_console(
                "Action impossible : login/logout en cours.\n"
            )
            return

        url = (
            url_override
            if url_override is not None
            else self.query_entry.get().strip()
        )
        if not url:
            self._append_console("Aucune URL/requete fournie.\n")
            return

        fresh_batch = self._is_batch_idle()
        if fresh_batch:
            self._reset_batch_state()

        command = self._build_cli_args(query_override=url)

        with self._dl_lock:
            if len(self.active_downloads) < MAX_PARALLEL_DOWNLOADS:
                self._launch_download_locked(url, command)
            else:
                self.download_queue.append((url, command))
                self._append_console(
                    f"[Queue] DL en file (position {len(self.download_queue)}) : {url}\n"
                )

        self._update_dl_ui_state()

    def _reset_batch_state(self) -> None:
        """Reinitialise les compteurs / etat partages de la batch.

        Appele uniquement quand on commence une nouvelle batch de
        telechargements (aucun actif, aucun en file). Sinon les nouveaux
        downloads s'agregent a la batch en cours.
        """
        self.current_action = "download"
        self.current_mode = "url"
        self.last_process_exit_code = None
        self.last_downloaded_path = None
        self.all_downloaded_paths = []
        self.last_download_metadata = {}
        self.batch_track_titles = []
        self.batch_track_artists = []
        self.batch_convert_stats = {"total": 0, "converted": 0, "failed": 0}
        self.dl_progress_current = 0
        self.dl_progress_total = 0
        self.conv_progress_current = 0
        self.conv_progress_total = 0
        self.failed_tracks = []
        self.last_dl_exit_codes = {}
        self.progress.configure(mode="indeterminate")
        self.progress.set(0)
        self.progress_counter.configure(text="")

    def _launch_download_locked(self, url: str, command: list[str]) -> int:
        """Demarre un subprocess de DL. DOIT etre appele sous _dl_lock."""
        dl_id = self.next_dl_id
        self.next_dl_id += 1
        # Reserve le slot des maintenant (le vrai Popen est cree dans
        # le thread, mais on veut compter ce DL comme actif des
        # maintenant pour ne pas spammer plus que MAX en parallele).
        self.active_downloads[dl_id] = None  # type: ignore[assignment]
        self._append_console(f"\n[DL#{dl_id}] $ {' '.join(command)}\n")
        thread = threading.Thread(
            target=self._run_download_subprocess,
            args=(dl_id, url, command),
            name=f"ZotifyDL#{dl_id}",
            daemon=True,
        )
        thread.start()
        return dl_id

    def _run_download_subprocess(self, dl_id: int, url: str, command: list[str]) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        popen_kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.PIPE,
            "text": True,
            "bufsize": 1,
            "env": env,
        }
        exit_code = -1
        proc: subprocess.Popen | None = None
        prefix = f"[DL#{dl_id}] "
        try:
            proc = subprocess.Popen(command, **popen_kwargs)
            with self._dl_lock:
                self.active_downloads[dl_id] = proc
            assert proc.stdout is not None
            for line in proc.stdout:
                # Les signaux internes (ZOTIFY_*) doivent rester intacts
                # pour etre parses par _extract_download_metadata.
                stripped = line.strip()
                is_signal = stripped.startswith((
                    "ZOTIFY_PROGRESS:",
                    "ZOTIFY_DL_COMPLETE:",
                    "ZOTIFY_DL_FAILED:",
                    "ZOTIFY_CONV_PROGRESS:",
                ))
                self.output_queue.put(line if is_signal else (prefix + line))
            exit_code = proc.wait()
            self.output_queue.put(f"{prefix}Processus termine (code {exit_code}).\n")
        except BaseException as exc:
            self.output_queue.put(f"\n{prefix}Erreur de lancement : {exc}\n")
        finally:
            with self._dl_lock:
                self.active_downloads.pop(dl_id, None)
                self.last_dl_exit_codes[dl_id] = exit_code
            self.output_queue.put(f"__DOWNLOAD_DONE__:{dl_id}:{exit_code}")

    def _dispatch_next_from_queue(self) -> None:
        """Demarre les DL de la file tant qu'il reste de la capacite.

        Appele depuis le main loop apres chaque __DOWNLOAD_DONE__.
        """
        with self._dl_lock:
            while (
                self.download_queue
                and len(self.active_downloads) < MAX_PARALLEL_DOWNLOADS
            ):
                url, command = self.download_queue.popleft()
                self._launch_download_locked(url, command)

    def _update_dl_ui_state(self) -> None:
        """Met a jour le status / progress / boutons selon l'etat actuel."""
        n_active = len(self.active_downloads)
        n_queued = len(self.download_queue)
        if n_active > 0 or n_queued > 0:
            self.progress.configure(mode="indeterminate")
            try:
                self.progress.start()
            except Exception:
                pass
            self.status_label.configure(
                text=self._dl_status_text(),
                text_color="#22C55E",
            )
            self.stop_button.configure(state="normal")
            self.nav_auth_button.configure(state="disabled")
            # On laisse le bouton "Lancer" ACTIF pour permettre d'empiler
            # de nouvelles requetes sans rafraichir l'UI.
            self.run_button.configure(state="normal")
        else:
            try:
                self.progress.stop()
            except Exception:
                pass
            self.status_label.configure(text="Pret", text_color="#9AA6B2")
            self.run_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.nav_auth_button.configure(state="normal")

    # =====================================================================
    # AUTH (login/logout) - reste strictement exclusif
    # =====================================================================

    def login_spotify(self) -> None:
        if self.auth_process is not None or self.active_downloads:
            self._append_console(
                "Action impossible : telechargement(s) en cours.\n"
            )
            return
        self.pending_oauth_url = None
        self.oauth_url_opened = False
        self.login_flow_active = True
        self.login_success_detected = False
        self.current_action = "login"
        self.last_process_exit_code = None
        command = self._build_base_cli_args() + ["--login-only"]
        self._start_auth_subprocess(command, "Connexion Spotify...")

    def logout_spotify(self) -> None:
        if self.auth_process is not None or self.active_downloads:
            self._append_console(
                "Action impossible : telechargement(s) en cours.\n"
            )
            return
        self.current_action = "logout"
        self.last_process_exit_code = None
        command = self._build_base_cli_args() + ["--logout"]
        self._start_auth_subprocess(command, "Deconnexion Spotify...")

    def toggle_spotify_auth(self) -> None:
        if self._resolve_credentials_path().exists():
            self.logout_spotify()
        else:
            self.login_spotify()

    def stop_command(self) -> None:
        """Arret d'urgence : tue tous les telechargements + vide la file."""
        # 1. Annule les downloads en file (jamais demarres)
        with self._dl_lock:
            n_cancelled = len(self.download_queue)
            self.download_queue.clear()
        if n_cancelled:
            self._append_console(
                f"[Stop] {n_cancelled} DL en file annule(s).\n"
            )

        # 2. Kill tous les downloads actifs (snapshot pour ne pas muter
        #    le dict pendant qu'on itere).
        with self._dl_lock:
            active_snapshot = list(self.active_downloads.items())
        for dl_id, proc in active_snapshot:
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    pass
                self._append_console(f"[Stop] DL#{dl_id} arrete.\n")

        # 3. Auth process si actif
        if self.auth_process is not None and self.auth_process.poll() is None:
            try:
                self.auth_process.kill()
                self.auth_process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
            self._append_console("[Stop] Auth process arrete.\n")

    def _start_auth_subprocess(self, command: list[str], status_text: str) -> None:
        self._append_console(f"\n$ {' '.join(command)}\n")
        self.status_label.configure(text=status_text, text_color="#22C55E")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.run_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.nav_auth_button.configure(state="disabled")
        thread = threading.Thread(
            target=self._run_auth_subprocess,
            args=(command,),
            daemon=True,
        )
        thread.start()

    def _run_auth_subprocess(self, command: list[str]) -> None:
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            popen_kwargs: dict = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "stdin": subprocess.PIPE,
                "text": True,
                "bufsize": 1,
                "env": env,
            }
            self.auth_process = subprocess.Popen(command, **popen_kwargs)
            assert self.auth_process.stdout is not None
            for line in self.auth_process.stdout:
                self.output_queue.put(line)
            return_code = self.auth_process.wait()
            self.output_queue.put(f"\nProcessus termine (code {return_code}).\n")
        except BaseException as exc:
            self.output_queue.put(f"\nErreur de lancement: {exc}\n")
        finally:
            self.auth_process = None
            self.output_queue.put("__AUTH_DONE__")

    # =====================================================================
    # HANDLERS appeles par _drain_output_queue (main thread Tkinter)
    # =====================================================================

    def _on_download_done(self, msg: str) -> None:
        """Un telechargement s'est termine. Format: ``__DOWNLOAD_DONE__:id:code``."""
        try:
            _, dl_id_s, exit_s = msg.split(":", 2)
            exit_code = int(exit_s)
        except (ValueError, IndexError):
            return

        # `last_process_exit_code` reste utilise par la success page ;
        # on retient le code du DERNIER DL termine. Pour les decisions
        # de batch on s'appuie sur ``last_dl_exit_codes``.
        self.last_process_exit_code = exit_code

        # Demarrer eventuellement le DL suivant en file
        self._dispatch_next_from_queue()

        if self._is_batch_idle():
            self._finalize_batch()
        else:
            self._update_dl_ui_state()

    def _on_auth_done(self) -> None:
        try:
            self.progress.stop()
        except Exception:
            pass
        self.run_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.nav_auth_button.configure(state="normal")
        self.status_label.configure(text="Pret", text_color="#9AA6B2")
        self._refresh_auth_status()
        if self.login_flow_active:
            self.login_flow_active = False
            if self._resolve_credentials_path().exists():
                if not self.login_success_detected:
                    self.login_success_detected = True
                self._show_login_success_popup()
                self.show_page("Download")
        self.current_action = "idle"
        self.current_mode = ""

    def _finalize_batch(self) -> None:
        """Toute la batch (actifs + file) est terminee.

        On declenche la conversion auto si au moins un DL a reussi
        (au moins un exit_code == 0). Sinon on remet juste l'UI au
        repos sans conversion.
        """
        any_success = any(
            code == 0 for code in self.last_dl_exit_codes.values()
        )
        if not any_success:
            self._update_dl_ui_state()
            self.current_action = "idle"
            self.current_mode = ""
            return

        fmt_label = self._conversion_label()
        # UI : busy pendant la conversion
        self.run_button.configure(state="disabled")
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self.status_label.configure(
            text=f"Preparation de la conversion {fmt_label}...",
            text_color="#1DB954",
        )
        n_files = len(self.all_downloaded_paths)
        n_dl = len(self.last_dl_exit_codes)
        n_ok = sum(1 for c in self.last_dl_exit_codes.values() if c == 0)
        if n_files >= 1:
            self._append_console(
                f"Batch terminee ({n_ok}/{n_dl} DL OK, "
                f"{n_files} fichier{'s' if n_files > 1 else ''}). "
                f"Conversion {fmt_label} (sweep complet du dossier)...\n"
            )
        else:
            self._append_console(
                "Aucun fichier capture dans la session. Sweep du dossier de "
                "telechargement pour rattraper les fichiers orphelins...\n"
            )
        self._batch_convert()

    def _drain_output_queue(self) -> None:
        try:
            while True:
                msg = self.output_queue.get_nowait()
                if msg == "__ALL_DONE__":
                    self.progress.stop()
                    self.run_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.nav_auth_button.configure(state="normal")
                    self.status_label.configure(text="Prêt", text_color="#9AA6B2")
                    self.show_page("Success")
                    self.current_action = "idle"
                    self.current_mode = ""
                    continue
                if msg.startswith("__DOWNLOAD_DONE__:"):
                    self._on_download_done(msg)
                    continue
                if msg == "__AUTH_DONE__":
                    self._on_auth_done()
                    continue
                else:
                    self._extract_download_metadata(msg)
                    exit_match = re.search(r"Processus termine \(code\s+(-?\d+)\)", msg)
                    if exit_match:
                        self.last_process_exit_code = int(exit_match.group(1))
                    maybe_url = self._try_extract_login_url(msg)
                    if maybe_url and self.pending_oauth_url != maybe_url:
                        self.pending_oauth_url = maybe_url
                        self._open_oauth_url(maybe_url)
                    lowered_msg = msg.lower()
                    if self.login_flow_active and (
                        "received callback" in lowered_msg
                        or "spotify login completed" in lowered_msg
                        or "login completed" in lowered_msg
                        or "session initialized successfully" in lowered_msg
                    ):
                        if not self.login_success_detected:
                            self.login_success_detected = True
                            self._append_console("Connexion Spotify detectee, finalisation...\n")
                    # Filter out noisy loader animation lines
                    stripped_msg = msg.strip()
                    noisy_login_line = (
                        "logging in..." in lowered_msg
                        or stripped_msg.startswith("[...")
                        or stripped_msg.startswith("[>..]")
                        or stripped_msg.startswith("[.>.]")
                        or stripped_msg.startswith("[..>]")
                        or stripped_msg.startswith("[\u2219")
                        or stripped_msg.startswith("[\u25cf")
                    )
                    if noisy_login_line:
                        continue
                    # Filter internal GUI<->CLI signals (already parsed above) so they
                    # do not pollute the user-facing console output.
                    if stripped_msg.startswith(("ZOTIFY_PROGRESS:", "ZOTIFY_DL_COMPLETE:",
                                                "ZOTIFY_DL_FAILED:", "ZOTIFY_CONV_PROGRESS:")):
                        continue
                    if msg == self.last_console_line:
                        continue
                    self.last_console_line = msg
                    self._append_console(msg)
        except Empty:
            pass
        finally:
            self.after(100, self._drain_output_queue)

    def _on_close(self) -> None:
        """Kill all running subprocesses (auth + downloads) before closing."""
        # Stoppe le serveur HTTP local
        try:
            from zotify.gui_server import stop_server
            stop_server(getattr(self, "bridge_server", None))
        except Exception:
            pass

        # Vide la file de DL
        with self._dl_lock:
            self.download_queue.clear()

        # Kill tous les DL actifs
        with self._dl_lock:
            active_snapshot = list(self.active_downloads.values())
        for proc in active_snapshot:
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    pass

        # Kill l'auth process si actif
        if self.auth_process is not None and self.auth_process.poll() is None:
            try:
                self.auth_process.kill()
                self.auth_process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass

        self.destroy()

    def trigger_download_from_url(self, url: str) -> tuple[bool, str]:
        """Appele par le serveur HTTP local (extensions Spotify) pour
        declencher un telechargement avec l'URL donnee. Renvoie
        ``(success, message)``.

        Avec le mode parallele : ne refuse JAMAIS, met simplement en
        file si MAX_PARALLEL_DOWNLOADS est deja atteint.

        Cette methode est invoquee depuis un thread serveur ; toute
        manipulation de widgets Tkinter doit imperativement passer
        par ``self.after(0, ...)`` pour s'executer sur le main loop.
        """
        if self.auth_process is not None:
            return (False, "Login/logout Spotify en cours, reessaie dans un instant.")

        def _apply() -> None:
            try:
                self.query_entry.delete(0, "end")
                self.query_entry.insert(0, url)
            except Exception:
                pass
            try:
                self.show_page("Download")
            except Exception:
                pass
            self._append_console(f"\n[Bridge] Demande recue depuis Spotify :\n  {url}\n")
            try:
                self.run_command(url_override=url)
            except Exception as exc:
                self._append_console(f"[Bridge] Echec lancement : {exc}\n")

        self.after(0, _apply)

        n_active = len(self.active_downloads)
        n_queued = len(self.download_queue)
        if n_active >= MAX_PARALLEL_DOWNLOADS:
            return (True, f"Mis en file (position {n_queued + 1}).")
        return (True, "Telechargement lance en parallele." if n_active else "Telechargement demarre.")


def launch_gui() -> None:
    app = ZotifyGUI()
    app.mainloop()

