# Zotify Bridge — Extension navigateur

Ajoute un bouton **"Télécharger via Zotify"** sur Spotify Web Player
(`open.spotify.com`). Le bouton envoie l'URL de la page courante
(track / album / playlist) au GUI Zotify lancé en local.

## Prérequis

- Le **GUI Zotify** doit être ouvert sur ta machine. Il fait tourner
  un mini serveur HTTP local sur `127.0.0.1:43219` (ou 43220 / 43221
  en cas de conflit).
- Un navigateur basé Chromium (Chrome, Edge, Brave, Opera) ou Firefox.

## Installation Chrome / Edge / Brave / Opera

1. Ouvre `chrome://extensions` (ou `edge://extensions`, etc.).
2. Active le **Mode développeur** (interrupteur en haut à droite).
3. Clique **"Charger l'extension non empaquetée"**.
4. Sélectionne ce dossier (`browser_extension/`).
5. Épingle l'extension à la barre d'outils (icône puzzle → punaise).

## Installation Firefox

1. Ouvre `about:debugging#/runtime/this-firefox`.
2. Clique **"Charger un module complémentaire temporaire…"**.
3. Sélectionne le fichier `manifest.json` de ce dossier.
   - Note : Firefox réinstalle l'extension à chaque redémarrage.
     Pour la rendre permanente, package-la en `.xpi` signé.

## Utilisation

1. Lance le **GUI Zotify**.
2. Va sur `https://open.spotify.com/track/...` (ou `/album/...`, `/playlist/...`).
3. Un bouton vert **"↓ Télécharger via Zotify"** apparaît en bas à droite.
4. Clique → le GUI ouvre l'écran de téléchargement et démarre le DL.

Tu peux aussi cliquer l'icône de l'extension dans la barre d'outils
pour vérifier que la connexion au GUI est OK.

## Diagnostic

- **"Zotify GUI introuvable"** : le GUI n'est pas lancé, ou le pare-feu
  bloque les connexions loopback (rare).
- **Bouton invisible** : la page actuelle n'est pas une track / album /
  playlist. Navigue vers une de ces pages.
- **Console du navigateur** (`F12` → onglet Console) : les erreurs
  éventuelles y sont visibles.

## Sécurité

L'extension n'envoie **rien sur Internet**. Elle communique uniquement
avec `127.0.0.1` (loopback local). Aucun cookie Spotify n'est lu ; on
récupère juste l'URL de la page courante (`location.href`).
