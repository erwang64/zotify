from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
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
GUI_VERSION: Final[str] = "1.1.0"
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

        self.current_process: subprocess.Popen | None = None
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

    def _animate_success_page(self) -> None:
        self.anim_canvas.delete("all")
        
        is_batch = self.batch_convert_stats["total"] > 1
        n_dl_failed = len(self.failed_tracks)
        
        if is_batch:
            n_total = self.batch_convert_stats["total"]
            n_ok = self.batch_convert_stats["converted"]
            n_fail = self.batch_convert_stats["failed"]
            self.success_title.configure(text="Playlist Téléchargée \u0026 Convertie")
            subtitle_parts = [f"{n_ok}/{n_total} morceaux convertis en {self._conversion_label()}"]
            if n_fail > 0:
                subtitle_parts.append(f"({n_fail} échec{'s' if n_fail > 1 else ''} ffmpeg)")
            if n_dl_failed > 0:
                subtitle_parts.append(f"- {n_dl_failed} indisponible{'s' if n_dl_failed > 1 else ''} sur Spotify")
            self.success_subtitle.configure(text=" ".join(subtitle_parts))
            self.success_track_title.configure(text=self.last_download_metadata.get("title", f"{n_total} morceaux"))
            self.success_track_artist.configure(text=self.last_download_metadata.get("artist", ""))
            self.success_track_album.configure(text=self.last_download_metadata.get("album", ""))
        else:
            self.success_title.configure(text="Téléchargement \u0026 Conversion Terminés")
            if n_dl_failed > 0:
                self.success_subtitle.configure(
                    text=f"{n_dl_failed} morceau{'x' if n_dl_failed > 1 else ''} indisponible{'s' if n_dl_failed > 1 else ''} sur Spotify"
                )
            else:
                self.success_subtitle.configure(text="")
            title = self.last_download_metadata.get("title", "Titre Inconnu")
            artist = self.last_download_metadata.get("artist", "Artiste Inconnu")
            album = self.last_download_metadata.get("album", "Album Inconnu")
            self.success_track_title.configure(text=title)
            self.success_track_artist.configure(text=artist)
            self.success_track_album.configure(text=album)
        
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
        
        # Try to extract cover art from the last downloaded file
        cover_source = None
        if self.last_downloaded_path and MUTAGEN_AVAILABLE:
            # Try .ogg first (original before conversion), then .wav, then the path itself
            for suffix in [".ogg", ".wav", ".mp3", self.last_downloaded_path.suffix]:
                candidate = self.last_downloaded_path.with_suffix(suffix)
                if candidate.exists() and candidate.suffix.lower() == ".ogg":
                    cover_source = candidate
                    break
        
        if cover_source:
            try:
                pil_img = self._extract_cover_from_ogg(cover_source)
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
            """Run ffmpeg for a single file. Returns (src, ok, error_excerpt).

            Critical flags to avoid hangs in parallel subprocess scenarios:
              -nostdin       : tells ffmpeg NEVER to read from stdin (otherwise
                               4 parallel ffmpeg processes can deadlock on a
                               shared/closed stdin on Windows).
              -hide_banner   : trim verbose output.
              -loglevel error: suppress info/warning chatter we never display.
              stdin=DEVNULL  : extra safety so ffmpeg can't ever block on stdin.
              stdout=DEVNULL : we don't need progress chatter from ffmpeg.
              stderr=PIPE    : keep error tail for diagnosis if it fails.
              timeout=10min  : prevents a single bad file from halting the batch.
            """
            dst = src.with_suffix(self._conversion_ext())
            cmd = [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-y", "-i", str(src), "-vn", *self._ffmpeg_encode_args(dst),
            ]
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
                if proc.returncode == 0:
                    if src.suffix.lower() == ".ogg":
                        try:
                            src.unlink()
                        except OSError:
                            pass
                    return (src, True, "")
                tail = ""
                if proc.stderr:
                    tail = "\n".join(proc.stderr.strip().splitlines()[-3:])
                return (src, False, tail)
            except subprocess.TimeoutExpired:
                # Clean up the half-written .wav so a retry later actually retries
                try:
                    if dst.exists():
                        dst.unlink()
                except OSError:
                    pass
                return (src, False, f"Timeout apres {ffmpeg_timeout_s}s")
            except OSError as exc:
                return (src, False, f"Erreur ffmpeg: {exc}")

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
                self.last_download_metadata[key] = match.group(1).strip()

    def _update_download_progress(self) -> None:
        """Update the progress bar and counter label with current download progress."""
        if self.dl_progress_total > 0:
            # Switch to determinate mode for real progress
            self.progress.configure(mode="determinate")
            fraction = self.dl_progress_current / self.dl_progress_total
            self.progress.set(fraction)
            self.progress_counter.configure(
                text=f"{self.dl_progress_current}/{self.dl_progress_total} morceaux téléchargés"
            )
            if self.dl_progress_current > 0:
                self.status_label.configure(
                    text=f"Téléchargement en cours ({self.dl_progress_current}/{self.dl_progress_total})",
                    text_color="#1DB954"
                )

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

    def _ffmpeg_encode_args(self, dst: Path) -> list[str]:
        if self._get_conversion_format() == "mp3":
            return ["-c:a", "libmp3lame", "-b:a", "320k", str(dst)]
        return ["-c:a", "pcm_s16le", str(dst)]

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

    def _build_cli_args(self) -> list[str]:
        args = self._build_base_cli_args()
        query = self.query_entry.get().strip()

        if self.persist_var.get():
            args.append("--persist")

        if query:
            args.extend(query.split())
        args.extend(["--codec", "ogg"])

        return args

    def run_command(self) -> None:
        if self.current_process is not None:
            self._append_console("Un telechargement est deja en cours.\n")
            return

        self.current_action = "download"
        self.current_mode = "url"
        self.last_process_exit_code = None
        self.last_downloaded_path = None
        self.all_downloaded_paths = []
        self.last_download_metadata = {}
        self.batch_convert_stats = {"total": 0, "converted": 0, "failed": 0}
        self.dl_progress_current = 0
        self.dl_progress_total = 0
        self.conv_progress_current = 0
        self.conv_progress_total = 0
        self.failed_tracks = []
        self.progress.configure(mode="indeterminate")
        self.progress.set(0)
        self.progress_counter.configure(text="")
        command = self._build_cli_args()
        self._start_subprocess(command, "Execution en cours...")

    def _start_subprocess(self, command: list[str], status_text: str) -> None:
        self._append_console(f"\n$ {' '.join(command)}\n")
        self.status_label.configure(text=status_text, text_color="#22C55E")
        self.progress.start()
        self.run_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.nav_auth_button.configure(state="disabled")

        thread = threading.Thread(target=self._run_subprocess, args=(command,), daemon=True)
        thread.start()

    def login_spotify(self) -> None:
        if self.current_process is not None:
            self._append_console("Action impossible: un processus est deja en cours.\n")
            return
        self.pending_oauth_url = None
        self.oauth_url_opened = False
        self.login_flow_active = True
        self.login_success_detected = False
        self.current_action = "login"
        self.last_process_exit_code = None
        command = self._build_base_cli_args() + ["--login-only"]
        self._start_subprocess(command, "Connexion Spotify...")

    def logout_spotify(self) -> None:
        if self.current_process is not None:
            self._append_console("Action impossible: un processus est deja en cours.\n")
            return
        self.current_action = "logout"
        self.last_process_exit_code = None
        command = self._build_base_cli_args() + ["--logout"]
        self._start_subprocess(command, "Deconnexion Spotify...")

    def toggle_spotify_auth(self) -> None:
        if self._resolve_credentials_path().exists():
            self.logout_spotify()
        else:
            self.login_spotify()

    def stop_command(self) -> None:
        if self.current_process is not None and self.current_process.poll() is None:
            self.current_process.kill()  # kill() instead of terminate() to ensure child threads die
            try:
                self.current_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self._append_console("Arret force du processus.\n")

    def _run_subprocess(self, command: list[str]) -> None:
        try:
            # Force unbuffered output so subprocess prints reach the GUI immediately
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

            self.current_process = subprocess.Popen(command, **popen_kwargs)
            assert self.current_process.stdout is not None
            for line in self.current_process.stdout:
                self.output_queue.put(line)
            return_code = self.current_process.wait()
            self.output_queue.put(f"\nProcessus termine (code {return_code}).\n")
        except BaseException as exc:
            self.output_queue.put(f"\nErreur de lancement: {exc}\n")
        finally:
            self.current_process = None
            self.output_queue.put("__PROCESS_DONE__")

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
                if msg == "__PROCESS_DONE__":
                    self.progress.stop()
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
                    should_auto_convert = (
                        self.current_action == "download"
                        and self.last_process_exit_code == 0
                    )
                    if should_auto_convert:
                        fmt_label = self._conversion_label()
                        # Keep the UI in "busy" state during conversion so the user
                        # cannot launch a second download mid-conversion and the
                        # progress bar/status stay informative.
                        self.run_button.configure(state="disabled")
                        self.progress.configure(mode="determinate")
                        self.progress.set(0)
                        self.status_label.configure(
                            text=f"Preparation de la conversion {fmt_label}...",
                            text_color="#1DB954",
                        )
                        n_files = len(self.all_downloaded_paths)
                        if n_files >= 1:
                            self._append_console(
                                f"Telechargement termine ({n_files} fichier{'s' if n_files > 1 else ''}). "
                                f"Conversion {fmt_label} (sweep complet du dossier)...\n"
                            )
                            self._batch_convert()
                        else:
                            # No downloads captured this session, but the user might still have
                            # leftover .ogg files in the configured dir from a previous run.
                            self._append_console(
                                "Aucun fichier capture dans la session. Sweep du dossier de "
                                "telechargement pour rattraper les fichiers orphelins...\n"
                            )
                            self._batch_convert()
                    elif self.current_action == "download" and self.last_process_exit_code == 0:
                        self.show_page("Success")
                        self.current_action = "idle"
                        self.current_mode = ""
                    else:
                        self.current_action = "idle"
                        self.current_mode = ""
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
        """Kill any running subprocess before closing the window."""
        if self.current_process is not None and self.current_process.poll() is None:
            self.current_process.kill()
            try:
                self.current_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self.destroy()


def launch_gui() -> None:
    app = ZotifyGUI()
    app.mainloop()

