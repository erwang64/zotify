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
GUI_VERSION: Final[str] = "1.0.0"


class ZotifyGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        self.title("Zotify GUI")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(900, 620)

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
        self.last_download_metadata: dict[str, str] = {}
        self.current_cover_image: ctk.CTkImage | None = None

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
        
        subtitle = ctk.CTkLabel(card, text="Configure les dossiers, le client API et l'authentification.", text_color="#B3B3B3")
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

        ctk.CTkLabel(card, text="Authentification manuelle (Alternative à OAuth)", font=ctk.CTkFont(weight="bold", size=14), text_color="#FFFFFF").grid(row=row_idx, column=0, padx=32, pady=(32, 8), sticky="w")
        row_idx += 1
        auth_frame = ctk.CTkFrame(card, fg_color="transparent")
        auth_frame.grid(row=row_idx, column=0, padx=32, pady=(0, 8), sticky="ew")
        auth_frame.grid_columnconfigure((0, 1), weight=1)
        row_idx += 1
        
        self.username_entry = ctk.CTkEntry(auth_frame, placeholder_text="Username", fg_color="#282828", border_width=0, height=40)
        self.username_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.token_entry = ctk.CTkEntry(auth_frame, placeholder_text="Token (login5)", show="*", fg_color="#282828", border_width=0, height=40)
        self.token_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        self.settings_info = ctk.CTkLabel(card, text="", text_color="#B3B3B3")
        self.settings_info.grid(row=row_idx, column=0, padx=32, pady=(24, 8), sticky="w")
        row_idx += 1
        
        save_btn = ctk.CTkButton(card, text="Sauvegarder", command=self.save_settings, fg_color="#FFFFFF", text_color="#000000", hover_color="#B3B3B3", font=ctk.CTkFont(weight="bold", size=15), height=48, corner_radius=24, width=200)
        save_btn.grid(row=row_idx, column=0, padx=32, pady=(0, 32), sticky="w")

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
        
        self.success_title = ctk.CTkLabel(self.success_header, text="Téléchargement & Conversion Terminés", font=ctk.CTkFont(size=24, weight="bold"), text_color="#1DB954")
        self.success_title.grid(row=1, column=0)

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

        buttons_frame = ctk.CTkFrame(self.success_card, fg_color="transparent")
        buttons_frame.grid(row=2, column=0, pady=(0, 40))

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
        
        title = self.last_download_metadata.get("title", "Titre Inconnu")
        artist = self.last_download_metadata.get("artist", "Artiste Inconnu")
        album = self.last_download_metadata.get("album", "Album Inconnu")
        
        self.success_track_title.configure(text=title)
        self.success_track_artist.configure(text=artist)
        self.success_track_album.configure(text=album)
        self.current_cover_image = None
        self.cover_label.configure(image=None, text="Pas de pochette")
        
        if self.last_downloaded_path and MUTAGEN_AVAILABLE:
            ogg_path = self.last_downloaded_path.with_suffix(".ogg")
            if not ogg_path.exists():
                ogg_path = self.last_downloaded_path
                
            if ogg_path.exists() and ogg_path.suffix.lower() == ".ogg":
                try:
                    pil_img = self._extract_cover_from_ogg(ogg_path)
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

        mode_label = ctk.CTkLabel(left_frame, text="Mode de recherche", font=ctk.CTkFont(weight="bold"), text_color="#B3B3B3")
        mode_label.grid(row=1, column=0, pady=(0, 4), sticky="w")
        
        self.mode_var = ctk.StringVar(value="URL(s)")
        self.mode_menu = ctk.CTkOptionMenu(
            left_frame,
            values=["URL(s)", "Fichier URLs", "Recherche", "Liked Songs", "Playlists utilisateur", "Artistes suivis", "Albums suivis", "Verifier librairie"],
            variable=self.mode_var,
            command=lambda _value: self._update_mode_hint(),
            fg_color="#282828", button_color="#3E3E3E", button_hover_color="#535353", dropdown_fg_color="#282828", height=36
        )
        self.mode_menu.grid(row=2, column=0, pady=(0, 4), sticky="ew")

        self.input_hint = ctk.CTkLabel(left_frame, text="Entrez une ou plusieurs URL separees par un espace", text_color="#B3B3B3", font=ctk.CTkFont(size=12), justify="left")
        self.input_hint.grid(row=3, column=0, pady=(0, 12), sticky="w")

        self.query_entry = ctk.CTkEntry(left_frame, placeholder_text="URL, recherche ou chemin de fichier", fg_color="#282828", border_width=0, height=40)
        self.query_entry.grid(row=4, column=0, pady=(0, 8), sticky="ew")

        browse_button = ctk.CTkButton(left_frame, text="Parcourir un fichier", command=self._browse_file, fg_color="transparent", hover_color="#3E3E3E", text_color="#FFFFFF", border_width=1, border_color="#B3B3B3", height=36)
        browse_button.grid(row=5, column=0, sticky="w")

        # --- Right Column ---
        right_frame = ctk.CTkFrame(content, fg_color="transparent")
        right_frame.grid(row=0, column=1, sticky="nsew")
        right_frame.grid_columnconfigure(0, weight=1)

        adv_label = ctk.CTkLabel(right_frame, text="Options avancées", font=ctk.CTkFont(weight="bold"), text_color="#FFFFFF")
        adv_label.grid(row=0, column=0, pady=(0, 12), sticky="w")

        self.persist_var = ctk.BooleanVar(value=False)
        self.debug_var = ctk.BooleanVar(value=False)
        self.no_splash_var = ctk.BooleanVar(value=True)
        
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
        self.progress.grid(row=1, column=0, columnspan=2, pady=(0, 12), sticky="ew")
        self.progress.set(0)

        self.run_button = ctk.CTkButton(actions_frame, text="Lancer", command=self.run_command, fg_color="#1DB954", text_color="#000000", hover_color="#1ED760", font=ctk.CTkFont(weight="bold", size=15), height=40, corner_radius=20)
        self.run_button.grid(row=2, column=0, padx=(0, 4), sticky="ew")
        
        self.stop_button = ctk.CTkButton(actions_frame, text="Arrêter", command=self.stop_command, fg_color="transparent", text_color="#FFFFFF", hover_color="#282828", border_width=1, border_color="#B3B3B3", font=ctk.CTkFont(weight="bold", size=15), height=40, corner_radius=20)
        self.stop_button.grid(row=2, column=1, padx=(4, 0), sticky="ew")
        self.stop_button.configure(state="disabled")

        self._update_mode_hint()
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

    def _convert_last_download_to_wav(self, delete_source_after_success: bool = True) -> None:
        src = self._resolve_last_downloaded_audio_path()
        if src is None or not src.exists():
            self._append_console("Conversion WAV impossible: fichier source introuvable.\n")
            self.output_queue.put("__ALL_DONE__")
            return
        if src.suffix.lower() == ".wav":
            self._append_console("Le fichier est deja en WAV.\n")
            self.output_queue.put("__ALL_DONE__")
            return
        if shutil.which("ffmpeg") is None:
            self._append_console("FFmpeg est introuvable. Installe-le ou ajoute-le au PATH.\n")
            self.output_queue.put("__ALL_DONE__")
            return

        dst = src.with_suffix(".wav")
        self._append_console(f"Conversion en WAV en cours: {src.name} -> {dst.name}\n")

        def worker() -> None:
            cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-c:a", "pcm_s16le", str(dst)]
            try:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
                if proc.returncode == 0:
                    if delete_source_after_success and src.suffix.lower() == ".ogg":
                        try:
                            src.unlink()
                            self.output_queue.put(f"Conversion WAV reussie: {dst} (source .ogg supprimee)\n")
                        except OSError as exc:
                            self.output_queue.put(f"Conversion WAV reussie: {dst} (suppression .ogg impossible: {exc})\n")
                    else:
                        self.output_queue.put(f"Conversion WAV reussie: {dst}\n")
                    self.output_queue.put("__ALL_DONE__")
                else:
                    self.output_queue.put("Echec conversion WAV (voir details ffmpeg ci-dessous).\n")
                    if proc.stdout:
                        self.output_queue.put(proc.stdout + "\n")
            except OSError as exc:
                self.output_queue.put(f"Impossible de lancer ffmpeg: {exc}\n")

        threading.Thread(target=worker, daemon=True).start()

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
        downloaded_match = re.search(r'(?:DOWNLOADED|SKIPPING):\s*"([^"]+)"', msg)
        if downloaded_match:
            raw_path = downloaded_match.group(1).strip()
            parsed = Path(raw_path).expanduser()
            if not parsed.is_absolute():
                root_raw = self.download_dir_entry.get().strip()
                if root_raw:
                    parsed = Path(root_raw).expanduser() / parsed
            self.last_downloaded_path = parsed

        patterns = {
            "title": r"Track Name ==\s*(.+)",
            "artist": r"Artist Name ==\s*(.+)",
            "album": r"Album Name ==\s*(.+)",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, msg)
            if match:
                self.last_download_metadata[key] = match.group(1).strip()

    def _browse_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choisir un fichier",
            filetypes=[("Text files", "*.txt"), ("JSON files", "*.json"), ("All files", "*.*")],
        )
        if selected:
            self.query_entry.delete(0, "end")
            self.query_entry.insert(0, selected)

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
        username = self.username_entry.get().strip()
        token = self.token_entry.get().strip()
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
        if username:
            args.extend(["--username", username])
        if token:
            args.extend(["--token", token])
        client_id = self.client_id_entry.get().strip()
        if client_id:
            args.extend(["--client-id", client_id])
        return args

    def _resolve_resource_path(self, *parts: str) -> Path:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        return base.joinpath(*parts)

    def _resolve_credentials_path(self) -> Path:
        """Resolve credentials path matching the CLI's Config.get_credentials_location() logic.
        
        The CLI determines the credentials path by:
        1. Reading CREDENTIALS_LOCATION from config.json
        2. If empty, using the platform default directory
        3. Appending 'credentials.json' if the path has no suffix
        
        The GUI must replicate this so the auth status display is accurate.
        """
        # Step 1: Determine config directory (same logic as Config.load)
        if hasattr(self, 'default_config_entry'):
            config_input = self.default_config_entry.get().strip()
        else:
            config_input = self.gui_settings.get("default_config_path", "")

        if config_input:
            config_dir_or_file = Path(config_input).expanduser()
        else:
            system_paths = {
                "win32": Path.home() / "AppData" / "Roaming" / "Zotify",
                "linux": Path.home() / ".config" / "zotify",
                "darwin": Path.home() / "Library" / "Application Support" / "Zotify",
            }
            config_dir_or_file = system_paths.get(sys.platform, Path.cwd() / ".zotify")
        config_json = config_dir_or_file if config_dir_or_file.suffix else config_dir_or_file / "config.json"

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

    def _load_gui_settings(self) -> dict[str, str]:
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

    def save_settings(self) -> None:
        self.gui_settings = {
            "download_dir": self.download_dir_entry.get().strip(),
            "podcast_dir": self.podcast_dir_entry.get().strip(),
            "default_config_path": self.default_config_entry.get().strip(),
            "api_client_id": self.client_id_entry.get().strip(),
        }
        try:
            with open(self.settings_path, "w", encoding="utf-8") as settings_file:
                json.dump(self.gui_settings, settings_file, indent=2)
            self.settings_info.configure(text="Paramètres sauvegardés.", text_color="#1DB954")
            self._append_console(f"Paramètres sauvegardés : {self.settings_path}\n")
        except OSError as exc:
            self.settings_info.configure(text=f"Erreur sauvegarde : {exc}", text_color="#E22134")

    def _update_mode_hint(self) -> None:
        hints = {
            "URL(s)": "Entrez une ou plusieurs URL separees par un espace",
            "Fichier URLs": "Selectionnez un fichier .txt avec des URL",
            "Recherche": "Entrez une recherche Spotify (ex: Daft Punk /t album)",
            "Liked Songs": "Telecharge vos titres likes",
            "Playlists utilisateur": "Telecharge vos playlists sauvegardees",
            "Artistes suivis": "Telecharge vos artistes suivis",
            "Albums suivis": "Telecharge vos albums suivis",
            "Verifier librairie": "Verifie et corrige les metadonnees locales",
        }
        self.input_hint.configure(text=hints.get(self.mode_var.get(), ""))

    def _build_cli_args(self) -> list[str]:
        args = self._build_base_cli_args()
        mode = self.mode_var.get()
        query = self.query_entry.get().strip()

        if self.persist_var.get():
            args.append("--persist")

        if mode == "URL(s)" and query:
            args.extend(query.split())
        elif mode == "Fichier URLs":
            if query:
                args.extend(["--file", query])
        elif mode == "Recherche":
            args.extend(["--search", query if query else " "])
        elif mode == "Liked Songs":
            args.append("--liked")
        elif mode == "Playlists utilisateur":
            args.append("--playlist")
        elif mode == "Artistes suivis":
            args.append("--artists")
        elif mode == "Albums suivis":
            args.append("--albums")
        elif mode == "Verifier librairie":
            args.append("--verify-library")

        download_modes = {
            "URL(s)",
            "Fichier URLs",
            "Recherche",
            "Liked Songs",
            "Playlists utilisateur",
            "Artistes suivis",
            "Albums suivis",
        }
        if mode in download_modes:
            args.extend(["--codec", "ogg"])

        return args

    def run_command(self) -> None:
        if self.current_process is not None:
            self._append_console("Un telechargement est deja en cours.\n")
            return

        self.current_action = "download"
        self.current_mode = self.mode_var.get()
        self.last_process_exit_code = None
        self.last_downloaded_path = None
        self.last_download_metadata = {}
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
                    should_auto_convert_to_wav = (
                        self.current_action == "download"
                        and self.last_process_exit_code == 0
                        and self.current_mode != "Verifier librairie"
                    )
                    if should_auto_convert_to_wav:
                        self._append_console("Telechargement termine. Conversion automatique en WAV...\n")
                        self._convert_last_download_to_wav(delete_source_after_success=False)
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

