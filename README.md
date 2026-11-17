# NetGrab

**Site : https://ck526438-lgtm.github.io/netgrab/**

Application Android pour télécharger des vidéos en haute qualité à partir d'un lien
(YouTube, TikTok, Instagram, Facebook, X, Snapchat, Pinterest, Vimeo et 1000+ sites).
Moteur : [yt-dlp](https://github.com/yt-dlp/yt-dlp) + FFmpeg via
[youtubedl-android](https://github.com/yausername/youtubedl-android).

## Fonctions

- Coller un lien, ou **Partager > NetGrab** depuis n'importe quelle appli
- Qualité : meilleure dispo (4K/2K), 1080p, 720p, 480p
- Bouton dédié « Télécharger en MP3 » (audio seul, enregistré dans `Musique/NetGrab`)
- Fusion vidéo + audio en MP4, enregistrement dans `Films/NetGrab` (visible dans la galerie)
  et `Musique/NetGrab` pour l'audio
- Mise à jour automatique du moteur yt-dlp (1 fois par jour) + bouton manuel
- Annulation en cours de téléchargement

## Obtenir l'APK (sans PC)

1. Crée un dépôt GitHub et envoie-y ce dossier (branche `main`).
2. L'onglet **Actions** compile l'APK automatiquement (~5 min).
3. Télécharge `NetGrab.apk` depuis **Releases** et installe-le.

## Compiler en local

Android Studio (JDK 17) : ouvrir le dossier puis *Run*, ou `gradle assembleDebug` (Gradle 8.7).

## Limites connues (v1.0)

- Le téléchargement s'arrête si l'appli est fermée (pas encore de service en arrière-plan).
- Vidéos privées / avec connexion obligatoire non prises en charge.
- N'utilise l'appli que pour des contenus dont tu as les droits ou l'autorisation.

## Version web

Le dossier [`web/`](web/) contient NetGrab Web : la même chose dans le navigateur (PC, iPhone, Android),
avec son serveur à lancer en local ou à héberger. Voir [`web/README.md`](web/README.md).
