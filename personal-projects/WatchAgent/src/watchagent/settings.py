# ============================================================
# WatchAgent – Configuration centralisée
# Toutes les valeurs par défaut du projet sont définies ici.
# ============================================================

# --- Vidéo générale -----------------------------------------
DEFAULT_FPS: float = 30.0         # FPS de secours quand le flux ne l'expose pas
DEFAULT_SOURCE_FPS: float = 30.0  # FPS typique d'une caméra IP

# --- RTSP Streaming -----------------------------------------
RTSP_QUEUE_SIZE: int = 1                    # Taille de la queue inter-threads (frames)
RTSP_STALL_TIMEOUT: float = 5.0            # Secondes sans frame → flux considéré bloqué
RTSP_RECONNECT_INTERVAL: float = 1.0       # Attente entre deux tentatives de reconnexion
RTSP_MAX_RECONNECT_ATTEMPTS: int = 10       # Nombre max de reconnexions avant abandon
RTSP_FRAME_TIMEOUT: float = 1.0            # Timeout du générateur frames() en secondes

# --- FrameSampler -------------------------------------------
SAMPLER_TARGET_FPS: float = 5.0            # FPS cible après sous-échantillonnage
SAMPLER_EDGE_MARGIN_RATIO: float = 0.0     # Part des frames de bord exclues (0 = aucune)

# --- Features de frame (bas niveau) ------------------------
LOWRES_SIZE: tuple[int, int] = (96, 54)    # Résolution pour la comparaison rapide de frames

# --- Détection de scènes ------------------------------------
SCENE_GLOBAL_CHANGE_THRESHOLD: float = 0.30   # Distance Bhattacharyya ref → changement global
SCENE_LOCAL_CHANGE_THRESHOLD: float = 0.20    # Distance Bhattacharyya prev → changement local
SCENE_FRAME_SIMILARITY_THRESHOLD: float = 0.01 # En dessous : frame considérée similaire à la précédente
SCENE_MIN_LEN_FRAMES: int = 1                  # Nombre minimum de frames pour fermer une scène

# --- Client Ollama (VLM) ------------------------------------
OLLAMA_BASE_URL: str = "http://localhost:11434"
OLLAMA_MODEL: str = "qwen2.5vl:3b"
OLLAMA_JPEG_QUALITY: int = 85      # Qualité JPEG des fichiers temporaires
OLLAMA_MAX_RESOLUTION: int = 640   # Largeur/hauteur max avant sauvegarde (0 = pas de resize)
OLLAMA_TEMPERATURE: float = 0.0    # 0 = décodage glouton (plus rapide et déterministe)

# --- SceneDescriber -----------------------------------------
# ⚠ qwen2.5vl:3b : limiter à 4–5 images max par appel.
#   Au-delà, llama.cpp lève GGML_ASSERT(a->ne[2] * 4 == b->ne[0]) dans
#   l'encodeur vision (contrainte du merge spatial 2×2 des patches visuels).
DESCRIBER_MAX_KEYFRAMES: int = 4   # Nombre max de keyframes envoyées au VLM par scène
DESCRIBER_CONTEXT_WINDOW: int = 3  # Nombre de scènes précédentes injectées comme contexte
