# Zotify — GUI 1.2.1

<p align="center">
  <img src="https://i.imgur.com/hGXQWSl.png" width="40%" alt="Zotify logo">
</p>

**Zotify** est un téléchargeur de musique et podcasts Spotify hautement personnalisable.  
Cette version met l’accent sur une **interface graphique moderne**, une **conversion audio de haute qualité**, et un **pont direct depuis Spotify** (navigateur ou app desktop) sans copier-coller d’URL.

| Version GUI | Extension navigateur | Pont local |
|-------------|-------------------|------------|
| **1.2.1**   | Zotify Bridge 1.2.1 | `127.0.0.1:43219` |

Voir le détail des changements dans [CHANGELOG.md](CHANGELOG.md).

---

## Fonctionnalités (1.2.1)

- Interface graphique sombre (CustomTkinter) avec console en direct
- Téléchargement depuis une **URL**, une **recherche**, les **favoris**, **playlists**, **albums**, **artistes**
- **Téléchargements parallèles** : plusieurs morceaux / URLs en même temps, avec file d’attente
- **Téléchargement en un clic** depuis Spotify Web (extension navigateur)
- **Téléchargement depuis l’app desktop** via Spicetify (menu contextuel + `Ctrl+Shift+D`)
- Conversion automatique **WAV 24 bits** ou **MP3 320 kbps** avec métadonnées et pochette
- Page de succès adaptée (1 morceau, playlist, ou batch parallèle)

---

## Prérequis

- **Python 3.10+**
- **FFmpeg** installé et accessible dans le `PATH`
- Compte Spotify (Premium recommandé pour la qualité maximale)

---

## Installation

### Module Python (recommandé)

```bash
python -m pip install -e .
```

Depuis le dépôt cloné, ou :

```bash
python -m pip install git+https://github.com/Googolplexed0/zotify.git
```

### Exécutable global (pipx)

```bash
pipx install git+https://github.com/Googolplexed0/zotify.git
```

Instructions détaillées : [INSTALLATION.md](INSTALLATION.md).

---

## Lancer l’interface graphique

```bash
zotify-gui
```

ou :

```bash
python -m zotify --gui
```

Au démarrage, le GUI affiche **v1.2.1** en bas de la barre latérale et démarre automatiquement le **serveur pont** sur `http://127.0.0.1:43219` (nécessaire pour les extensions).

> **Important** : garde le GUI ouvert pendant que tu utilises l’extension navigateur ou Spicetify.

---

## Utilisation rapide (GUI)

1. Lance **`zotify-gui`** et connecte ton compte Spotify (onglet Authentification).
2. Va dans **Téléchargement**, colle une URL Spotify (track, album, playlist) ou utilise le bouton depuis le navigateur (voir ci-dessous).
3. Choisis le format de sortie dans **Paramètres** : **WAV** (24 bits) ou **MP3** (320 kbps).
4. Clique **Lancer** — tu peux cliquer plusieurs fois sur « Télécharger » depuis Spotify : les jobs s’empilent et tournent en parallèle (dans la limite configurée).
5. À la fin : page **Succès** avec le résumé (1 morceau, playlist, ou « X/Y téléchargements terminés »).

---

## Extension navigateur — Zotify Bridge

Permet de télécharger **directement depuis Spotify Web** (`open.spotify.com`) sans copier l’URL dans le GUI.

### Prérequis

- Le **GUI Zotify** doit être **lancé** sur la même machine.
- Navigateur **Chrome**, **Edge**, **Brave**, **Opera** ou **Firefox**.

### Installation (Chrome / Edge / Brave / Opera)

1. Ouvre la page des extensions :
   - Chrome : `chrome://extensions`
   - Edge : `edge://extensions`
   - Brave : `brave://extensions`
2. Active le **Mode développeur** (interrupteur en haut à droite).
3. Clique **Charger l’extension non empaquetée** (ou équivalent).
4. Sélectionne le dossier **`browser_extension/`** à la racine de ce dépôt.
5. Épingle l’extension (icône puzzle → punaise) pour un accès rapide.

### Installation (Firefox)

1. Ouvre `about:debugging#/runtime/this-firefox`.
2. **Charger un module complémentaire temporaire…**
3. Choisis le fichier **`browser_extension/manifest.json`**.

> Sur Firefox, l’extension en mode développeur est **temporaire** (à recharger après chaque redémarrage du navigateur), sauf si tu la packages en `.xpi` signé.

### Utilisation

