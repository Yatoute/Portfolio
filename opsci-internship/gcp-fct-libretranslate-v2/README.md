# gcp-fct-libretranslate-v2

Cloud Function candidate pour remplacer `gcp-fct-translation` (qui appelle un service Cloud Run
externe `libretranslate`, image Docker stock) par un backend **local**, sans dépendance HTTP à un
autre service, tout en gardant **exactement les mêmes modèles de traduction**.

## Pourquoi cette fonction

`gcp-fct-translation` ne fait aucune traduction elle-même : elle délègue par HTTP à un Cloud Run
séparé (`libretranslate/libretranslate:latest`, cf. `gcp-terraform/runs.tf`) configuré avec
`LT_LOAD_ONLY` sur 22 langues. Ce service scale à zéro (`container_concurrency=1`, pas de
`min_instance_count`) : **chaque cold start recharge les 22 modèles de langue en mémoire**, même
si une seule paire est nécessaire pour la requête en cours. Mesuré sur le benchmark
`notebooks/perfs/perfs_translate/main.ipynb` (importation 1164) : moyenne de **1854,89 ms/doc**,
jusqu'à 338 s pour un lot de 50 documents — et ce, même quand `gcp-fct-translation` elle-même est
"warm" (`cold_start_ms=0`), preuve que le goulot d'étranglement est entièrement côté service
`libretranslate`, invisible au `PerfTimer` de la CF appelante.

