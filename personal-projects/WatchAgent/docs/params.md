Parfait — ces paramètres sont **le cœur de ton intelligence de segmentation**.
Je vais te les expliquer comme si on réglait un système de surveillance réel 👇

---

# 🧠 Vue d’ensemble

Ton système détecte un changement de scène quand :

```text
Image actuelle ≠ image précédente ≠ image de référence
```

… avec plusieurs garde-fous pour éviter les faux positifs.

---

# 🎯 1. `hist_ref_threshold`

```python
hist_ref_threshold = 0.18
```

👉 Compare la frame actuelle avec **le début de la scène**

### 🧠 Rôle

> “Est-ce que la scène actuelle est différente de l’état initial ?”

### 📊 Méthode

Distance de Bhattacharyya sur histogrammes HSV :

* 0 → identique
* 1 → totalement différent

---

### 💡 Interprétation

| Valeur | Effet          |
| ------ | -------------- |
| 0.05   | ultra sensible |
| 0.18   | bon compromis  |
| 0.4    | très strict    |

---

### 🎯 Exemple

* caméra de parking
* une voiture arrive

👉 histogram change → dépasse 0.18 → candidat changement

---

# 🎯 2. `hist_prev_threshold`

```python
hist_prev_threshold = 0.12
```

👉 Compare avec **la frame précédente**

### 🧠 Rôle

> “Est-ce qu’il y a un changement instantané ?”

---

### ⚠️ Pourquoi c’est crucial

Sans ça :
👉 un changement lent pourrait être détecté comme une nouvelle scène

---

### 🎯 Exemple

* soleil qui se couche (progressif)

👉 ref change mais prev change faible → PAS de nouvelle scène

---

# 🎯 3. `lowres_diff_threshold`

```python
lowres_diff_threshold = 18.0
```

👉 différence pixel (grayscale basse résolution)

---

### 🧠 Rôle

> détecter les changements **structurels** (mouvement réel)

---

### Pourquoi c’est complémentaire aux histogrammes ?

Histogramme → global (couleurs)
Lowres diff → spatial (formes)

---

### 🎯 Exemple

| Cas            | Histogram | Lowres |
| -------------- | --------- | ------ |
| lumière change | 🔺        | ❌      |
| personne passe | 🔺        | 🔺     |

---

👉 donc :

```text
lowres_diff = filtre anti faux positifs
```

---

# 🎯 4. `min_scene_len_frames`

```python
min_scene_len_frames = 15
```

👉 nombre minimum de frames avant d’autoriser un changement

---

### 🧠 Rôle

> éviter les micro-scènes inutiles

---

### 🎯 Exemple

* caméra tremble
* 2 frames différentes

👉 sans ce param → explosion de scènes

---

# 🎯 5. `required_consecutive_changes`

```python
required_consecutive_changes = 3
```

👉 nombre de frames consécutives validant le changement

---

### 🧠 Rôle

> confirmer que le changement est réel

---

### 🎯 Exemple

```text
Frame 1 → changement
Frame 2 → changement
Frame 3 → changement
→ OK nouvelle scène
```

---

### ⚠️ Sans ça

👉 bruit = nouvelle scène

---

# 🎯 6. `target_keyframe_fps`

```python
target_keyframe_fps = 3.0
```

👉 combien de frames tu gardes par seconde

---

### 🧠 Rôle

> contrôler le coût VLM 💸

---

### 🎯 Exemple

| FPS entrée | target | ratio |
| ---------- | ------ | ----- |
| 25         | 3      | 12%   |
| 5          | 3      | 60%   |

---

👉 impact direct :

```text
moins de frames = moins d’API calls
```

---

# 🎯 7. `max_scene_buffer_frames`

```python
max_scene_buffer_frames = None
```

👉 limite mémoire du buffer de scène

---

### 🧠 Rôle

> éviter explosion RAM

---

### 🎯 Exemple

* scène statique 3h
* 25 fps

👉 sans limite = 💥 RAM

---

### ✔️ Bonne pratique

```python
max_scene_buffer_frames = 300  # ~10 sec
```

---

# 🎯 8. `lowres_size`

```python
lowres_size = (96, 54)
```

👉 taille pour comparaison rapide

---

### 🧠 Rôle

> accélérer les calculs

---

### Tradeoff

| Taille  | Précision | CPU |
| ------- | --------- | --- |
| 32x18   | faible    | ⚡   |
| 96x54   | bon       | 👍  |
| 320x180 | élevé     | 🐢  |

---

# 🎯 9. `default_fps`

```python
default_fps = 25.0
```

👉 fallback si FPS inconnu

---

### 🧠 Rôle

> éviter crash logique

---

### ⚠️ Important

👉 ce n’est PAS le vrai FPS
👉 juste une valeur de secours

---

# 🔥 Résumé global (le plus important)

Ton système déclenche une nouvelle scène si :

```text
1. changement global (hist_ref)
2. changement local (hist_prev)
3. changement spatial (lowres)
4. assez long (min_scene_len)
5. stable (consecutive_changes)
```

---

# 🧠 Vision produit (très important)

Ces paramètres contrôlent :

👉 **le compromis :**

```text
sensibilité vs stabilité
coût vs précision
```

---

# 🎯 Configuration recommandée pour TON use case (surveillance)

```python
hist_ref_threshold = 0.15
hist_prev_threshold = 0.10
lowres_diff_threshold = 15.0

min_scene_len_frames = 10
required_consecutive_changes = 2

target_keyframe_fps = 2.0

max_scene_buffer_frames = 300
```

---

# 💡 Insight clé

👉 Tu n’essaies PAS de détecter des “scènes cinéma”

👉 Tu veux détecter :

```text
"quelque chose d'important a changé"
```

---

# 🚀 Si tu veux aller plus loin

Prochaine étape logique :

👉 remplacer ces seuils fixes par :

* seuils adaptatifs
* ou modèle ML léger

👉 là tu passes de **heuristique → agent intelligent**

---

Si tu veux, je peux t’aider à :

👉 visualiser ces scores en live (très utile pour debug 👀)
