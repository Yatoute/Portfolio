export type Badge = "model" | "orchestration" | "infra" | "agent";

export const badgeLabels: Record<Badge, string> = {
  model: "Modèle open-weight fine-tuné",
  orchestration: "Orchestration API",
  infra: "Infra ML production",
  agent: "Agent autonome",
};

export type ProjectStatus = "case-study" | "live-demo" | "write-up";

export type Metric = { value: string; label: string };

export type Project = {
  slug: string;
  title: string;
  oneLiner: string;
  badge: Badge;
  status: ProjectStatus;
  metrics: Metric[];
  stack: string[];
  demoUrl?: string;
  repoUrl?: string;
  group: "stage" | "perso";
};

export const projects: Project[] = [
  {
    slug: "translation-cost-optimization",
    title: "Moteur de traduction — -90% de coût, ×77 plus rapide",
    oneLiner:
      "Remplacement d'un appel externe LibreTranslate par un moteur de traduction en process (CTranslate2/Argos, GPU) couvrant 50 langues avec détection automatique de la langue source, et un mécanisme de fusion de requêtes développé from scratch.",
    badge: "model",
    status: "live-demo",
    metrics: [
      { value: "-90%", label: "de coût infra" },
      { value: "×77", label: "plus rapide" },
      { value: "30.7ms", label: "par document" },
    ],
    stack: ["CTranslate2", "Argos Translate", "PyTorch", "GCP Cloud Functions", "Cloud Tasks"],
    demoUrl: "/demos/video-mining",
    group: "stage",
  },
  {
    slug: "video-mining-pipeline",
    title: "Pipeline de video mining à grande échelle",
    oneLiner:
      "Détection de scènes, sélection de keyframes par embeddings CLIP et transcription synchronisée, sur des milliers de vidéos par heure.",
    badge: "infra",
    status: "live-demo",
    metrics: [
      { value: "~1000", label: "vidéos/h (scènes)" },
      { value: "~500", label: "vidéos/h (transcription)" },
      { value: "×2.3", label: "moins d'appels GPU" },
    ],
    stack: ["PySceneDetect", "OpenCLIP", "WhisperX", "GCP Cloud Functions"],
    demoUrl: "/demos/video-mining",
    group: "stage",
  },
  {
    slug: "monitoring-agent",
    title: "Agent de monitoring des Cloud Functions de scraping",
    oneLiner:
      "Un agent LLM qui diagnostique et corrige lui-même les Cloud Functions de scraping en production, avec des garde-fous stricts.",
    badge: "agent",
    status: "case-study",
    metrics: [
      { value: "3", label: "niveaux de diagnostic" },
      { value: "Auto-fix", label: "du code parser" },
      { value: "//", label: "reconnexion multi-profils" },
    ],
    stack: ["Claude Code CLI", "Playwright", "Cloud Tasks", "Cloud Run Jobs"],
    group: "stage",
  },
  {
    slug: "gptlight",
    title: "GPT-Light — GPT-2 from scratch + fine-tuning",
    oneLiner:
      "Implémentation complète d'un GPT-2 en PyTorch, chargement manuel des poids officiels OpenAI, fine-tuning supervisé sur une tâche de classification.",
    badge: "model",
    status: "live-demo",
    metrics: [
      { value: "96.96%", label: "accuracy (spam)" },
      { value: "124M/355M", label: "params chargés" },
      { value: "48%→97%", label: "après fine-tuning" },
    ],
    stack: ["PyTorch", "tiktoken", "GPT-2 (OpenAI checkpoints)"],
    demoUrl: "/demos/spam-classifier",
    repoUrl: "https://github.com/Yatoute/gpt-light",
    group: "perso",
  },
  {
    slug: "chatpdf",
    title: "ChatPDF — agent RAG agentique",
    oneLiner:
      "Un agent LangGraph qui navigue dans un document (recherche sémantique, navigation entre chunks, lecture par page) plutôt qu'un simple top-k RAG.",
    badge: "orchestration",
    status: "live-demo",
    metrics: [
      { value: "5", label: "outils agent" },
      { value: "OCR", label: "intégré" },
      { value: "Auto", label: "résumé de contexte" },
    ],
    stack: ["LangGraph", "FastAPI", "FAISS", "Streamlit"],
    demoUrl: "/demos/chatpdf",
    group: "perso",
  },
  {
    slug: "queryrewriter",
    title: "QueryRewriter — fine-tuning QLoRA de Qwen",
    oneLiner:
      "Génération d'un dataset synthétique de 15k exemples et fine-tuning QLoRA d'un Qwen 9B via LLaMA-Factory sur GPU loué, pour la réécriture de requêtes conversationnelles.",
    badge: "model",
    status: "write-up",
    metrics: [
      { value: "15 391", label: "exemples générés" },
      { value: "r16/α32", label: "QLoRA" },
      { value: "A100", label: "RunPod" },
    ],
    stack: ["LLaMA-Factory", "PEFT/QLoRA", "Qwen", "sentence-transformers"],
    group: "perso",
  },
  {
    slug: "watchagent",
    title: "WatchAgent — surveillance vidéo par VLM local",
    oneLiner:
      "Un flux RTSP segmenté par vision classique (peu coûteuse), puis décrit par un VLM local (Qwen2.5-VL via Ollama) avec mémoire conversationnelle inter-scènes.",
    badge: "agent",
    status: "write-up",
    metrics: [
      { value: "CV", label: "avant VLM (coût-aware)" },
      { value: "Qwen2.5-VL", label: "local (Ollama)" },
      { value: "3", label: "scènes de mémoire" },
    ],
    stack: ["OpenCV", "Ollama", "Qwen2.5-VL", "FFmpeg"],
    group: "perso",
  },
];

export function getProject(slug: string): Project | undefined {
  return projects.find((p) => p.slug === slug);
}
