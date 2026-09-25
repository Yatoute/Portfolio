*23/07/2026 — testé sur l’importation 1164 (12 285 documents)*

## Contexte

L’objectif initial était d’améliorer la qualité et les performances de MADLAD, censé remplacer
notre solution actuelle de traduction. Plusieurs modèles de traduction généralistes multilingues
open source ont été testés dans cette optique — même constat à chaque fois : ils hallucinent trop
sur les langues à faibles ressources, et ignorent parfois complètement certaines expressions au
profit de tournures plus fréquentes rencontrées à l’entraînement.

Ce constat a orienté vers une autre piste : repenser l’architecture autour de LibreTranslate
plutôt que de chercher un autre modèle généraliste — en réutilisant **exactement les mêmes
modèles de traduction** que LibreTranslate (donc la même qualité de traduction, garantie par
construction), mais servis différemment pour aller beaucoup plus vite et moins cher.

## Ce qui change par rapport à LibreTranslate (v1)

`gcp-fct-libretranslate-v2` n’est pas un autre moteur de traduction : c’est **le même moteur**
(Argos Translate — les mêmes paires de modèles qu’utilise LibreTranslate en interne), réarchitecturé
autour de deux changements :

- **Chargement à la demande, avec cache, au lieu d’un préchargement fixe.** v1 recharge un jeu figé
de 22 langues en mémoire à **chaque** démarrage, qu’elles soient utilisées ou non sur le batch en
cours. v2 couvre les **50 langues** que sait traduire Argos, mais ne charge un modèle que lorsqu’une nouvelle langue est réellement rencontrée dans le corpus (après détection automatique de langue), et le garde en cache durant toute la vie de l’instance chaude.
- **Traduction par lot, groupée par langue.** Après détection automatique de la langue de chaque
document, les documents d’une même langue sont regroupés et envoyés **ensemble** au modèle en un seul appel, plutôt qu’un appel par document.

**→ Ces deux changements d’architecture, à eux seuls : ×10 plus rapide que v1, -40% de coût.**

## Le vrai accélérateur : la taille de lot du modèle

Les modèles Argos ont un paramètre critique : la taille maximale de lot traité en une seule
passe d’inférence (*forward pass*). Sa valeur par défaut, 32, est pensée pour faire tourner ces
modèles sur des machines modestes, sans GPU — elle ne profite pas du tout de la capacité d’un GPU
de datacenter.

Ce paramètre a été testé à la hausse pour exploiter réellement notre GPU. Après plusieurs paliers,
le plafond optimal observé sur le GPU L4 de Google Cloud est **4096** (au-delà, plus aucun gain
mesurable — un vrai plafond de calcul confirmé, pas une supposition).

**→ Avec ce réglage : ×76 plus rapide que v1, -90% de coût.**

En parallèle, la taille de lot de documents envoyée à une instance a été portée à **500 documents
par défaut**, pour donner suffisamment de matière à chaque passe d’inférence de 4096 tokens.
Envoyer plus de documents à une seule instance ne bloque rien : le modèle traduit simplement en
plusieurs passes d’inférence si besoin. C’est même recommandé tant qu’une seule instance GPU tourne
— cela évite des appels superflus à la fonction.

## Résultat final

|  | v1 (CF translate + service libretranslate) | v2 (candidat, GPU) |
| --- | --- | --- |
| Débit par instance (ms/document) | 1 855 ms | 24 ms |
| Coût total réel (import test) | 3,53 $ | 0,37 $ |

**→ ×76 plus rapide par instance, -90% de coût.**

*(v2 tourne volontairement plafonné à 1 seule instance GPU aujourd’hui — pas une limite de capacité du système.)*

## Qualité de traduction

Mêmes modèles que v1 des deux côtés → qualité équivalente, vérifiée document par document sur un échantillon réel.

## Licence

Licence des modèles vérifiée : CC-BY 4.0 (attribution requise, non restrictive — cf. README
embarqué dans chaque paquet Argos, ex. `ARGOS_MODELS/ro_en/README.md`, citant OPUS-MT/Tiedemann & Thottingal 2020). Aucune exposition nouvelle : LibreTranslate (v1) utilise déjà `argostranslate`comme moteur en interne, donc **les mêmes fichiers de modèle, sous la même licence**, en production aujourd’hui.
