# Zotify Bridge — Extension navigateur (v1.2.1)

Ajoute un bouton **« Télécharger via Zotify »** sur [Spotify Web Player](https://open.spotify.com).  
L’URL de la page courante (track, album ou playlist) est envoyée au **GUI Zotify** via un serveur HTTP local.

> Tutoriel complet (installation pas à pas, dépannage, utilisation) : voir la section **Extension navigateur** du [README principal](../README.md).

## Prérequis

- Le **GUI Zotify** ouvert (`zotify-gui`) — serveur sur `127.0.0.1:43219` (ou `43220` / `43221`).
- Chrome, Edge, Brave, Opera ou Firefox.

## Installation rapide

**Chromium** : `chrome://extensions` → Mode développeur → *Charger l’extension non empaquetée* → dossier `browser_extension/`.

**Firefox** : `about:debugging` → *Charger un module temporaire* → `manifest.json`.

## Utilisation

1. Page Spotify : `/track/`, `/album/` ou `/playlist/`.
2. Bouton **Zotify** (barre d’actions, à côté de Lecture) ou bouton flottant en secours.
3. Toast de confirmation → le GUI démarre le téléchargement (jobs parallèles supportés).

## Sécurité

Communication **uniquement** en loopback (`127.0.0.1`). Aucune requête externe ; seule l’URL de la page est lue.
