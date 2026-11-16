# NetGrab Web

Version web de NetGrab : on colle un lien, on récupère la vidéo (meilleure qualité, 1080p, 720p, 480p)
ou le son en MP3. Serveur Python (FastAPI + yt-dlp + FFmpeg), interface en une page.

## Lancer sur son PC (ou Termux)

```bash
cd web
pip install -r requirements.txt      # FFmpeg doit aussi être installé
uvicorn app:app --host 0.0.0.0 --port 8000
```

Puis ouvrir http://localhost:8000 (ou http://IP-du-PC:8000 depuis un téléphone sur le même Wi-Fi).

## Mettre en ligne

Tout hébergeur qui accepte Docker convient (Render, Railway, Koyeb, un VPS…).

- **Render** : New > Blueprint > ce dépôt (le fichier `render.yaml` est à la racine), puis saisir `NETGRAB_PASSWORD`.
- **VPS** : `docker build -t netgrab web && docker run -d -p 80:8000 -e NETGRAB_PASSWORD=... netgrab`

## Réglages (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `NETGRAB_PASSWORD` | vide | Mot de passe d'accès au site. Vide = site ouvert à tous. |
| `NETGRAB_MAX_JOBS` | 2 | Téléchargements simultanés. |
| `NETGRAB_TTL_MIN` | 30 | Minutes avant effacement des fichiers du serveur. |
| `NETGRAB_MAX_DURATION_MIN` | 0 | Durée maximale d'une vidéo (0 = sans limite). |
| `NETGRAB_RATE_LIMIT` | 12 | Téléchargements par adresse IP sur 10 minutes. |

## À savoir

- Certains sites (YouTube surtout) bloquent souvent les serveurs d'hébergeurs : une vidéo peut marcher
  dans l'appli Android ou sur ton PC et échouer sur un serveur en ligne.
- Un hébergement gratuit a peu de mémoire et de bande passante : garde un mot de passe et une limite de durée.
- Le serveur refuse les liens vers des adresses internes (localhost, réseau privé).
- N'utilise NetGrab que pour des contenus dont tu as les droits ou l'autorisation.
