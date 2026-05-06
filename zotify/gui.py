from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from queue import Empty, Queue
from typing import Final

import customtkinter as ctk
from PIL import Image
from tkinter import filedialog, Menu


WINDOW_WIDTH: Final[int] = 1024
WINDOW_HEIGHT: Final[int] = 700


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

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_main_layout()
        self.after(100, self._drain_output_queue)

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, corner_radius=0, fg_color="#111319")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        logo_path = Path(__file__).resolve().parents[1] / "logo" / "logo.png"
        self.logo_image = None
        if logo_path.exists():
            pil_img = Image.open(logo_path)
            self.logo_image = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(280, 90))
            logo_label = ctk.CTkLabel(header, image=self.logo_image, text="")
            logo_label.grid(row=0, column=0, padx=18, pady=14, sticky="w")
        else:
            title = ctk.CTkLabel(
                header,
                text="Zotify",
                font=ctk.CTkFont(size=40, weight="bold"),
                text_color="#25D366",
            )
            title.grid(row=0, column=0, padx=20, pady=14, sticky="w")

        subtitle = ctk.CTkLabel(
            header,
            text="Interface moderne pour telecharger musique et podcasts",
            text_color="#BCC4D0",
            font=ctk.CTkFont(size=14),
        )
        subtitle.grid(row=0, column=1, padx=(0, 20), pady=14, sticky="w")

    def _build_main_layout(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=1, column=0, sticky="nsew", padx=16, pady=16)
        main.grid_columnconfigure(0, weight=0, minsize=200)
        main.grid_columnconfigure(1, weight=1)
        main.grid_rowconfigure(0, weight=1)

        self._build_navbar(main)
        self._build_pages(main)

    def _build_navbar(self, parent: ctk.CTkFrame) -> None:
        nav = ctk.CTkFrame(parent, width=190)
        nav.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        nav.grid_propagate(False)
        nav.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(nav, text="Navigation", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, padx=14, pady=(16, 12), sticky="w"
        )

        self.home_nav_btn = ctk.CTkButton(nav, text="Accueil", command=lambda: self.show_page("Accueil"))
        self.home_nav_btn.grid(row=1, column=0, padx=14, pady=(0, 8), sticky="ew")

        self.settings_nav_btn = ctk.CTkButton(nav, text="Settings", command=lambda: self.show_page("Settings"))
        self.settings_nav_btn.grid(row=2, column=0, padx=14, pady=(0, 8), sticky="ew")

        self.nav_auth_button = ctk.CTkButton(nav, text="Se connecter", command=self.toggle_spotify_auth)
        self.nav_auth_button.grid(row=3, column=0, padx=14, pady=(6, 8), sticky="ew")

        self.nav_auth_status = ctk.CTkLabel(nav, text="Spotify: deconnecte", text_color="#9AA6B2")
        self.nav_auth_status.grid(row=4, column=0, padx=14, pady=(0, 8), sticky="w")

        self._sync_nav_style()

    def _build_pages(self, parent: ctk.CTkFrame) -> None:
        self.pages_container = ctk.CTkFrame(parent)
        self.pages_container.grid(row=0, column=1, sticky="nsew")
        self.pages_container.grid_columnconfigure(0, weight=1)
        self.pages_container.grid_rowconfigure(0, weight=1)

        self.home_page = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        self.home_page.grid(row=0, column=0, sticky="nsew")
        self.home_page.grid_columnconfigure(0, weight=0)
        self.home_page.grid_columnconfigure(1, weight=1)
        self.home_page.grid_rowconfigure(0, weight=1)
        self._build_left_controls(self.home_page)
        self._build_output_panel(self.home_page)

        self.settings_page = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        self.settings_page.grid(row=0, column=0, sticky="nsew")
        self.settings_page.grid_columnconfigure(0, weight=1)
        self._build_settings_page(self.settings_page)

        self.show_page("Accueil")

    def _build_settings_page(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(parent)
        card.grid(row=0, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="Settings", font=ctk.CTkFont(size=24, weight="bold")).grid(
            row=0, column=0, padx=16, pady=(16, 8), sticky="w"
        )
        ctk.CTkLabel(
            card,
            text="Configure les parametres de l'app et les dossiers de telechargement.",
            text_color="#9AA6B2",
        ).grid(row=1, column=0, padx=16, pady=(0, 16), sticky="w")

        ctk.CTkLabel(card, text="Dossier musique (ROOT_PATH)").grid(row=2, column=0, padx=16, pady=(0, 4), sticky="w")
        music_row = ctk.CTkFrame(card, fg_color="transparent")
        music_row.grid(row=3, column=0, padx=16, pady=(0, 10), sticky="ew")
        music_row.grid_columnconfigure(0, weight=1)
        self.download_dir_entry = ctk.CTkEntry(music_row, placeholder_text="Ex: D:/Music/Zotify")
        self.download_dir_entry.grid(row=0, column=0, padx=(0, 8), sticky="ew")
        self.download_dir_entry.insert(0, self.gui_settings.get("download_dir", ""))
        ctk.CTkButton(music_row, text="Parcourir", width=120, command=lambda: self._browse_directory(self.download_dir_entry)).grid(
            row=0, column=1, sticky="e"
        )

        ctk.CTkLabel(card, text="Dossier podcasts (ROOT_PODCAST_PATH)").grid(row=4, column=0, padx=16, pady=(0, 4), sticky="w")
        pod_row = ctk.CTkFrame(card, fg_color="transparent")
        pod_row.grid(row=5, column=0, padx=16, pady=(0, 12), sticky="ew")
        pod_row.grid_columnconfigure(0, weight=1)
        self.podcast_dir_entry = ctk.CTkEntry(pod_row, placeholder_text="Ex: D:/Music/Zotify Podcasts")
        self.podcast_dir_entry.grid(row=0, column=0, padx=(0, 8), sticky="ew")
        self.podcast_dir_entry.insert(0, self.gui_settings.get("podcast_dir", ""))
        ctk.CTkButton(pod_row, text="Parcourir", width=120, command=lambda: self._browse_directory(self.podcast_dir_entry)).grid(
            row=0, column=1, sticky="e"
        )

        ctk.CTkLabel(card, text="Config par defaut (optionnel)").grid(row=6, column=0, padx=16, pady=(0, 4), sticky="w")
        cfg_row = ctk.CTkFrame(card, fg_color="transparent")
        cfg_row.grid(row=7, column=0, padx=16, pady=(0, 16), sticky="ew")
        cfg_row.grid_columnconfigure(0, weight=1)
        self.default_config_entry = ctk.CTkEntry(cfg_row, placeholder_text="Dossier ou fichier config.json")
        self.default_config_entry.grid(row=0, column=0, padx=(0, 8), sticky="ew")
        self.default_config_entry.insert(0, self.gui_settings.get("default_config_path", ""))
        ctk.CTkButton(cfg_row, text="Parcourir", width=120, command=self._browse_default_config).grid(row=0, column=1, sticky="e")

        ctk.CTkLabel(card, text="Client ID API Spotify (optionnel)").grid(row=8, column=0, padx=16, pady=(0, 4), sticky="w")
        self.client_id_entry = ctk.CTkEntry(card, placeholder_text="Laisser vide pour le client interne")
        self.client_id_entry.grid(row=9, column=0, padx=16, pady=(0, 16), sticky="ew")
        self.client_id_entry.insert(0, self.gui_settings.get("api_client_id", ""))

        self.settings_info = ctk.CTkLabel(card, text="", text_color="#9AA6B2")
        self.settings_info.grid(row=10, column=0, padx=16, pady=(0, 10), sticky="w")
        ctk.CTkButton(card, text="Sauvegarder les settings", command=self.save_settings).grid(
            row=11, column=0, padx=16, pady=(0, 16), sticky="w"
        )

    def show_page(self, page: str) -> None:
        self.current_page = page
        if page == "Accueil":
            self.home_page.tkraise()
        else:
            self.settings_page.tkraise()
        self._sync_nav_style()

    def _sync_nav_style(self) -> None:
        selected_color = "#16A34A"
        default_color = "#1F2937"
        self.home_nav_btn.configure(fg_color=selected_color if self.current_page == "Accueil" else default_color)
        self.settings_nav_btn.configure(fg_color=selected_color if self.current_page == "Settings" else default_color)

    def _build_left_controls(self, parent: ctk.CTkFrame) -> None:
        panel = ctk.CTkFrame(parent, width=350)
        panel.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(panel, text="Commandes", font=ctk.CTkFont(size=20, weight="bold"))
        title.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="w")

        mode_label = ctk.CTkLabel(panel, text="Mode")
        mode_label.grid(row=1, column=0, padx=16, pady=(8, 4), sticky="w")
        self.mode_var = ctk.StringVar(value="URL(s)")
        self.mode_menu = ctk.CTkOptionMenu(
            panel,
            values=[
                "URL(s)",
                "Fichier URLs",
                "Recherche",
                "Liked Songs",
                "Playlists utilisateur",
                "Artistes suivis",
                "Albums suivis",
                "Verifier librairie",
            ],
            variable=self.mode_var,
            command=lambda _value: self._update_mode_hint(),
        )
        self.mode_menu.grid(row=2, column=0, padx=16, pady=(0, 10), sticky="ew")

        self.input_hint = ctk.CTkLabel(panel, text="Entrez une ou plusieurs URL separees par un espace")
        self.input_hint.grid(row=3, column=0, padx=16, pady=(0, 4), sticky="w")

        self.query_entry = ctk.CTkEntry(panel, placeholder_text="URL, recherche ou chemin de fichier")
        self.query_entry.grid(row=4, column=0, padx=16, pady=(0, 8), sticky="ew")

        browse_button = ctk.CTkButton(panel, text="Parcourir un fichier", command=self._browse_file)
        browse_button.grid(row=5, column=0, padx=16, pady=(0, 10), sticky="ew")

        config_label = ctk.CTkLabel(panel, text="Config optionnelle (.json ou dossier)")
        config_label.grid(row=6, column=0, padx=16, pady=(0, 4), sticky="w")
        self.config_entry = ctk.CTkEntry(panel, placeholder_text="Chemin de config")
        self.config_entry.grid(row=7, column=0, padx=16, pady=(0, 8), sticky="ew")
        saved_config = self.gui_settings.get("default_config_path", "")
        if saved_config:
            self.config_entry.insert(0, saved_config)

        creds_frame = ctk.CTkFrame(panel, fg_color="transparent")
        creds_frame.grid(row=8, column=0, padx=16, pady=(0, 8), sticky="ew")
        creds_frame.grid_columnconfigure((0, 1), weight=1)
        self.username_entry = ctk.CTkEntry(creds_frame, placeholder_text="Username (optionnel)")
        self.username_entry.grid(row=0, column=0, padx=(0, 6), sticky="ew")
        self.token_entry = ctk.CTkEntry(creds_frame, placeholder_text="Token (optionnel)", show="*")
        self.token_entry.grid(row=0, column=1, padx=(6, 0), sticky="ew")

        self.persist_var = ctk.BooleanVar(value=False)
        self.debug_var = ctk.BooleanVar(value=False)
        self.no_splash_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(panel, text="Session persistante (--persist)", variable=self.persist_var).grid(
            row=9, column=0, padx=16, pady=(4, 0), sticky="w"
        )
        ctk.CTkCheckBox(panel, text="Mode debug (--debug)", variable=self.debug_var).grid(
            row=10, column=0, padx=16, pady=(2, 0), sticky="w"
        )
        ctk.CTkCheckBox(panel, text="Masquer splash (--no-splash)", variable=self.no_splash_var).grid(
            row=11, column=0, padx=16, pady=(2, 10), sticky="w"
        )

        self.status_label = ctk.CTkLabel(panel, text="Pret", text_color="#9AA6B2")
        self.status_label.grid(row=12, column=0, padx=16, pady=(0, 8), sticky="w")

        self.progress = ctk.CTkProgressBar(panel, mode="indeterminate")
        self.progress.grid(row=13, column=0, padx=16, pady=(0, 10), sticky="ew")
        self.progress.set(0)

        actions = ctk.CTkFrame(panel, fg_color="transparent")
        actions.grid(row=14, column=0, padx=16, pady=(0, 16), sticky="ew")
        actions.grid_columnconfigure((0, 1), weight=1)
        self.run_button = ctk.CTkButton(actions, text="Lancer", command=self.run_command)
        self.run_button.grid(row=0, column=0, padx=(0, 6), sticky="ew")
        self.stop_button = ctk.CTkButton(actions, text="Arreter", fg_color="#B91C1C", hover_color="#991B1B", command=self.stop_command)
        self.stop_button.grid(row=0, column=1, padx=(6, 0), sticky="ew")
        self.stop_button.configure(state="disabled")

        self._update_mode_hint()
        self._refresh_auth_status()

    def _build_output_panel(self, parent: ctk.CTkFrame) -> None:
        output_panel = ctk.CTkFrame(parent)
        output_panel.grid(row=0, column=1, sticky="nsew")
        output_panel.grid_columnconfigure(0, weight=1)
        output_panel.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(output_panel, text="Console", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, padx=16, pady=(16, 8), sticky="w"
        )

        self.console = ctk.CTkTextbox(output_panel, wrap="word", font=("Consolas", 13))
        self.console.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="nsew")
        self.console.insert("end", "Bienvenue dans Zotify GUI.\n")
        self._setup_console_clipboard()

        clear_button = ctk.CTkButton(output_panel, text="Effacer la console", command=self._clear_console)
        clear_button.grid(row=2, column=0, padx=16, pady=(0, 16), sticky="e")

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
        args = [sys.executable, "-m", "zotify"]
        config_path = self.config_entry.get().strip()
        username = self.username_entry.get().strip()
        token = self.token_entry.get().strip()
        download_dir = self.download_dir_entry.get().strip()
        podcast_dir = self.podcast_dir_entry.get().strip()

        if self.debug_var.get():
            args.append("--debug")
        if self.no_splash_var.get():
            args.append("--no-splash")
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

    def _resolve_credentials_path(self) -> Path:
        config_path = self.config_entry.get().strip()
        if config_path:
            cfg_path = Path(config_path).expanduser()
            base_dir = cfg_path.parent if cfg_path.suffix else cfg_path
            return base_dir / "credentials.json"
        if sys.platform == "win32":
            appdata = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
            return appdata / "Zotify" / "credentials.json"
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "Zotify" / "credentials.json"
        return Path.home() / ".local" / "share" / "zotify" / "credentials.json"

    def _refresh_auth_status(self) -> None:
        cred_path = self._resolve_credentials_path()
        if cred_path.exists():
            self.nav_auth_status.configure(text="Spotify: connecte", text_color="#22C55E")
            self.nav_auth_button.configure(text="Se deconnecter", fg_color="#374151", hover_color="#1F2937")
        else:
            self.nav_auth_status.configure(text="Spotify: deconnecte", text_color="#9AA6B2")
            self.nav_auth_button.configure(text="Se connecter", fg_color="#16A34A", hover_color="#15803D")

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
            self.settings_info.configure(text="Settings sauvegardes.", text_color="#22C55E")
            self.config_entry.delete(0, "end")
            if self.gui_settings["default_config_path"]:
                self.config_entry.insert(0, self.gui_settings["default_config_path"])
            self._append_console(f"Settings sauvegardes: {self.settings_path}\n")
        except OSError as exc:
            self.settings_info.configure(text=f"Erreur sauvegarde: {exc}", text_color="#EF4444")

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

        return args

    def run_command(self) -> None:
        if self.current_process is not None:
            self._append_console("Un telechargement est deja en cours.\n")
            return

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
        command = self._build_base_cli_args() + ["--login-only"]
        self._start_subprocess(command, "Connexion Spotify...")

    def logout_spotify(self) -> None:
        if self.current_process is not None:
            self._append_console("Action impossible: un processus est deja en cours.\n")
            return
        command = self._build_base_cli_args() + ["--logout"]
        self._start_subprocess(command, "Deconnexion Spotify...")

    def toggle_spotify_auth(self) -> None:
        if self._resolve_credentials_path().exists():
            self.logout_spotify()
        else:
            self.login_spotify()

    def stop_command(self) -> None:
        if self.current_process is not None and self.current_process.poll() is None:
            self.current_process.terminate()
            self._append_console("Arret demande...\n")

    def _run_subprocess(self, command: list[str]) -> None:
        try:
            self.current_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
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
                            self.show_page("Accueil")
                else:
                    maybe_url = self._try_extract_login_url(msg)
                    if maybe_url and self.pending_oauth_url != maybe_url:
                        self.pending_oauth_url = maybe_url
                        self._open_oauth_url(maybe_url)
                    if self.login_flow_active and "received callback" in msg.lower():
                        self.login_success_detected = True
                        self._append_console("Callback Spotify recu, finalisation de la connexion...\n")
                    noisy_login_line = self.login_flow_active and (
                        "logging in..." in msg.lower()
                        or msg.strip().startswith("[...")
                        or msg.strip().startswith("[>..]")
                        or msg.strip().startswith("[.>.]")
                        or msg.strip().startswith("[..>]")
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


def launch_gui() -> None:
    app = ZotifyGUI()
    app.mainloop()