`libretranslate` (et le paquet Python **Argos Translate** qu'il utilise en interne) s'appuie sur
CTranslate2 — **exactement le même moteur d'inférence** que `gcp-fct-translation-madlad`. Les
paquets de traduction distribués par Argos Translate sont eux-mêmes **dérivés des modèles
OPUS-MT** (Helsinki-NLP) : le `README.md` embarqué dans chaque paquet `.argosmodel` cite
explicitement "Jörg Tiedemann and Santhosh Thottingal — OPUS-MT" et précise que le modèle
d'origine est sous licence **CC-BY 4.0**.

Cette fonction reprend donc **les modèles exacts déjà utilisés en production** (aucun changement
de poids/qualité à revalider), mais les charge **à la demande, un par un, avec un cache LRU par
paire de langues** (`utils/cache.py`) plutôt que tout charger d'un coup — pattern calqué sur
`gcp-fct-video-transcription/utils/cache.py` (`get_or_load_align_model`), transposé ici à des
paires de traduction au lieu de modèles d'alignement WhisperX.

## Différences avec `gcp-fct-translation`

| | `gcp-fct-translation` (actuelle) | `gcp-fct-libretranslate-v2` |
|---|---|---|
| Backend | Appel HTTP à un Cloud Run externe (`libretranslate`) | Argos Translate en local (CTranslate2), dans le process |
| Chargement modèle | Toutes les 22 langues à chaque cold start du service externe | À la demande, une paire à la fois, cache LRU |
| Pivot langue manquante | Géré en interne par Argos Translate côté service externe | Idem (réutilise `argostranslate.translate.CompositeTranslation`), déclenché explicitement ici |
| Éviction mémoire | Aucune (scale-to-zero = tout perdu, ou tout gardé) | LRU par paquet + désinstallation disque sous pression mémoire |
| Paramètres HTTP | — | **Identiques** (remplacement direct) |

## Paramètres (body JSON / query params)

Contrat **identique** à `gcp-fct-translation` :

| Paramètre | Défaut | Description |
|---|---|---|
| `lang` | — | Langue cible (`fr` ou `en`). Requis sauf en mode `post_lang_detection`. |
| `source_lang` | `"auto"` | Langue source, sinon détectée via `fast_langdetect`. |
| `importation_ids` | — | Liste d'IDs d'importations (accepte aussi `importation_id` legacy, un int, ou une string CSV). |
| `just_translate_ids` | — | Liste d'IDs de documents/chunks à traiter directement. |
| `force_translate` | `false` | Force la retraduction même si `text_fr`/`text_en` déjà renseigné. |
| `task_queue` | `"libretranslate-v2"` | Queue Cloud Tasks dédiée. |
| `batch_size` | `1000` | Taille de CHAQUE lot une fois qu'on a décidé de découper (cf. `dispatch_threshold` ci-dessous — ne contrôle plus la décision de découper depuis le 2026-07-24). Relevé 50→200 (benchmark du 2026-07-16) puis 200→500 (Test 8) puis 500→1000 (2026-07-24, avec `max_instance_count=5`) : un lot plus gros donne plus de matière par groupe de langue au batching GPU interne (`ARGOS_BATCH_SIZE`), mesuré -11% de temps/coût sur le saut 200→500. 1000 pas encore re-testé empiriquement (raisonnement : cf. section historique), mais garde ~5 lots par découpage juste au-dessus de `dispatch_threshold=5000` — un par instance disponible (~25ms/doc, 1000 docs ≈ 25s/tâche, sous `timeout_seconds=600`). |
| `dispatch_threshold` | `5000` | Nombre de documents/chunks au-delà duquel on découpe en plusieurs tâches Cloud Tasks (potentiellement plusieurs instances GPU) plutôt que de tout traiter dans l'invocation en cours. Introduit le 2026-07-24 pour découpler cette décision de `batch_size` — sous ce seuil, tout est traité par l'instance déjà chaude en cours plutôt que de démarrer inutilement d'autres instances froides (chacune payant son propre coût fixe d'import + ses propres téléchargements de modèles). **Garde-fou** : si `batch_size > dispatch_threshold` (configuration incohérente — un sous-lot se re-découperait à l'identique indéfiniment), `batch_size` est silencieusement ramené à `dispatch_threshold` (validateur Pydantic, testé). |
| `start_date` / `end_date` | — | Filtre de date sur `Post.created_at`. |
| `document_type` | `"text"` | Type de document (`text`, `image`, `video`). |
| `post_lang_detection` | `false` | Si `true`, détecte uniquement la langue et met à jour `Post.lang`. |
| `translate_chunks` | `false` | Si `true`, traduit les `Chunk.text` d'une analyse (transcripts WhisperX). Nécessite `analyse_id`. |
| `analyse_id` | — | Analyse dont les chunks sont à traduire. |
| `max_retries` | `5` | Tentatives avant traitement document-par-document en cas d'erreur transitoire. |
| `retry_count` | `0` | Compteur interne (géré par Cloud Tasks). |

## Exemple de requête

```bash
curl -X POST https://europe-west1-demo-project.cloudfunctions.net/libretranslate-v2 \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{
    "importation_ids": [123],
    "lang": "fr",
    "force_translate": false
  }'
```

## Logique métier

1. Appel initial (sans en-tête `X-CloudTasks-TaskName`) → dispatch fire-and-forget vers Cloud
   Tasks (queue `libretranslate-v2`), retour `202`.
2. Trois modes, mutuellement exclusifs : `post_lang_detection`, `translate_chunks`, ou traduction
   des `Document.text` par défaut (mêmes règles de sélection que `gcp-fct-translation`).
3. Si le nombre de documents dépasse `dispatch_threshold`, découpage en sous-tâches Cloud Tasks
   (taille `batch_size` chacune) partageant le même `parent_job_id`.

   **`call_back_pipeline` réintroduit (2026-07-29)**, cf. section dédiée plus bas — retiré le
   2026-07-24 puis réintroduit une fois le coordinateur de fusion arrivé, qui a rendu le polling
   `check_queue_status` déconnecté de la réalité. Tous les noms de tâche Cloud Tasks suivent le format
   `{prefix}-j{job_id}-...` avec `j{job_id}` systématiquement en 2e position (séparateur `-`) —
   nécessaire pour que le matching positionnel de `gcp-fct-tasks-status`
   (`basename_parts[1] == f"j{job_id}"`) fonctionne quel que soit le type de tâche (init,
   langdetect, chunks, batch principal, retry).
4. Détection de langue par document (`fast_langdetect`), regroupement par langue détectée, puis
   **préchargement en parallèle** (`utils.cache.prefetch_packages()`, ajouté le 2026-07-24) de
   tous les paquets Argos nécessaires pour l'ENSEMBLE des langues du lot, avant de traduire le
   premier groupe — plutôt que d'attendre, groupe par groupe, la fin du téléchargement du
   précédent avant de lancer le suivant. Dédoublonné sur `(from_code, to_code)` avant dispatch
   (un paquet pivot `en->target_lang` est souvent partagé par plusieurs langues sources du même
   lot) : chaque paquet manquant n'est donc téléchargé qu'une seule fois, mais tous les paquets
   manquants distincts démarrent en même temps (`ThreadPoolExecutor`). Sûr par construction :
   `argostranslate.package` protège déjà l'extraction/le catalogue installé par un verrou global
   (`package_lock`), le téléchargement réseau se fait hors de ce verrou — et le dédoublonnage en
   amont garantit qu'on ne demande jamais deux fois le même paquet en parallèle. Ensuite, pour
   chaque groupe : récupération (ou chargement, déjà chaud dans le cas courant grâce au
   préchargement) d'une traduction Argos Translate via
   `utils.cache.get_or_load_translation(from_code, to_code)` :
   - Paire directe si elle existe dans l'index Argos (ex. `en->fr`).
   - Sinon pivot automatique via l'anglais (ex. `ro->fr` : installe `ro->en` et `en->fr`, Argos
     Translate construit lui-même le `CompositeTranslation`).
   - Les paquets déjà installés (mémoire + disque `/tmp`) sont réutilisés tels quels ; sous
     pression mémoire, les paquets les moins récemment utilisés sont désinstallés (mémoire ET
     disque — `/tmp` est un tmpfs sur Cloud Run, donc compté dans la RAM du conteneur).
   - **Miroir GCS** (`_install_from_gcs` dans `utils/cache.py`, ajouté le 2026-07-24) : avant de
     télécharger depuis le CDN public `argos-net.com`, le paquet est cherché sur
     `gs://demo-project.appspot.com/ARGOS_MODELS/{from_code}_{to_code}/` — peuplé par
     `notebooks/maintenance/upload_modeles_argos_translate.ipynb` (réexécutable à tout moment pour
     rafraîchir ; les 50 paires couvrant `ALLOWED_LANGUAGES`, 4,86 Go au total, y sont déjà). Un
     transfert same-region est nettement plus rapide et fiable qu'un CDN externe (motivé par le
     pic de 32s de téléchargement concentré sur la 1ère tâche du Test 4, jusqu'à 37% de son temps
     total), et fige une version connue (pas de dérive de modèle silencieuse — cf. root cause du
     bug tout-caps masqué côté LibreTranslate v1 par un cache dont la version installée n'était
     plus connue). Repli automatique sur `argos-net.com` si une paire n'est pas (encore) dans le
     miroir, sans lever d'exception.
   - `ALLOWED_LANGUAGES` (50 langues, mis à jour le 2026-07-24) couvre **tout** ce qu'Argos
     Translate sait traduire (vérifié en direct sur l'index public), pas les 22 langues de
     `LT_LOAD_ONLY` héritées de `gcp-fct-translation` — cette liste-là reflétait une contrainte
     de coût de chargement de LibreTranslate qui ne s'applique pas ici (chargement à la demande).
     Deux cas distincts en mode `source_lang="auto"` :
     - **Détection en échec** (exception `fast_langdetect`) : document ignoré, pas de fallback —
       on ne sait pas du tout de quelle langue il s'agit (`n_skipped_detection_failed`).
     - **Langue détectée mais non supportée par Argos** (aucun paquet, ex. l'occitan) :
       `fallback sur 'en'` — le texte est traduit tel quel via le pivot anglais même s'il n'est
       pas en anglais (`n_fallback_en_unsupported_lang`), une traduction imparfaite plutôt que
       pas de traduction du tout, pour des langues qu'Argos ne supportera probablement jamais.
5. En cas d'erreur transitoire pendant la traduction d'un lot : re-dispatch en 2 sous-lots (ou
   document par document au-delà de `max_retries`), même pattern que `gcp-fct-translation`. En cas
   d'échec propre à une langue (ex. paquet Argos introuvable), les documents concernés ne sont
   **pas** mis à jour (pas d'écrasement par `NULL`) — ils restent non traduits jusqu'à un run
   ultérieur, plutôt que de perdre une traduction déjà valide en base.
6. Avant traduction, chaque texte est protégé (`protect_entities`) : mentions/hashtags/urls/emojis
   démojizés sont remplacés par des placeholders en **texte brut** (`PLACEHOLDER0`, `PLACEHOLDER1`,
   ...), traduits tels quels, puis restaurés après coup (`restore_entities`). Contrairement à
   l'approche HTML de `gcp-fct-translation` (`add_tag_libretranslate_not_translate`/
   `translate_html`), ce mécanisme reste **un seul appel `.translate()` par texte** — pas de
   fragmentation par nœud DOM. Validé le 2026-07-23 sur `notebooks/perfs/perfs_translate/
   quality_sample_libre.csv` (pivot `ro->en->fr` réel) : surcoût mesuré ~+20% (vs +250-350% pour
   `translate_html`, testé en prod puis réverté — cf. Limitations). Pas garanti à 100% (un
   placeholder peut occasionnellement être perdu par le modèle sur un pivot), mais un échec isolé
   ne dégrade que l'entité concernée, pas tout le document.
7. Traduction via `batch_translate()` : **toutes les phrases de tous les documents d'un groupe
   de langue, sur un saut donné, en un seul appel CTranslate2 `translate_batch()`** (direct, ou
   pivot via `CompositeTranslation` — le texte anglais intermédiaire est entièrement réassemblé
   après le 1er saut puis re-découpé en phrases anglaises pour le 2e, comme le fait
   `CompositeTranslation.hypotheses()` en interne). Bypass volontaire de l'API haut niveau d'Argos
   (`ITranslation.hypotheses()`, qui ne traite qu'un texte à la fois) — accès direct à
   `pkg.tokenizer`/`translator.translate_batch()`, avec découpage en phrases via
   `sentencizer.split_sentences()` (cf. section historique ci-dessous pour le pourquoi) et
   réassemblage par concaténation des tokens de
   chaque phrase avant un unique `decode()` par document, comme le fait
   `apply_packaged_translation()` en interne. Validé le 2026-07-23 (`notebooks/perfs/perfs_translate`)
   : résultats **bit-identiques** entre traduction un-par-un et en batch sur 60 textes réels
   hétérogènes (CTranslate2 traite chaque séquence d'un batch indépendamment, masquage d'attention
   pour le padding) — aucun impact sur la qualité.

   **`ARGOS_BATCH_SIZE=4096`** (`utils/cache.py::ensure_argos_env_configured`, relevé du défaut
   Argos de 32) : à ne pas confondre avec `batch_size` (1000, le nombre de documents par tâche
   Cloud Tasks) — celui-ci contrôle la taille des lots internes CTranslate2 (`batch_type="tokens"`
   dans `_batch_translate_sentences`), pensé par défaut pour du matériel modeste. Historique des
   paliers testés en prod (GPU L4) : 256 (Test 4, ~2% de gain réel seulement — lot interne encore
   trop petit, ~1 document/lot vu la longueur moyenne de 31,8 tokens/doc) → 1024 (Test 5,
   **×5,78** vs Test 4, 30,72 ms/doc — dépasse largement la prédiction locale ×2,3 mesurée sur un
   GPU portable) → 2048 (Test 6, **×1,12** seulement vs Test 5, 27,37 ms/doc — rendement net
   décroissant, plateau qui approche) → **4096** (Test 7, +1,1% seulement vs Test 6 — **plateau
   confirmé**, valeur figée définitivement, pas d'intérêt à tester plus haut). Cf. section
   historique pour le détail complet des Tests 5 à 7.

   Utilise aussi `length_penalty=1.0` (le défaut de CTranslate2 lui-même) au lieu de `0.2`, que
   `argostranslate.translate.apply_packaged_translation()` applique en interne par défaut — 0.2
   est un choix arbitraire d'Argos Translate, rien ne garantissait qu'il convienne à nos contenus.
   Comparé sur les 15 documents réels de `quality_sample_libre.csv` (pivot `ro->en->fr` inclus,
   toutes langues confondues) : 0.2 tronque prématurément plusieurs traductions multi-phrases
   (perte de contenu silencieuse sur 2 documents) et rate presque toujours la bonne hypothèse sur
   le texte tout-caps ; 1.0 corrige les deux, sans régression observée sur les 15 documents (1 seul
   document avec une différence de formulation mineure, ni meilleure ni pire).

   Rejette les hypothèses dégénérées (sans lettre) parmi 3 candidates par texte (gratuit, déjà
   calculées par le beam search). `None` pour un texte si toutes ses hypothèses sont dégénérées
   — traité comme un échec de traduction (le document reste non traduit, cf. point 5).

   La mise en minuscule générale du texte tout-caps avant traduction (présente dans l'ancien
   `safe_translate()`) a été retirée le 2026-07-23 (`length_penalty=1.0` seul suffisait sur les
   15 documents réels + des cas construits sigles/noms testés isolément, et la minuscule
   dégradait la traduction des entités — ex. "Ioan" anglicisé en "Jonathan"). **Mais le Test 4 en
   production a révélé un angle mort** (2026-07-24) : cette validation ne testait que du texte
   tout-caps *seul*, jamais combiné aux placeholders `PLACEHOLDER{i}` insérés par
   `protect_entities()`. Sur du texte tout-caps **avec** au moins un placeholder (ex. un post
   tout-caps contenant des emojis), le beam search s'effondre sur **toutes** ses hypothèses
   (testé jusqu'à 8) vers une sortie qui ne garde que les placeholders et perd le contenu réel —
   non détecté par le rejet d'hypothèse dégénérée puisque "PLACEHOLDER" contient des lettres.
   La mise en minuscule est donc réappliquée, mais **seulement** pour ce cas combiné précis
   (`text.isupper() and "PLACEHOLDER" in text`), pas pour tout texte tout-caps comme
   auparavant — le risque sur les entités reste donc limité aux textes tout-caps qui contiennent
   aussi une entité protégée, un sous-ensemble bien plus étroit. `restore_entities()` a aussi été
   rendu tolérant à un espace inséré par le tokenizer entre "PLACEHOLDER" et l'index (ex.
   "PLACEHOLDER 0"), observé sur ce même chemin de minuscule — un remplacement par chaîne exacte
   ratait ces occurrences.
8. `PerfTimer` (`function_name="libretranslate-v2"`) permet la comparaison directe avec
   `translate` et `translation-madlad` via Cloud Logging (`jsonPayload.perf.function_name`).

## Benchmark v1 vs v2 (2026-07-16, importation 1164, 12 285 docs)

Voir `notebooks/perfs/perfs_translate/main_libretranslate_v2.ipynb` pour le détail complet.

- **Perf** : ~11,5× plus rapide par document (161,25 ms/doc vs 1854,89 ms/doc), avec un wall-clock
  total comparable à v1 alors que v2 tourne sur **une seule instance** GPU contre jusqu'à 20
  instances CPU pour v1 (confirmé via Cloud Monitoring `instance_count`).
- **Coût réel total** (CF + service externe pour v1) : 1,91 $ (v2) vs 3,53 $ (v1), soit -46%.
- **Qualité** : fidélité de traduction globalement comparable (mêmes modèles Argos/OPUS-MT sous
  les deux backends). L'écriture de `NULL` sur échec de traduction identifiée par ce benchmark a
  été corrigée (point 5 ci-dessus).

## Historique de la protection des emojis/mentions/hashtags/URLs

1. **Absente** (implémentation initiale) : perte systématique des emojis, URLs parfois traduites
   (ex. `example.com` → `exemple.com`).
2. **Tentative `translate_html`** (package `argos-translate-files`, même mécanisme que
   `gcp-fct-translation`/LibreTranslate) — testée en production le 2026-07-23, **révertée** :
   fragmente chaque texte en un appel de traduction par nœud DOM autour de chaque entité protégée,
   inférence passée de 161 ms/doc à ~490-760 ms/doc (`parent_job_id=Test2-libretranslate-v2`,
   ~3,5-4,5× plus lent) — pire que le gain de perf de v2 sur v1.
3. **Placeholders en texte brut** (`protect_entities`/`restore_entities`, actuel) : un seul appel
   `.translate()`, pas de fragmentation. Validé le 2026-07-23 sur `quality_sample_libre.csv`
   (pivot `ro->en->fr` réel) : surcoût mesuré ~+20% seulement, emojis préservés dans la grande
   majorité des cas. Non garanti à 100% — cf. Limitations.

## Historique du bug "texte tout-caps → traduction vide" (résolu)

Observé sur `quality_sample_libre.csv`, doc_id 61883126, ex. "SCANDAL ÎN ȘATRA GUVERNAMENTALĂ".
**Root cause confirmée** (2026-07-23) : ce n'était **pas un bug de notre code**. Le modèle Argos
`ro->en` (paquet public version 1.9, jamais modifié depuis son ajout le 22/10/2023 — vérifié dans
l'historique Git de `argosopentech/argospm-index`) classe une hypothèse dégénérée (`'_'`) en rang 1
de son beam search pour ce texte tout-caps ; le rang 2, à peine moins bien noté (-2.951 vs -2.752),
est la traduction correcte. `.translate()` (nous, avant fix) et `hypotheses()[0]` (LibreTranslate,
même logique) prennent tous deux le rang 1 → même échec en théorie des deux côtés. Écarté par test
direct : sentence splitting (Stanza), version de librairie (`argostranslate` vs
`argos-translate-lt==1.12.1`, échec identique), taille de beam (4/8/16/32, rang inchangé),
non-déterminisme threads/calcul flottant (1/2/4/8 threads, scores identiques au 6e chiffre).

**Ce qui masquait le bug côté v1** : LibreTranslate a son propre cache de traductions en mémoire
(`libretranslate/cache.py` + `storage.py`, backend `memory://` par défaut — confirmé, aucune
variable `LT_SHARED_STORAGE` dans `runs.tf`). Par défaut `TRANSLATION_CACHE=''`, et
`''.split(',')` vaut `['']` en Python (pas une liste vide) → le cache s'active par accident de
parsing pour les requêtes **sans clé API**, exactement le cas de `gcp-fct-translation`. TTL =
604800s = **7 jours pile** — soit très exactement l'écart entre le benchmark original (16/07) et
cette investigation (23/07). L'instance "chaude" testée servait une entrée de cache posée
antérieurement (probablement par le benchmark lui-même), jamais recalculée tant que le process
restait vivant. **Confirmé en forçant une vraie nouvelle révision Cloud Run** (`/tmp` vide,
cache mémoire vidé) : sur une instance fraîche, v1 échouait **à l'identique** de v2 (`"_"` puis
chaîne vide). Le défaut du modèle était présent depuis le début côté v1 aussi — juste masqué par
son cache. Risque partagé par les deux CF, pas un désavantage propre à v2 ; et confirme au
passage qu'aucune des deux ne fige de version de modèle Argos (chaque installation à froid
télécharge la version courante de l'index public, sans épinglage — même si dans ce cas précis il
n'y a de toute façon qu'une seule version disponible).

**Fix appliqué** (2 versions successives, cf. point 7 de la logique métier ci-dessus) :
1. `safe_translate()` (2026-07-23, premier fix) : texte tout-caps mis en minuscules avant
   traduction (casse restaurée après), + rejet des hypothèses sans lettre en demandant 3
   candidats au lieu d'un seul. Validé sur le document `61883126` : ne produisait plus de sortie
   vide/dégénérée, mais traduisait toujours un document à la fois.
2. `batch_translate()` (2026-07-23, remplace `safe_translate()`) : mêmes protections, appliquées
   sur un batch complet en un seul appel CTranslate2 par saut, **plus** `length_penalty=1.0` (au
   lieu du `0.2` par défaut d'Argos) qui corrige la cause racine plus directement — validé sans
   régression sur les 15 documents réels de `quality_sample_libre.csv`, ~1,8× plus rapide que
   traduire un par un (mesuré en local, GPU).

## Historique — Test 5 : bug de troncature multi-phrases (résolu) + rapport de perf

**Résultat Test 5** (2026-07-24, `ARGOS_BATCH_SIZE=1024` + cache GCS des modèles) :

| | v1 | Test 4 (`ARGOS_BATCH_SIZE=32`) | Test 5 |
|---|---|---|---|
| ms/doc | 1854,89 | 177,57 | **30,72** |
| Coût total | 3,532414 $ | 2,08803 $ | **0,441968 $** |

**×60,4 plus rapide que v1, -87,5% de coût.** Confirme que le vrai goulot du Test 4 était la
taille de lot interne CTranslate2 (32 tokens ≈ 1 document/lot vu la longueur moyenne des posts),
pas le regroupement d'appels Python — et que le gain de `ARGOS_BATCH_SIZE` est **plus fort en
prod (×5,78) qu'en local (×2,3)**, cohérent avec un GPU L4 plus puissant qui sature son
parallélisme à un volume de calcul plus élevé qu'un GPU portable.

**Mais un vrai bug qualité a été signalé par l'utilisateur sur cet échantillon** : plusieurs
documents perdaient tout leur contenu après la première phrase (ex. doc_id 61884107, 4 lignes,
seule la 1ère traduite — ratio longueur sortie/source de 0,36 ; doc_id 61883117, 2 phrases, seule
la 1ère traduite — ratio 0,56). **Root cause confirmée** : `batch_translate()` traitait chaque
document comme **une seule séquence** (pas de découpage en phrases, contrairement à l'API haut
niveau d'Argos) — sur des documents multi-phrases hétérogènes (changement de registre, emoji,
texte cassé), le beam search "décide" de terminer après la 1ère phrase. Vérifié que ce n'est
**pas** une troncature par `max_decoding_length` (14 tokens de sortie sur un texte à 56 tokens en
entrée, testé) — le modèle s'arrête volontairement, les 3 meilleures hypothèses sont d'accord.
Traduire les mêmes phrases séparément (comme le fait Argos en interne) restitue le contenu complet.

**Fix** : `batch_translate()`/`_translate_documents_one_hop()` découpe maintenant chaque document
en phrases via `sentencizer.split_sentences()` avant de batcher (toutes les phrases de tous les
documents d'un groupe, en un seul appel), puis réassemble chaque document par concaténation des
tokens de ses phrases avant un unique `decode()` — même logique que
`apply_packaged_translation()` en interne (concaténer les tokens plutôt que joindre des chaînes
déjà décodées, pour préserver l'espacement/la ponctuation gérés par le tokenizer SentencePiece).
Aucun coût de perf mesuré (73,2 ms/texte avec découpage vs ~74 ms/texte sans, sur le même
`ARGOS_BATCH_SIZE=1024`).

**Régression trouvée en corrigeant ce bug, corrigée dans la foulée** : la protection
tout-caps+placeholders (`text.isupper() and "PLACEHOLDER" in text`) est évaluée par phrase depuis
ce fix — mais le découpage en phrases isole parfois un placeholder **seul**, sans aucun autre
contenu (ex. un emoji en toute fin de texte, après un point d'interrogation). Un placeholder
isolé comme `"PLACEHOLDER0"` matche `isupper()`, était donc mis en minuscules
(`"placeholder0"`) — un vrai mot anglais que le modèle traduit alors littéralement
(`"TITULAIRE DE LA PLACE0"` observé, doc_id 61883100) au lieu de le laisser intact. Corrigé en
excluant les phrases qui ne sont QUE des placeholders (rien d'autre après les avoir retirés) de
cette mise en minuscule. Validé sur les 15 documents réels : 0 sortie dégénérée, 0 troncature
sévère, aucune régression sur les cas déjà corrigés (emoji, tout-caps+placeholders, entités).

## Historique — Test 6 : `ARGOS_BATCH_SIZE=2048`, fix de troncature confirmé en prod

**Résultat Test 6** (2026-07-24) : 27,37 ms/doc, coût total 0,405379 $ — soit **×67,8 plus rapide
que v1 et -88,5% de coût**. Vs Test 5 (`ARGOS_BATCH_SIZE=1024`) : ×1,12 seulement (contre ×5,78
pour le saut 256→1024) — **rendement net décroissant confirmé**, signe qu'un plateau approche, à
un palier plus élevé que celui observé en local (1024).

**Le fix de troncature multi-phrases (cf. section historique Test 5) est confirmé en conditions
réelles** : vérifié sur `quality_sample_test6_libretranslate_v2.csv`, les documents précédemment
tronqués (61884107, 61883117) et le cas du placeholder isolé mistraduit (61883100) sont tous
corrects — 0 sortie dégénérée, 0 troncature sur les 15 documents réels.

**Suite (Test 7)** : `ARGOS_BATCH_SIZE=4096`, dans le seul but de confirmer le plateau (pas
d'attente de gain significatif) — cf. point 7 de la logique métier ci-dessus.

## Historique — Test 7 : plateau `ARGOS_BATCH_SIZE` confirmé, puis Test 8 (`batch_size` CF)

**Résultat Test 7** (2026-07-24, `ARGOS_BATCH_SIZE=4096`) : 27,06 ms/doc, coût 0,397901 $ — **×68,55
plus rapide que v1, -88,7% de coût**. Vs Test 6 (2048) : seulement **+1,1%** sur le temps et le
coût — rendement marginal quasi nul sur deux paliers consécutifs, **plateau confirmé**. Qualité
identique à Test 6 sur les 15 documents réels, aucune régression. `ARGOS_BATCH_SIZE` figé à 4096,
pas de raison de tester plus haut.

**Question soulevée avant le Test 8** : `batch_size` (CF, nombre de documents par tâche) fournit-il
assez de matière par groupe de langue pour exploiter `ARGOS_BATCH_SIZE=4096` ? Le plateau lui-même
répond : si la matière manquait, 2048→4096 aurait continué à apporter un gain proportionnel au lieu
de s'effondrer à +1% — c'est la signature d'un plafond de calcul GPU, pas d'un lot trop petit.
`batch_size=200` fournissait donc déjà largement assez de matière **pour ce levier précis**.

**Test 8** (2026-07-24, `batch_size` CF 200→500, `ARGOS_BATCH_SIZE=4096` inchangé) : instance
Cloud Run redémarrée de force avant le run (`gcloud run services update --update-env-vars=...` puis
`--remove-env-vars`, sans dérive Terraform) pour repartir sur un cache LRU/VRAM vide. **Bug notebook
trouvé et corrigé au passage** : le payload envoyé à la CF n'incluait jamais `batch_size` dans les
Tests 4 à 7 (sans conséquence tant que la valeur notebook coïncidait avec le défaut serveur, 200).

**Résultat** : 24,06 ms/doc, coût 0,35338 $, wall_clock 383,6s — **-11,1% sur le temps, -11,2% sur
le coût** vs Test 7, malgré `ARGOS_BATCH_SIZE` inchangé. **×77,1 plus rapide que v1, -90,0% de
coût.** Gain réel et distinct du plateau `ARGOS_BATCH_SIZE` : plus de documents par tâche = groupe
de langue dominant plus gros par tâche = meilleure utilisation GPU, indépendamment du plafond
interne CTranslate2. Qualité identique à Test 7 sur les 15 documents réels, aucune régression.

**`batch_size` figé à 500, pas plus** (décision utilisateur, 2026-07-24) : avec `max_instance_count=1`
actuel, la taille de lot n'a aucun compromis (une seule instance traite tout séquentiellement,
plus gros = toujours mieux). Mais le nombre d'instances GPU est prévu d'augmenter en production —
chaque tâche Cloud Tasks devient alors une unité de parallélisme indépendante entre instances, et
des lots trop gros réduiraient la granularité disponible pour répartir le travail. 500 reste un bon
compromis (~25 tâches sur 12 245 docs au Test 8, largement de quoi occuper plusieurs instances en
parallèle une fois le scale-out fait). **Défaut serveur (`RequestParams.batch_size`) relevé à 500
en conséquence** le même jour.

## Préchargement parallèle des paquets Argos (2026-07-24)

Jusqu'ici, `process_translation()` appelait `get_or_load_translation()` **séquentiellement** par
groupe de langue — sur une tâche à plusieurs langues manquantes du cache (typiquement la 1ère
tâche d'un job après un redémarrage d'instance, cf. Test 4 : 8-9 paires de langues, jusqu'à 30s de
téléchargement cumulé sur la seule 1ère tâche du Test 8), le temps total de téléchargement était la
**somme** des téléchargements de chaque paire, alors que ce sont des transferts réseau/disque
indépendants (miroir GCS ou CDN Argos) qui n'ont aucune raison d'attendre les uns les autres.

**Implémenté** : `utils.cache.prefetch_packages(target_lang, source_langs)`, appelé une fois avant
la boucle de traduction, calcule l'union des paquets nécessaires pour **toutes** les langues du lot
(dédoublonnée sur `(from_code, to_code)` — un paquet pivot `en->target_lang` est souvent partagé
par plusieurs langues sources), puis télécharge en parallèle (`ThreadPoolExecutor`) uniquement ceux
manquants. Sûr par construction, vérifié en lisant `argostranslate/package.py` : l'écriture du
catalogue installé (`install_from_path`, appelé après téléchargement) est protégée par un verrou
global (`argostranslate.package.package_lock`) — mais le téléchargement réseau lui-même se fait HORS
de ce verrou, donc réellement parallélisable sans risque de corruption. Le dédoublonnage en amont
élimine par ailleurs tout risque de lancer deux fois le téléchargement du même paquet en parallèle.
La boucle de traduction par groupe (`get_or_load_translation()` par langue) reste inchangée dans sa
logique — elle retombe simplement sur un cache déjà chaud dans le cas courant, et sert de filet de
sécurité (retéléchargement individuel, mesuré) si un paquet a échoué au préchargement.

Les stats de cache (`n_cache_hits`/`n_cache_misses`) sont maintenant dérivées du résultat de
`prefetch_packages()` (état du cache **avant** le préchargement) plutôt que du retour de
`get_or_load_translation()`, qui verrait systématiquement un hit après le préchargement et rendrait
la métrique trompeuse.

**Non encore validé en prod** (implémenté le 2026-07-24, pas encore testé sur un vrai run avec
plusieurs langues manquantes simultanément — à vérifier au prochain test avec instance froide et
lot multi-langues).

## Remplacement de `translate` (v1) à 100% + montée à 5 instances (2026-07-24)

`libretranslate-v2` remplace désormais `gcp-fct-translation` sur tous les chemins identifiés :
les 4 pipelines (`coordination`, `topic-modeling`, `narrative-filtering` v1 et v2), le dispatch
de détection de langue de `gcp-fct-json-parser`, la route API Gateway `/jobs/analyse/translation`,
le mapping de `gcp-fct-scheduler` (nouvelles tâches), les entrées de `gcp-fct-tasks-status`, et les
6 tâches Cloud Scheduler déjà actives en production (migrées en place, mêmes programmation/corps/
retry, seule l'URL cible a changé).

**`max_instance_count` relevé 1→5** : plusieurs des tâches Cloud Scheduler migrées tirent à la
même minute chaque jour (4 à 2h10), plus des dispatches concurrents possibles depuis les 4
pipelines — 5 couvre cette charge observée sans viser aussi haut que les 20 instances CPU de v1
(chaque instance GPU fait déjà ~76× le débit d'une instance v1). `min_instance_count` reste à 0
(scale-to-zero intact) : ce changement n'augmente que la capacité de burst, pas le coût à vide.
`max_concurrent_dispatches` de la queue Cloud Tasks `libretranslate-v2` relevé 1→5 en cohérence.

**Nouveau champ `dispatch_threshold` (5000), découplé de `batch_size` (500→1000)** : jusqu'ici
`batch_size` servait à la fois de seuil de découpage ET de taille de lot — avec une seule instance
possible pendant tous les tests (3 à 9), ce conflit n'avait aucun effet observable. Avec 5
instances désormais possibles, découper trop tôt un petit volume reviendrait à démarrer
inutilement plusieurs instances froides (chacune payant son propre coût fixe d'import
ctranslate2/torch + ses propres téléchargements de modèles, cf. section préchargement parallèle
ci-dessus) pour un volume qu'une seule instance chaude traiterait déjà rapidement. `batch_size`
ne contrôle donc plus que la taille de chaque lot une fois le découpage décidé ;
`dispatch_threshold` (nombre de documents/chunks à partir duquel on découpe) est le nouveau
paramètre qui décide QUAND découper. Argument validé sans test dédié : cette décision est
architecturale (éviter des démarrages à froid superflus), pas une question de débit GPU par
forward-pass — contrairement à `batch_size`/`ARGOS_BATCH_SIZE`, qui eux ont toujours été validés
empiriquement avant d'être figés. `batch_size=1000` n'a en revanche pas été re-testé
empiriquement (seuls 200/500 ont des données réelles) — jugé sans risque significatif (le pire cas
plausible est un plateau, pas une régression, par analogie avec `ARGOS_BATCH_SIZE`), mais à garder
en tête si une future mesure montre un écart.

## Échecs Stanza MD5 + langues sans sentencizer (2026-07-26)

Analysé sur 9h+5h de logs de production réels (demande explicite de l'utilisateur) :

**`eo`/`ms`/`sw`/`tl` retirées d'`ALLOWED_LANGUAGES`** : échec garanti à 100% (`"No processors to
load for language X"`) — confirmé sur plusieurs occurrences réelles. `argostranslate/sbd.py`
(`MiniSBDSentencizer.LANGUAGE_CODE_MAPPING`) marque déjà `eo`/`ms`/`tl` comme *"a model for these
is not available"* (fallback anglais côté MiniSBD) ; `sw` n'y est même pas. Notre code utilise
`StanzaSentencizer`, qui n'a pas ce filet de sécurité.

**Échecs de vérification MD5 Stanza sur des paquets déjà installés** (`el`, `ur`, `vi`, `th`,
`ja`, `zh`, `fa`, `hu`, `nl`, `da`, `bg` observés le 2026-07-26, tous avec le même hash "actuel"
erroné malgré des langues différentes) : root cause initialement non confirmée, **résolue le
2026-07-27** après analyse des 12h de logs suivant le déploiement des fixes ci-dessus — 10 langues
(`de`, `fr`, `pt`, `ar`, `nl`, `it`, `id`, `gl`, `hi`, `zh`) ont échoué en rafale sur une fenêtre de
30s, retry inclus (10/10), avec le même hash "actuel" identique sur les 10 fichiers Stanza
(différents pourtant : `combined.pt`, `bosque.pt`, `padt.pt`, etc.).

**Root cause confirmée** : notre miroir GCS (`_install_from_gcs` / notebook
`notebooks/maintenance/upload_modeles_argos_translate.ipynb`) ne couvrait QUE le paquet Argos
(modèle CTranslate2 + vocab), jamais le sous-dossier `stanza/` du sentencizer — celui-ci n'est
matérialisé sur disque qu'au premier usage réel (`StanzaSentencizer.lazy_pipeline()`), après la
synchronisation du miroir, donc jamais capturé. Chaque conteneur à froid (ou paquet évincé)
retélécharge donc ces fichiers depuis `raw.githubusercontent.com/stanfordnlp/stanza-resources`
(en dur dans `stanza/resources/common.py`), qui applique un rate-limit par IP — nos Cloud Run
partagent un pool réduit d'IP de sortie NAT, et `max_instance_count` vient justement de passer de
1 à 5 (cf. section dédiée plus haut), augmentant la probabilité de cogner ce rate-limit sous charge
concurrente. Pire : `stanza.resources.common.download_file()` n'appelle `r.raise_for_status()` que
si `raise_for_status=True` — jamais passé par `argostranslate`/`stanza.Pipeline()` — donc une
réponse d'erreur/rate-limit de GitHub est écrite sur disque **telle quelle**, sans vérification du
status HTTP ; seule la vérification MD5 *a posteriori* l'attrape. Si GitHub renvoie la même page
d'erreur à plusieurs requêtes pendant la fenêtre de rate-limit, chaque langue touchée écrit le même
contenu erroné → même hash MD5, peu importe le fichier — exactement ce qui a été observé. Ça
explique aussi l'échec du retry immédiat (10/10) : s'il retombe pendant la même fenêtre de
rate-limit, il échoue pareil.

**Corrigé le 2026-07-27** : le notebook de sync force désormais `StanzaSentencizer.lazy_pipeline()`
avant l'upload GCS (matérialise `package_path/stanza/` avec les vrais fichiers + un
`resources.json` à jour), pour les paquets qui utilisent Stanza (`packaged_sbd_path` contient
"stanza"). `_install_from_gcs`/`download_directory` n'ont pas eu besoin de changer : ils
téléchargent déjà tout le sous-arbre du préfixe GCS, donc `stanza/` suit automatiquement une fois
présent sur le miroir.

**Backfill complet effectué le 2026-07-27** (10 langues initialement touchées + 36 autres paires
déjà mirrorées avant ce fix, ne téléchargeant que `metadata.json` + le sous-dossier `stanza/`
existant pour identifier les paquets Stanza sans retélécharger les gros fichiers de modèle) :
34/36 corrigées avec succès. 2 échecs, mais **pas liés au fix** — deux langues authentiquement
non supportées par Stanza, découvertes à cette occasion :
- `az` (azéri) : `stanza.Pipeline(lang='az')` lève directement *"Language az is currently
  unsupported"* — Stanza n'a aucun modèle pour cette langue.
- `bn` (bengali) : `stanza.Pipeline(lang='bn')` lève `KeyError('packages')` dans
  `stanza/resources/common.py::add_mwt` — entrée `resources.json` incomplète côté Stanza pour le
  bengali.

Même famille que `eo`/`ms`/`sw`/`tl` (retirés le 2026-07-26) : **`az` et `bn` retirés
d'`ALLOWED_LANGUAGES`** le 2026-07-27 (échec garanti à 100%, pas une histoire de miroir GCS).

**Mitigation défensive conservée en complément** (pas un remplacement du fix ci-dessus) :
`utils.cache.evict_translation_packages()` + une seule retentative dans `process_translation()`
quand l'erreur contient "md5" — utile si un paquet se corrompt malgré tout (disque, éviction
partielle, etc.), même si la cause principale identifiée est désormais couverte par le miroir GCS.
N'affecte pas les erreurs "langue non supportée" (pas de retentative inutile sur un échec garanti).

**Sévérité de log volontairement abaissée (`warning` ⚠️, pas `error` 🚨)** pour les deux issues
de la branche MD5 (échec initial retenté, et échec persistant après réinstall) : c'est un état
corrompu auto-réparable, pas un blocage définitif — la requête répond quand même 200, les autres
langues du lot continuent d'être traduites, et le paquet évincé repart d'un état propre au run
suivant. Aucune alerte `monitoring.tf` n'est indexée sur la sévérité des logs applicatifs (elles
lisent `task_attempt_count`/`dispatchCount` côté Cloud Tasks) — la vraie conséquence d'un `error`
ici est de faire remonter l'événement dans Cloud Error Reporting comme "critique", ce qui n'a pas
de sens pour une condition qui se corrige seule. Le `else` générique (erreur de traduction non
liée à une corruption MD5 connue) reste en `error` : pas de mécanisme d'auto-réparation identifié
pour ce cas.

## Lectures SQL de plusieurs minutes (2026-07-26)

Observé en prod : des lectures de **4 à 5,6 minutes** (`sql_read_ms` jusqu'à 335 721 ms) pour
lire seulement 10 à 221 lignes — largement disproportionné (la plus grosse lecture légitime
mesurée, 25 349 lignes, prend ~19s). Root cause **pas formellement confirmée** (probable
contention DB pendant les pics de charge concurrents — plusieurs instances GPU + tâches
réactiomètre en simultané au moment des incidents observés — pas de `SELECT FOR UPDATE` ni de
verrou explicite dans le code de cette CF qui expliquerait un blocage direct). Un bug similaire
en apparence (retries bloqués sur `pg_advisory_xact_lock`) a déjà été diagnostiqué le 2026-07-22
sur `gcp-fct-coordination` — mécanisme différent (verrou applicatif explicite, absent ici), mais
le correctif proposé à l'époque (borner le temps de requête) s'applique tout aussi bien ici.
**Mitigation implémentée** : `SET LOCAL statement_timeout = '120s'` juste après l'ouverture de la
session DB (`main.py::run()`) — borne toutes les lectures/écritures de la requête à 120s (>
marge sur la plus grosse lecture légitime connue, très en dessous des 234-335s observés) ; un
dépassement fait échouer la requête proprement (Cloud Tasks retente normalement) plutôt que
d'immobiliser une instance GPU entière pendant plusieurs minutes pour rien.

**Status code corrigé (2026-07-29)** : observé en prod (import 632, statement_timeout atteint pile
à 120s, résolu par un simple retry Cloud Tasks 30s plus tard) que ce timeout tombait dans le
catch-all générique de `gcpOpsci.log_and_return_response` et remontait en **500** — trompeur pour
un cas connu et déjà borné, pas un bug à corriger. `run()` attrape désormais explicitement
`sqlalchemy.exc.OperationalError` contenant `"statement timeout"` et retourne **503** (dépendance
temporairement indisponible, retry normal) au lieu de laisser remonter au fallback générique. Pas
**504** : déjà utilisé par le timeout de Cloud Run lui-même (`timeout_seconds`, cf. section
deadlock ci-dessous) — le réutiliser aurait cassé la distinction dans les logs entre "notre requête
a dépassé son propre budget DB" et "Cloud Run a tué la requête entière". Toute autre
`OperationalError` (ex. perte de connexion réelle) continue de remonter au catch-all générique.

## Fusion dynamique des petites requêtes concurrentes (2026-07-27)

**Constat** : beaucoup de tâches Cloud Scheduler de collecte ramènent de petits lots (~20 posts)
et déclenchent chacune leur propre tâche de traduction. La CF est optimisée pour traduire ~1000
documents en ~1 minute (batching GPU efficace, cf. commentaire `batch_size` dans `RequestParams`)
— de nombreux appels à 20 documents sous-exploitent donc le GPU. Synchroniser les crons de
collecte ne suffit pas : chaque collecte crée quand même sa propre tâche de traduction séparée. Un
sweep périodique (façon réactiomètre) a été envisagé puis écarté : il introduit un problème de
coordination avec les crons de collecte (sur quelles importations tourner ?) et une ambiguïté sur
"quand la traduction est terminée" si une collecte est encore en cours d'insertion.

**Vérifié avant d'implémenter** : `ctranslate2.Translator` relâche bien le GIL Python pendant le
calcul C++/GPU (doc officielle CTranslate2, "Parallelization with multiple Python threads is
possible because all computation methods release the Python GIL") — plusieurs threads Python
peuvent donc réellement s'exécuter en parallèle. Mais `inter_threads`/`max_queued_batches` ne
fusionnent PAS les requêtes entre elles : chaque appel `translate_batch()` reste son propre batch
indépendant traité par un des N workers en parallèle, pas concaténé dans un batch plus gros.

**Solution implémentée** : deux leviers combinés.
1. **Vrai parallélisme entre cycles concurrents** : `max_instance_request_concurrency` 1→8
   (`functions.tf`) + nouvelle variable d'env `ARGOS_INTER_THREADS=4` (déjà lue nativement par
   `argostranslate.settings`, aucun code à ajouter côté `cache.py` pour ce paramètre). **Piège
   repéré après coup** : `tasks.tf::libretranslate-v2.rate_limits.max_concurrent_dispatches`
   était resté à 1 (aligné à l'époque sur `max_instance_count = 1`, pas touché) — sans le relever
   à 8 aussi, Cloud Tasks n'aurait jamais dispatché plus d'1 requête à la fois vers la CF, rendant
   le coordinateur de fusion inopérant (jamais plus d'un membre par cycle). Corrigé en même temps.
2. **Fusion applicative des requêtes concurrentes** (`utils/batch_coordinator.py`, nouveau) : la
   phase de détection de langue (boucle document par document, donc un temps non nul) sert de
   fenêtre d'absorption naturelle. La première requête à arriver pour une paire
   `(target_lang, source_lang, force_translate)` devient "leader" : elle détecte la langue de ses
   propres documents, puis absorbe en un seul passage les requêtes arrivées entre-temps
   ("followers") avant de grouper par langue et traduire le tout en un seul cycle — sans pause
   artificielle. Toute requête arrivant après la clôture démarre le cycle suivant (potentiellement
   en parallèle réel du précédent, grâce au point 1). Chaque requête garde sa propre lecture ET
   écriture DB (`main.py::run()`) — seule l'étape détection+groupement+traduction est mutualisée.

`main.py::process_translation()` (devenu obsolète depuis, cf. remarque ci-dessous) a été scindée en
deux fonctions réutilisables sans changement de logique (`_detect_languages_for()` puis
`_translate_detected()`) pour permettre cette fusion.

*Mise à jour 2026-07-28 : `process_translation()` elle-même a été supprimée (plus aucun appelant —
ni `run()` ni le coordinateur, qui utilisent directement `_detect_languages_for`/`_translate_detected`,
ne passaient par ce wrapper).*

**Durcissement thread-safety associé** (`utils/cache.py`) : `PACKAGE_LAST_USED_AT` et les
décisions d'éviction/installation LRU n'étaient protégées par aucun verrou explicite (sans risque
tant que la concurrency par instance valait 1). Un `threading.RLock()` protège désormais ces
sections de comptabilité — volontairement court, le téléchargement réseau lui-même reste hors du
verrou pour ne pas sérialiser les installations de paquets différents.

**Validé empiriquement** (threads simulés, `detect_fn`/`translate_fn` injectées) : fusion réelle de
requêtes arrivant pendant la détection du leader, requêtes arrivant après clôture bien isolées dans
un nouveau cycle, échec de détection d'un follower isolé sans affecter les autres membres, échec de
la traduction fusionnée propagé à tous les membres concernés, repli en solo si le leader dépasse
`FOLLOWER_TIMEOUT_SECONDS` (60s, très supérieur à un cycle normal).

**Limites connues (acceptées)** : le `PerfTimer` de chaque follower ne reflète pas les durées
réelles de `model_load`/`inference` (mesurées dans le thread du leader) ; en cas de timeout d'un
follower (dysfonctionnement réel du leader), son membre reste enregistré dans le cycle et sera
retraité une seconde fois par le leader une fois qu'il finit — travail redondant mais inoffensif
(résultat tardif jamais lu). Scope volontairement limité au chemin "traduction de documents" —
`translate_chunks`/`process_chunk_translation` (dispatché par `analyse_id`, pas par les scrapers)
n'est pas concerné par cette fusion.

### Validation en charge réelle (Test 10, 2026-07-27) — deux problèmes trouvés, un corrigé

Premier run en conditions réelles (`batch_size=50`, fusion active) : la fusion fonctionne (log
`🧩 Cycle de traduction fusionné` observé, plusieurs requêtes bien regroupées), mais deux
problèmes concrets sont apparus :

1. **Bug de concurrence confirmé dans `argostranslate`** : `'Package' object has no attribute
   'tokenizer'` et une erreur de parsing JSON (`Expecting value: line 1 column 1`) sur des
   documents `nl`/`pl`/`pt` — pas des erreurs de perf, une vraie perte silencieuse de traduction.
   Root cause : `get_or_load_translation()` appelait `argostranslate.translate
   .get_installed_languages.cache_clear()` puis `get_translation_from_codes()` **hors verrou** —
   cette paire invalide un cache PARTAGÉ par tout le process et le reconstruit en **rescannant
   tous les paquets installés** (pas seulement celui demandé). Si un autre thread installait un
   paquet différent au même moment (fichiers partiellement écrits), le scan pouvait remonter un
   objet `Package` incomplet.
   - **1er correctif (insuffisant à lui seul)** : mise sous `_PACKAGE_STATE_LOCK` — empêche deux
     scans de se marcher dessus, mais PAS un scan de tomber pendant qu'un AUTRE paquet est encore
     en cours d'installation sous le sémaphore d'installation (mécanisme différent, non
     coordonné). **Récidive confirmée en prod** après déploiement de ce 1er correctif : erreur sur
     `nl` 1,7s après la fin de l'install de `nl_en`, pendant que `de_en`/`ca_en`/`cs_en`/`ar_en`
     étaient encore en cours de téléchargement au même instant.
   - **2e correctif (celui qui règle réellement le problème)** : `utils.cache._exclusive_of_installs()`
     — une barrière qui prend d'un coup les `_GLOBAL_INSTALL_MAX_CONCURRENT` jetons du sémaphore
     d'installation (cf. point 2) avant de scanner. Puisque toute installation en cours détient
     toujours au moins 1 jeton, prendre TOUS les jetons garantit qu'aucune installation n'est
     active nulle part dans le process avant le scan. Le téléchargement lui-même reste hors de
     cette barrière (toujours parallélisable). Validé empiriquement (une installation lente + un
     scan concurrent : le scan attend bien la fin de l'installation avant de procéder).
2. **Contention de démarrage à froid amplifiée par la concurrency** : sur une instance neuve, 4
   cycles fusionnés concurrents ont chacun lancé leur propre rafale `prefetch_packages()` (jusqu'à
   6 paquets chacun) en même temps — jusqu'à 24 installations simultanées observées sur une
   instance à 4 CPU, requêtes à 70-90s le temps que ça se débloque. **Corrigé** : un sémaphore
   GLOBAL au process (`utils.cache._GLOBAL_INSTALL_SEMAPHORE`, plafond `_GLOBAL_INSTALL_MAX_CONCURRENT=4`
   — calé sur `available_cpu=4`, l'installation fait du travail CPU-bound (dézippage, vérification
   MD5), pas seulement du réseau) protège désormais `_ensure_package_installed()` quel que soit le
   chemin d'appel (`prefetch_packages`, `get_or_load_translation` direct, retry MD5) — peu importe
   le nombre de cycles concurrents, au plus 4 installations tournent en même temps dans tout le
   process. Validé empiriquement (20 "leaders" simulés demandant chacun un paquet différent en
   même temps → pic de concurrence mesuré à 4, pas 20).

**Mesuré (Test 9 sans fusion, batch=500, vs Test 10 avec fusion, batch=50, AVANT ces deux
corrections)** : la fusion ne récupérait pas l'efficacité des gros lots —
`avg_inference_ms_per_item` ×2,38 plus cher, `avg_model_load_ms_per_item` ×4,19 plus cher avec la
fusion sur petits lots. Les deux root causes (concurrence `argostranslate` + contention de
préchargement) sont maintenant corrigées.

**Re-mesuré après ces deux corrections** (run "TestFusion50", `batch_size=50`) : `avg_model_load_ms_per_item`
bien meilleur (×1,63 au lieu de ×4,19 vs Test 9), mais `avg_inference_ms_per_item` quasi inchangé
(×2,34) — root cause : `avg_concurrency` mesurée à 3,6 sur un plafond de 8, et `fused_requests`
presque toujours 1-2 (jamais plus de 3 sur 251 cycles). Le design leader/follower ci-dessus a une
limite structurelle : le leader détecte SES documents en série AVANT d'absorber les followers, et
chaque follower reste bloqué (occupe un slot de concurrency) pendant tout le cycle — peu de
requêtes ont le temps de se rejoindre avant qu'un cycle se ferme.

## Fusion v2 (2026-07-27) : pool partagé + harvester dynamique

Remplace le design leader/follower ci-dessus (event/timeout) par un mécanisme plus simple qui
répond aux deux limites identifiées :

- **Détection vraiment parallèle** : chaque requête détecte SES PROPRES documents dans SON PROPRE
  thread — plus de détection sérielle par un "leader" pour le compte des autres.
- **Libération immédiate** : une requête qui a fini sa détection dépose son résultat dans un pool
  partagé (par clé `target_lang`/`source_lang`/`force_translate`). Si un "harvester" est déjà
  actif (quelqu'un traduit en ce moment), elle répond IMMÉDIATEMENT sans attendre et sans écrire
  elle-même en DB — son slot de concurrency Cloud Tasks se libère aussitôt. Sinon, elle DEVIENT le
  harvester : elle draine tout le pool, traduit, écrit en DB avec sa propre session, puis
  revérifie le pool — si du nouveau contenu est arrivé pendant qu'elle travaillait, elle boucle et
  le traite aussi, jusqu'à vider complètement le pool (aucun dépôt ne reste orphelin en attente
  d'un futur appel qui n'arriverait peut-être jamais).

**Risque accepté explicitement par l'utilisateur** : une requête qui dépose puis répond avant que
SES documents soient réellement traduits+écrits perd le filet de sécurité Cloud Tasks — si
l'instance recycle avant la fin du travail réel du harvester qui les a récupérés, ces documents
restent `NULL` sans retry (Cloud Tasks pense déjà la tâche réussie). Mitigation partielle
naturelle : un futur run de traduction sur la même importation les retraduira (sauf pour un
dispatch `just_translate_ids` ponctuel, jamais rejoué). Le harvester lui-même reste protégé : sa
requête HTTP reste active tout le temps qu'il traduit/écrit, donc il y a toujours au moins une
requête active sur l'instance tant qu'une traduction est réellement en cours (Cloud Run ne recycle
pas une instance qui sert une requête).

`utils/batch_coordinator.py` réécrit : `_Cycle`/`_Member`/`_register`/`_close_and_snapshot`/
`_wait_as_follower`/`_run_as_leader`/`FOLLOWER_TIMEOUT_SECONDS` supprimés, remplacés par `_Pool`
(accumulateur partagé + verrou) et une seule fonction `translate_via_coordinator()` qui retourne un
`CoordinatorOutcome` (`role` = `"deposited"` / `"harvester"` / `"solo_error"` / `"harvester_error"`).
`main.py::run()` branche sur `outcome.role` : `"deposited"` → réponse HTTP immédiate sans écriture ;
`"harvester"` → écriture faite (potentiellement pour plusieurs requêtes d'origine fusionnées,
`outcome.n_docs` peut dépasser le nombre de documents propres à cette requête) ; les cas d'erreur
réutilisent le retry/re-dispatch Cloud Tasks existant, sur `outcome.doc_ids` (peut couvrir
plusieurs requêtes d'origine pour `harvester_error`). L'écriture DB elle-même a été extraite dans
`main.py::_write_translations()` (injectée au coordinateur comme `write_fn`, même pattern que
`detect_fn`/`translate_fn`).

**Validé empiriquement** (threads simulés) : un seul harvester actif à la fois par clé ; un dépôt
arrivé PENDANT qu'un harvester traduit est bien repris par sa boucle (pas orphelin) ; un échec de
détection isolé à son déposant ; un échec de traduction/écriture du harvester remet bien le pool à
disponible (pas bloqué indéfiniment) et retourne les `doc_ids` du lot en échec pour le retry ; le
harvester reste bien bloqué (thread actif) pendant toute la durée de la traduction.

**Observabilité** : `main.py::run()` appelle désormais `timer.emit({"role": ..., ...})` aussi bien
pour `"deposited"` (avec `own_docs`) que pour `"harvester"` (avec `fused_docs`/`own_docs`) — sans
ça, les requêtes qui déposent sans traduire n'apparaîtraient jamais dans les logs `[PERF]`,
faussant le comptage `wait_for_tasks()` des notebooks de perf (`n_items_written=0` reste bien
compté comme "terminé", pas confondu avec une tâche qui n'a jamais répondu).

### Validation en charge réelle (Test "TestHarvesterV2", 2026-07-27) — résultat au-delà des attentes

248 requêtes envoyées (`batch_size=50`). **247 ont déposé et répondu en médiane 44ms** (latence
HTTP réelle, mesurée côté Cloud Run — pas une approximation). **Une seule est devenue harvester,
et l'est restée pendant toute la durée du run** (311s) : 4 vagues, 12 281/12 285 documents traduits
+ écrits en une session continue, alors qu'elle n'avait lu que 50 documents pour son propre compte.
Zéro erreur (les fixes de concurrence `argostranslate` tiennent sous une charge bien plus intense).

**`avg_inference_ms_per_item` = 18,64 ms contre 24,47 ms pour Test 9 (×0,76)** — la fusion v2 fait
mieux que l'envoi direct de gros lots, pas juste comparable. Explication : avec des requêtes tirées
en rafale rapprochée, la première à finir sa détection devient harvester et ne relâche jamais la
main tant que de nouveaux dépôts arrivent — un seul thread alimente le GPU en continu avec de gros
lots, exactement le pattern optimal.

**Nuance de fiabilité observée concrètement** : le risque accepté (perte silencieuse si l'instance
recycle) s'est retrouvé concentré sur UNE seule requête portant la quasi-totalité du run. Si elle
avait planté, seule la **vague en cours** (jusqu'à `MAX_DOCS_PER_WAVE` documents) aurait été perdue
— les vagues déjà écrites restent acquises (chaque vague commit indépendamment).

### Garde-fous ajoutés suite à cette validation (2026-07-27) — un vrai risque de deadlock permanent

Question posée par l'utilisateur après ce run : si la charge continue longtemps (lots de 50 envoyés
en continu pendant des heures), le harvester qui ne relâche jamais la main peut-il dépasser la
durée de vie de l'instance ? Oui, et c'est plus grave qu'un simple ralentissement :
`functions.tf::libretranslate-v2.timeout_seconds = 600` — Cloud Run **tue la connexion sans
préavis** au-delà (pas d'exception Python rattrapable). Si ça arrive pendant qu'un harvester
boucle, notre `finally`/nettoyage ne s'exécute JAMAIS : `harvester_active` resterait bloqué à
`True` **pour toujours** — plus aucune requête ne pourrait plus jamais redevenir harvester pour
cette langue (deadlock permanent, pas un incident ponctuel qui se résorbe tout seul).

Trois garde-fous ajoutés dans `utils/batch_coordinator.py` :
1. **`HARVESTER_MAX_SECONDS`** : le harvester rend la main proprement (même si le pool n'est pas
   vide) après ce budget — marge sous `timeout_seconds` pour finir la dernière vague + répondre.
2. **`MAX_DOCS_PER_WAVE = 3000`** : plafonne la taille d'une seule vague (calé sur un run réel,
   ~3000 docs/vague ≈ 70-80s) — sans ça, un gros backlog accumulé pourrait produire une 1ère vague
   à elle seule plus longue que `HARVESTER_MAX_SECONDS`, rendant ce garde-fou inefficace (le check
   de budget n'a lieu qu'entre deux vagues).
3. **`STALE_HARVESTER_SECONDS`** (filet de sécurité si les deux premiers ne suffisent pas, ex. bug,
   OOM, kill externe) : une requête qui trouve un harvester actif depuis plus longtemps que
   `timeout_seconds` de la CF sait que rien de légitime ne peut plus tourner — elle reprend le pool
   de force plutôt que de déposer dans un pool que plus personne ne videra jamais.

**`timeout_seconds` relevé 600→1800 dans la foulée** (même jour, question directe de
l'utilisateur : "on peut aller jusqu'à 30 minutes comme Cloud Task ?") — 1800s est le plafond de
`dispatch_deadline` que Cloud Tasks accepte lui-même. **`HARVESTER_MAX_SECONDS` fixé à 900s
(15 min)** — volontairement bien EN DEÇÀ des 1800s (pas juste une marge de sécurité étroite,
demande explicite de l'utilisateur : "pas besoin de s'approcher à 30 min"). `STALE_HARVESTER_SECONDS`
reste calé sur `timeout_seconds` (1850, +50s) — rôle différent : détecter un harvester réellement
tué par Cloud Run, pas fixer le budget normal. **`TASK_TIMEOUT_SECONDS = 1800`** ajouté dans `main.py` et passé
explicitement à chaque `CloudTask(..., timeout=TASK_TIMEOUT_SECONDS)` qui peut finir par
traduire (langdetect, chunks, dispatch_threshold, retry) — sans ça, Cloud Tasks retombe sur son
propre défaut (~10 min, plus court que ce que la CF autorise désormais) et abandonnerait
(retry) une tâche encore légitimement en train de tourner côté CF. Le dispatch initial
fire-and-forget (`timeout=540`) n'est pas concerné, il ne fait que rediriger rapidement.

Bonus au passage : `n_language_pairs`/`n_cache_hits`/`n_cache_misses` (perdus dans la réécriture du
coordinateur — `_translate_stats` était ignoré, `_` en préfixe) sont maintenant agrégés sur toutes
les vagues d'un harvester et remontés dans `CoordinatorOutcome.stats`.

**Validé empiriquement** (4 nouveaux tests) : un harvester actif depuis plus de
`STALE_HARVESTER_SECONDS` est bien repris de force par la requête suivante ; un harvester qui
dépasse `HARVESTER_MAX_SECONDS` rend bien la main avec du contenu restant, qu'un 2e harvester
prend ensuite en charge (tous les documents finissent traités, par l'un ou l'autre) ; un backlog
dépassant `MAX_DOCS_PER_WAVE` est bien découpé en plusieurs vagues bornées ; les stats de cache
s'additionnent correctement sur plusieurs vagues.

**Non fait** : re-validation en charge réelle avec ces garde-fous actifs (le run "TestHarvesterV2"
ci-dessus a eu lieu AVANT leur ajout) — à refaire après déploiement avec
`notebooks/perfs/perfs_translate/main_libretranslate_v2_fusion_vs_test9.ipynb`.

### Passation active + relève du harvester (2026-07-28)

Constat clé (question de l'utilisateur) : `timeout_seconds` (Cloud Run) est un budget **par
requête**, pas par instance — une nouvelle requête qui devient harvester repart avec son propre
budget à zéro, indépendamment de combien de temps un précédent harvester avait déjà tourné. Une
instance Cloud Run n'a pas de durée de vie maximale fixe tant qu'elle reçoit du trafic (confirmé
via la doc officielle), mais deux évènements effacent silencieusement `_pools` (simple dict en
mémoire du process, sans trace ailleurs) : l'inactivité (10 min sur GPU) et le déploiement d'une
nouvelle révision.

**Design retenu, en 2 temps (décision utilisateur explicite après plusieurs itérations)** :

1. **Relève (`HARVESTER_MAX_SECONDS=900s`, 15 min)** : ce budget ne fait PLUS s'arrêter le
   harvester lui-même. Il autorise un **DÉPOSANT** qui arrive (pas le harvester) à **voler le
   rôle** au lieu de simplement déposer et repartir : il incrémente `pool.harvester_generation`,
   réinitialise `harvester_started_at` à SON PROPRE départ (son propre budget de 28 min repart de
   zéro), et devient le nouveau harvester. L'ancien harvester continue de traduire sa vague en
   cours sans interruption brutale, mais à son prochain point de contrôle (entre deux vagues) il
   constate que `harvester_generation` a changé et s'arrête proprement — ce qu'il a déjà traduit
   reste acquis, il ne touche pas à l'état du nouveau propriétaire (y compris en cas d'erreur
   tardive sur sa propre vague, cf. tests).
   - **Si aucun dépôt n'arrive jamais pour relever** : décision utilisateur explicite — le
     harvester continue de travailler indéfiniment au-delà de 900s plutôt que de s'arrêter dans
     le vide. Théorie de l'utilisateur, validée : si le rythme des dépôts ralentit après 15 min
     (plus rien à fusionner), le harvester aura simplement fini de tout traduire lui-même avant
     d'atteindre la deadline dure ci-dessous.
2. **Deadline dure (`HARVESTER_DISPATCH_DEADLINE_SECONDS=1680s`, 28 min) — dernier recours** :
   propre à CHAQUE harvester (mesurée depuis son propre départ, pas cumulée). Si cette deadline
   est atteinte SANS qu'aucune relève n'ait eu lieu et qu'il reste des documents, le harvester
   drain tout ce qui reste et appelle `dispatch_remaining_fn(doc_ids)` (injectée depuis
   `main.py::_dispatch_remaining`, crée une Cloud Task dédiée avec `just_translate_ids=doc_ids`,
   nommée `{prefix}-j{job_id}-continuation-t{task_id}`) **avant** de rendre la main. Si ce dispatch
   échoue, le contenu drainé est réinjecté dans le pool (`_merge_back`) plutôt que perdu. 1680s
   (28 min) plutôt que les 1800s (30 min) documentés de `timeout_seconds` : marge réelle
   opérationnelle confirmée par l'utilisateur, pas une valeur théorique.

`STALE_HARVESTER_SECONDS` recalé à **1740s** (juste au-dessus de la deadline dure + marge pour le
temps du dispatch lui-même) — filet de sécurité distinct, pour le cas où même le dispatch de
dernier recours n'a pas pu s'exécuter (bug/OOM/kill externe avant que le harvester n'atteigne son
propre point de contrôle).

**Validé empiriquement** (`test_batch_coordinator_v3_guardrails.py::test_harvester_max_seconds_enables_relief`,
`test_batch_coordinator_v4_active_handoff.py` réadapté à la deadline dure,
`test_batch_coordinator_v5_relief_race.py`) :
- un déposant arrivé après `HARVESTER_MAX_SECONDS` vole bien le rôle (2 harvesters distincts,
  aucun dispatch déclenché) ;
- la deadline dure déclenche bien le dispatch exact des `doc_ids` restants, pool vidé ;
- un échec de dispatch réinjecte le contenu dans le pool (repris normalement ensuite) ;
- **cas de course le plus délicat** : une erreur sur l'ancien harvester survenant APRÈS qu'il ait
  été relevé ne touche PAS à l'état (déjà terminé proprement) du nouveau propriétaire.

### Incident "TestDeadlineDure" (2026-07-28) — collisions de nom silencieuses, ~7975 documents perdus

Validation en charge réelle de la deadline dure : `dispatch_remaining_fn` créait UNE Cloud Task
portant tout `doc_ids` (ex. 3625, puis 6610 documents). Relue côté CF, cette tâche dépassait
`dispatch_threshold` et se faisait **re-découper** par le bloc "batch_split" existant — 145 puis
265 nouvelles tâches. Sur ce run, 319 de ces tâches n'ont jamais été créées : `CloudTasks.add_task`
catch `AlreadyExists` en silence (log `DEBUG`, invisible en prod) — leur nom (`t{task_id}`,
`make_task_id` = SHA-256 du payload tronqué à 12 caractères base64) entrait en collision avec un
nom déjà pris. Root cause probable : le pool de fusion accumule les dépôts par blocs contigus
(`.extend()`) — un drain-puis-redécoupage-à-partir-de-zéro peut reproduire EXACTEMENT les mêmes
frontières de lot (mêmes `doc_ids`, même ordre) qu'un découpage antérieur, donnant le même hash
donc le même nom. 319 tâches manquantes × 25 documents ≈ 7975, quasiment exactement les 7961
documents jamais traduits mesurés en base (12285 attendus, 4324 réellement écrits).

**Fix (2026-07-28)** : `_dispatch_remaining` (main.py) découpe désormais directement en lots de
`batch_size`, une seule fois, plutôt que de créer une grosse tâche qui serait re-découpée plus
tard — élimine le 2e passage par le bloc batch_split. Le nom de chaque tâche n'est PLUS basé sur
`make_task_id` (cf. `.claude/rules/cloud-tasks-naming.md` : un nom qui n'a pas besoin de
dédupliquer par contenu ne doit pas être bâti sur ce hash) mais sur
`{prefix}-j{job_id}-continuation-{timestamp_ms}-{idx}` — un timestamp partagé par tout l'appel +
un index par lot garantissent un nom unique à chaque tentative, même si le contenu d'un lot se
répète. Respecte la convention (`job_id` toujours en 2e segment, `continuation` sans tiret interne
donc ne décale rien) — validé compatible avec le parsing positionnel de
`gcp-fct-tasks-status/utils/task_status.py` (le job est cherché à n'importe quelle position par
sécurité, donc toujours retrouvable même si le label `job_id` lui-même contient des tirets).

### Incident "8 requêtes bloquées simultanément" (2026-07-28) — deadlock dans `_exclusive_of_installs()`

Observé en prod sur le job `FoImxB4zORqs` (`translate_chunks`, `analyse_id=628`, 16 tâches
dispatchées pour 1520 chunks) : 8 requêtes — exactement `max_instance_request_concurrency=8`,
tout le budget de l'instance — démarrées entre 17:53:24 et 17:53:33, ont chacune passé la
détection de langue de leur premier chunk puis **plus produit un seul log**, jusqu'à être tuées
par Cloud Run à `timeout_seconds=1800` (504, latence ~1799,8s chacune). Le watchdog générique de
`gcpOpsci.tools.log_and_return_response` a bien loggé "toujours en cours après 28min" pour ces
mêmes 8 requêtes (1680s après leur départ) — log informatif seulement, sans effet ici : `run()` ne
déclare pas de paramètre `stop_event`, ce watchdog ne peut donc rien interrompre.

**Root cause confirmée** : `utils.cache._exclusive_of_installs()` acquiert les 4 jetons de
`_GLOBAL_INSTALL_SEMAPHORE` **séquentiellement** (boucle de 4 `.acquire()`), sous l'hypothèse
implicite qu'un seul thread à la fois exécute cette séquence. Or `get_or_load_translation()` —
appelée par **toute** requête de traduction, paquet déjà installé ou non — passe systématiquement
par ce bloc en fin de fonction. Avec 8 requêtes concurrentes (`max_instance_request_concurrency=8`),
si ≥2 threads entrelacent leurs boucles d'acquisition, chacun peut se retrouver à détenir une
partie des 4 jetons en attendant le reste — détenu par l'autre thread, bloqué symétriquement.
**Deadlock permanent** : aucun des deux threads ne relâche jamais rien, puisque ni l'un ni l'autre
n'atteint jamais le `yield` qui suit l'acquisition complète. Ce chemin est emprunté aussi bien par
`translate_chunks`/`process_chunk_translation` (touché ici) que par le coordinateur de fusion
(`_translate_detected` → `get_or_load_translation`, cf. sections précédentes) — le risque n'est
donc pas spécifique aux chunks, seulement plus probable dès que plusieurs threads y arrivent en
même temps.

**Fix (2026-07-28)** : `_EXCLUSIVE_OF_INSTALLS_LOCK` (`threading.Lock()`) sérialise désormais
l'ENTRÉE dans `_exclusive_of_installs()` — un seul thread à la fois tente de vider le sémaphore, les
autres attendent leur tour au lieu de se disputer des jetons partiels. Reproduit et validé sur un
script isolé (`threading.Barrier` forçant l'entrelacement le plus défavorable, 2 threads) : deadlock
systématique avant le fix, aucun deadlock après sur le même scénario forcé.

**Conséquence de l'incident** : ~1300 des 1520 chunks de l'analyse 628 non traduits (les 16 tâches
Cloud Tasks ont été purgées manuellement pendant l'investigation, pas de retry automatique observé
— à relancer une fois le fix déployé).

## Callback de fin de traduction pour les pipelines (2026-07-29)

**Problème** : les 4 pipelines (`coordination`, `topic-modeling`, `narrative-filtering` v1/v2)
pollaient `check_queue_status` (état des Cloud Tasks) pour savoir si leur étape de traduction
était finie. Depuis le coordinateur de fusion, ce signal est déconnecté de la réalité : une tâche
du pipeline devenue harvester garde sa Cloud Task ouverte pendant qu'elle traite des dépôts
d'AUTRES déclencheurs, longtemps après que les documents du pipeline soient déjà écrits (ou
l'inverse : un "deposited" répond avant que le harvester ait réellement écrit). Une première
tentative (`check_status`, un `COUNT` DB périodique) a été explorée puis abandonnée (coût des
checks répétés, ne gère pas les nouveaux posts arrivant entre deux checks, ne distingue pas
"pas encore traduit" de "ne sera jamais traduit").

**Solution retenue** : réintroduire `call_back_pipeline` (même contrat que `gcp-fct-translation` :
`{queue_name, task_name, url, payload}`), adapté au pool de fusion — le harvester callback le(s)
pipeline(s) dès que TOUS les documents de leur `parent_job_id` sont écrits, y compris fusionnés
dans le harvester d'un job différent.

### Tracking par job dans le pool (`utils/batch_coordinator.py`)

`_Pool` gagne trois structures, peuplées UNIQUEMENT si le déposant fournit `call_back_pipeline`
(zéro coût pour l'écrasante majorité des appels cron/bulk qui ne l'utilisent pas) :
- `doc_job_ids` (`doc_id -> parent_job_id`) : en attente de traduction.
- `in_flight_job_ids` (`doc_id -> parent_job_id`) : drainé, en cours de traduction/écriture par UN
  harvester (potentiellement pas celui qui vérifie).
- `job_callbacks` (`parent_job_id -> call_back_pipeline`).

**Race condition trouvée et corrigée (relève)** : vérifier "job X terminé" avec seulement "plus
rien dans le pool" + "plus de Cloud Task active" est insuffisant. Si harvester A drain une vague
contenant des docs du job B, est relevé PENDANT qu'il les traduit, et qu'harvester B (déjà actif)
vérifie B entre-temps, le pool ne contient plus rien pour B (c'est chez A, "in flight") et la queue
Cloud Tasks peut déjà être vide pour B → callback tiré à tort, avant écriture réelle. Le distinguo
`doc_job_ids` (en attente) / `in_flight_job_ids` (en cours d'écriture, ailleurs) empêche ce faux
positif : un job n'est "terminé" que s'il est absent des DEUX. Le check a lieu juste après
`write_fn`, dans le même bloc `_pools_lock` qui gère déjà relève/deadline pour la vague suivante.

**`_is_last_task_for_job(queue_name, job_id, is_own_job)`** généralise `_is_last_task_in_queue`
(pattern déjà utilisé par `gcp-fct-translation` et une douzaine d'autres CF feuilles du repo —
appel direct à l'API Cloud Tasks, PAS via `gcp-fct-tasks-status`) : les usages existants supposent
que le vérificateur est toujours l'une des tâches du job vérifié (seuil `<=1`, sa propre Cloud
Task compte dans le total). Ici, le harvester peut vérifier un job FUSIONNÉ qui n'est pas le sien
— sa propre Cloud Task ne contient jamais `j{job_étranger}` dans son nom, donc le seuil correct est
`==0`. Fail-closed par construction (une erreur API laisse le job "non terminé").

**Cas "0 documents détectés"** : si la détection échoue sur 100% des docs d'un job, ce job n'entre
jamais dans le pool/dans aucune vague — le check normal (après vague écrite) ne se déclenche donc
jamais. `CoordinatorOutcome.own_n_detected` expose ce compteur ; si 0, `main.py` déclenche
immédiatement `fire_callback_if_last` (toujours `is_own_job=True`, c'est forcément son propre job)
sans attendre de vague.

**Dernier recours et retry-erreur — regroupement par job d'origine** : `_dispatch_remaining` et le
retry sur `harvester_error` collaient auparavant tous les doc_ids restants sous le
`parent_job_id`/`call_back_pipeline` du harvester COURANT — un lot fusionné pouvant couvrir
plusieurs pipelines, les autres perdraient silencieusement leur callback. Les deux chemins
regroupent désormais par job d'origine (`CoordinatorOutcome.doc_job_ids`/`job_callbacks_snapshot`
pour `harvester_error` ; `remaining.doc_job_ids`/`pool.job_callbacks` passés directement à
`dispatch_remaining_fn` pour la deadline dure), chaque groupe recevant sa propre Cloud Task avec le
bon `call_back_pipeline`.

### Côté pipelines

`handle_wait_translation` (branche texte/document uniquement — **pas** la branche
`transcript`/chunks, hors scope de la fusion, qui garde son polling `check_queue_status`
inchangé) ne poll plus : réveillée par le callback, elle avance directement, sans repoll.

**Filet anti-blocage** : `_is_last_task_for_job` est fail-closed (erreur API → callback jamais
tiré). Un réveil de secours à **longue échéance (25 min)** est dispatché en parallèle du callback
(`_translation_safety_net` dans le payload) ; s'il est atteint (callback perdu), il retombe sur
`check_queue_status` avant de conclure. Différent du pattern "poll de secours" déjà utilisé
ailleurs dans ces pipelines (même délai que le callback, toujours revérifié, cf. `handle_init`
branche downloader) : ici `check_queue_status` est précisément le signal CASSÉ que le callback
remplace — le revérifier systématiquement à chaque réveil annulerait le bénéfice du callback. D'où
un délai long + un marqueur explicite, pour ne revérifier QUE si le callback s'est vraiment perdu.

## Limitations connues / pistes d'optimisation futures

- La protection par placeholders (`protect_entities`) n'est pas garantie à 100% : sur pivot
  (ex. `ro->en->fr`), le modèle intermédiaire peut occasionnellement perdre un placeholder (1 cas
  observé sur 8 documents avec emoji dans `quality_sample_libre.csv`). Un échec isolé ne dégrade
  que l'entité concernée (pas de fallback vers `NULL`, cf. point 5 de la logique métier).
- **Le texte tout-caps reste, en soi, moins bien traduit** que le même texte en casse normale,
  même avec `length_penalty=1.0` (root cause confirmée le 2026-07-23, cf. section historique
  ci-dessus) — ni la mise en minuscule ni son absence ne corrigent complètement cette perte de
  fidélité. Cas observé (construit, pas dans l'échantillon de prod) : un mot romain courant
  ("alegerile" = les élections) traduit en charabia ("ALIGIES") dès le premier saut du pivot, puis
  le saut `en->fr` suivant "abandonne" sur ce texte déjà tout-caps et le recopie tel quel plutôt
  que de le traduire — ni l'un ni l'autre n'est une sortie vide (donc non détecté par le rejet
  d'hypothèse dégénérée), juste une qualité dégradée. Limite résiduelle du modèle Argos sur ce
  type d'entrée, pas quelque chose que `batch_translate()` peut éliminer par un choix de casse.
- **[Résolu le 2026-07-24, cf. section historique ci-dessous]** ~~`batch_translate()` ne refait
  pas le découpage en phrases par document~~ — corrigé, le découpage est maintenant fait.
- La licence CC-BY 4.0 (attribution) s'applique aux modèles OPUS-MT dont sont dérivés les
  paquets Argos Translate — à valider formellement avant toute bascule de production.
- Ne remplace pas encore `gcp-fct-translation` dans les pipelines — fonction candidate à
  benchmarker (perf + qualité) avant toute bascule.