1. Lance **`zotify-gui`** et vérifie dans la console qu’un message du type  
   `[Bridge] Serveur local actif sur http://127.0.0.1:43219` apparaît.
2. Ouvre Spotify dans le navigateur :  
   `https://open.spotify.com/track/...`  
   ou `/album/...`, `/playlist/...`
3. Un bouton vert **« Zotify »** (ou **« Télécharger via Zotify »** en mode flottant) apparaît :
   - **Inline** : dans la barre d’actions, à côté du bouton Lecture (cas le plus courant).
   - **Flottant** : en haut à droite si Spotify a changé sa mise en page et l’injection inline échoue.
4. Clique le bouton → un toast confirme *« Téléchargement lancé dans Zotify »* et le GUI passe à l’écran de téléchargement.
5. Tu peux enchaîner plusieurs morceaux : chaque clic ajoute un job (parallèle + file).

**Popup de l’extension** (clic sur l’icône) : état de connexion au GUI, nombre de téléchargements **actifs** et **en file**.

### Dépannage extension

| Problème | Solution |
|----------|----------|
| *Zotify GUI introuvable* | Lance `zotify-gui` ; vérifie qu’aucun pare-feu ne bloque `127.0.0.1`. |
| Bouton invisible | Ouvre une page **track**, **album** ou **playlist** (pas la page d’accueil ni un artiste seul). |
| Téléchargement ignoré | Le GUI peut être occupé par une **authentification** en cours — termine le login d’abord. |
| Erreurs techniques | `F12` → Console ; logs côté GUI dans la console intégrée. |

Documentation complémentaire : [browser_extension/README.md](browser_extension/README.md).

### Sécurité

L’extension **n’envoie rien sur Internet**. Elle lit uniquement l’URL de la page (`location.href`) et appelle le serveur local sur **`127.0.0.1`**. Aucun cookie Spotify n’est lu.

---

## Extension Spotify Desktop (Spicetify)

Pour l’**application Spotify Windows / macOS / Linux** avec [Spicetify](https://spicetify.app/) :

- Menu contextuel **« Télécharger via Zotify »** sur tracks, albums, playlists
- Raccourci **`Ctrl+Shift+D`** sur le morceau en cours de lecture

Guide d’installation : [spicetify_extension/README.md](spicetify_extension/README.md).

---

## Formats et métadonnées

| Format | Codec / qualité | Métadonnées |
|--------|-----------------|-------------|
| **MP3** | LAME 320 kbps, `compression_level 0` | ID3v2.3 + pochette APIC |
| **WAV** | PCM 24 bits | ID3 dans le fichier + chunk RIFF `LIST/INFO` |

**Windows Explorer** : l’explorateur Windows affiche rarement les tags WAV nativement ; les lecteurs comme **foobar2000**, **Rekordbox** ou **VLC** les lisent correctement.  
Vérifier un fichier : `python check_wav_tags.py chemin/vers/fichier.wav`

---

## Ligne de commande (CLI)

Le CLI reste disponible pour les usages avancés :

```bash
zotify <url_spotify>
zotify --help
zotify --version
```

| Commande | Description |
|----------|-------------|
| `zotify --gui` | Lance l’interface graphique |
| `zotify-gui` | Raccourci vers le GUI |
| `zotify -l` | Morceaux likés |
| `zotify -s "artiste"` | Recherche interactive |

Configuration : fichier `config.json` (emplacement selon l’OS — voir section Configuration dans la doc historique du projet).  
Guide avancé des options CLI : parcourir les sections *Usage*, *Configuration* et *Flags* du dépôt d’origine ou [INSTALLATION.md](INSTALLATION.md).

---

## Structure du projet

```
zotify/                 # Code Python (GUI, CLI, serveur pont)
browser_extension/      # Extension Chrome / Firefox (Zotify Bridge)
spicetify_extension/    # Extension Spicetify (app desktop)
check_wav_tags.py       # Diagnostic métadonnées WAV
CHANGELOG.md            # Journal des versions GUI
```

---

## Changelog

Voir **[CHANGELOG.md](CHANGELOG.md)** pour l’historique complet (1.2.1, 1.1.0, …).

---

## Avertissement

Zotify est destiné à un usage conforme au droit d’auteur, à des fins **éducatives**, **privées** et de **fair use**.  
Les contributeurs ne sont pas responsables d’une utilisation abusive. Utilise un compte dédié et des volumes de téléchargement raisonnables.

## Contribution

Voir [CONTRIBUTING.md](CONTRIBUTING.md).
