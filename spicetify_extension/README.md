# Zotify Bridge — Extension Spicetify

Intègre Zotify directement dans l'**app desktop Spotify** via
[Spicetify](https://spicetify.app/). Ajoute :

- Un item **"Télécharger via Zotify"** dans le menu clic droit sur
  tracks, albums et playlists.
- Un raccourci clavier **`Ctrl+Shift+D`** qui télécharge le morceau
  actuellement en lecture.

## Prérequis

- Le **GUI Zotify** doit être ouvert (il fait tourner le serveur local
  sur `127.0.0.1:43219`).
- **Spicetify CLI** installé. Si tu ne l'as pas encore :

### Installer Spicetify (Windows PowerShell)

Ouvre PowerShell **en utilisateur normal** (pas admin) et lance :

```powershell
iwr -useb https://raw.githubusercontent.com/spicetify/cli/main/install.ps1 | iex
```

Puis active le mode développeur de Spicetify pour autoriser
l'injection custom :

```powershell
spicetify config-dir
```

(Note le chemin affiché, par ex. `C:\Users\Toi\AppData\Roaming\spicetify`.)

## Installation de l'extension Zotify

1. Copie le fichier `zotify.js` dans le dossier **Extensions** de Spicetify :

   ```powershell
   $ext = "$env:APPDATA\spicetify\Extensions"
   New-Item -ItemType Directory -Force -Path $ext | Out-Null
   Copy-Item .\zotify.js $ext\
   ```

2. Active l'extension :

   ```powershell
   spicetify config extensions zotify.js
   ```

3. Applique :

   ```powershell
   spicetify apply
   ```

   Spotify desktop redémarre automatiquement avec l'extension active.

## Mise à jour de l'extension

Si tu modifies `zotify.js`, recopie-le puis :

```powershell
spicetify apply
```

## Utilisation

1. Lance le **GUI Zotify**.
2. Dans Spotify Desktop :
   - **Clic droit** sur une track / album / playlist → "Télécharger via Zotify".
   - Ou écoute un morceau et appuie **`Ctrl+Shift+D`**.
3. Une notification Spotify confirme l'envoi, et le GUI Zotify démarre
   le téléchargement automatiquement.

## Désinstallation

```powershell
spicetify config extensions zotify.js-
spicetify apply
Remove-Item "$env:APPDATA\spicetify\Extensions\zotify.js"
```

## Diagnostic

- **"Zotify GUI non lancé"** : ouvre d'abord le GUI Zotify.
- **Item invisible dans le clic droit** : Spicetify n'a peut-être pas
  pris en compte l'extension. Refais `spicetify apply` et redémarre
  Spotify.
- **Notification "Echec"** : vérifie dans la console Spicetify
  (`Ctrl+Shift+I` → Console) les éventuelles erreurs.

## Compatibilité avec les mises à jour Spotify

Spicetify casse parfois à la suite d'une mise à jour majeure de
Spotify. Vérifie [le repo Spicetify](https://github.com/spicetify/cli)
et lance `spicetify upgrade` au besoin.
