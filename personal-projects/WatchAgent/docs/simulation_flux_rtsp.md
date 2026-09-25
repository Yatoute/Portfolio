
# 📡 Simulation d’un flux RTSP avec FFmpeg + MediaMTX

Ce guide permet de simuler une caméra de surveillance RTSP à partir d’un fichier vidéo local.

---

## 🎯 Objectif

Créer un flux RTSP local utilisable par un système d’analyse vidéo (ex: Smart Surveillance Agent).

---

## 🧱 Architecture

```

video.mp4
↓
FFmpeg (streaming en boucle)
↓
MediaMTX (serveur RTSP)
↓
rtsp://localhost:8554/live.stream
↓
Client (OpenCV, ffplay, etc.)

````

---

## ⚙️ Prérequis

- Docker installé
- FFmpeg installé
- Un fichier vidéo (`video.mp4`)

---

## 🚀 Étapes

### 1. Lancer le serveur RTSP (MediaMTX)

```bash
docker run --rm -it -p 8554:8554 bluenviron/mediamtx
````

---

### 2. Publier un flux RTSP avec FFmpeg

```bash
ffmpeg -re -stream_loop -1 -i video.mp4 -an -c:v copy -rtsp_transport tcp -f rtsp rtsp://localhost:8554/live.stream
```

### 📌 Explication rapide

* `-re` → lit la vidéo en temps réel
* `-stream_loop -1` → boucle infinie
* `-i video.mp4` → source vidéo
* `-an` → supprime l’audio
* `-c:v copy` → copie le flux vidéo sans réencodage
* `-rtsp_transport tcp` → transport RTSP fiable
* `-f rtsp` → sortie en RTSP

### Cas de video source désentrelacé

Il faut désentrelacer puis réencoder avant d’envoyer en RTSP.

```bash
ffmpeg -re -i 09152008flight2tape3_2.mpg \
  -map 0:v:0 \
  -vf "yadif" \
  -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p \
  -an \
  -rtsp_transport tcp \
  -f rtsp rtsp://localhost:8554/live.stream
```

---

### 3. Lire le flux (test)

```bash
ffplay -rtsp_transport tcp rtsp://localhost:8554/live.stream
```

---

## 📍 URL du flux

```
rtsp://localhost:8554/live.stream
```

---

## ✅ Résultat

* Un flux RTSP local fonctionnel
* Représente une caméra de surveillance simulée
* Peut être utilisé pour :

  * ingestion vidéo
  * détection de scène
  * extraction de frames
  * tests de pipeline IA

---

## ⚠️ Notes

* L’audio est désactivé (`-an`) pour simplifier le flux
* Le transport TCP est recommandé pour plus de stabilité
* Ce setup est idéal pour un PoC reproductible

---
