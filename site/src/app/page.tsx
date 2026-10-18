import Link from "next/link";
import { ProjectCard } from "@/components/ProjectCard";
import { HeroMetric } from "@/components/HeroMetric";
import { projects } from "@/lib/projects";

const modelLevelSkills = [
  "ViT fine-tuning (LoRA, LP-FT) — détection de contenu généré par IA",
  "Whisper / NLLB — ASR et traduction en contexte bas-ressource",
  "CTranslate2 — optimisation d'inférence GPU",
  "QLoRA / LoRA fine-tuning (LLaMA-Factory)",
  "GPT-2 from scratch + chargement de checkpoints officiels",
  "OpenCLIP — embeddings & sélection de keyframes",
];

const systemsSkills = [
  "LangGraph — agents RAG multi-outils",
  "Cloud Functions & Cloud Tasks (GCP)",
  "Pipelines distribués, resumable & timeout-aware",
  "Agents LLM autonomes avec garde-fous de sécurité",
];

export default function Home() {
  const featured = projects.slice(0, 4);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-28 px-6 py-20 sm:py-28">
      {/* Hero */}
      <section className="animate-fade-up flex flex-col gap-8">
        <p
          className="text-sm font-bold uppercase tracking-[0.2em]"
          style={{ color: "var(--accent)" }}
        >
          AI Engineer
        </p>
        <h1 className="max-w-4xl text-5xl font-extrabold leading-[1.05] tracking-tight sm:text-6xl md:text-7xl">
          Je fine-tune et déploie des{" "}
          <span className="text-gradient">modèles open-weight</span> en
          production.
        </h1>
        <p className="max-w-2xl text-lg leading-relaxed text-muted sm:text-xl">
          En stage chez Opsci AI, j&apos;ai diagnostiqué les angles morts d&apos;un
          détecteur d&apos;images générées par IA puis corrigé par fine-tuning LoRA
          (rappel ×6, déployé en production), avant d&apos;étendre l&apos;architecture à la
          vidéo avec une tête d&apos;attention temporelle entraînée sur 50&nbsp;000
          vidéos. En parallèle, j&apos;ai fine-tuné Whisper et NLLB pour trois langues
          togolaises sans ressources numériques (éwé, kabiyè, moba) — reconnaissance
          vocale et traduction. J&apos;ai aussi construit un moteur de traduction
          interne (-90% de coût, ×77 plus rapide), un pipeline de video mining à
          grande échelle, et un agent de monitoring autonome pour des Cloud
          Functions de scraping en production.
        </p>
        <div className="flex flex-wrap gap-3 pt-2">
          <Link
            href="/projects"
            className="rounded-full px-6 py-3 text-sm font-bold text-background transition-transform hover:scale-105"
            style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
          >
            Voir les projets
          </Link>
          <Link
            href="/cv"
            className="card-glass rounded-full px-6 py-3 text-sm font-bold transition-colors hover:border-border-strong"
          >
            Télécharger le CV
          </Link>
        </div>

        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <HeroMetric
            value="89.8%"
            label="rappel détection images IA (vs 14.4%)"
            projectSlug="detection-images-ia"
            projectName="Détection d'images IA"
          />
          <HeroMetric
            value="84.8%"
            label="rappel détection vidéos IA (vs 44.7%)"
            projectSlug="detection-videos-ia"
            projectName="Détection de vidéos IA"
          />
          <HeroMetric
            value="8.9%"
            label="WER éwé (Whisper fine-tuné)"
            projectSlug="asr-langues-togolaises"
            projectName="ASR langues togolaises"
          />
          <HeroMetric
            value="-90%"
            label="de coût infra (traduction)"
            projectSlug="translation-cost-optimization"
            projectName="Moteur de traduction"
          />
        </div>
      </section>

      {/* Positioning: model-level vs systems */}
      <section className="grid gap-6 sm:grid-cols-2">
        <div
          className="card-glass glow-border flex flex-col gap-4 rounded-3xl p-7"
          style={{ "--glow-color": "var(--badge-model-fg)" } as React.CSSProperties}
        >
          <h2 className="text-sm font-bold uppercase tracking-wide" style={{ color: "var(--badge-model-fg)" }}>
            Travail au niveau modèle
          </h2>
          <ul className="flex flex-col gap-2.5 text-sm text-muted">
            {modelLevelSkills.map((s) => (
              <li key={s} className="flex gap-2.5">
                <span style={{ color: "var(--badge-model-fg)" }}>▸</span>
                {s}
              </li>
            ))}
          </ul>
        </div>
        <div
          className="card-glass glow-border flex flex-col gap-4 rounded-3xl p-7"
          style={{ "--glow-color": "var(--badge-orchestration-fg)" } as React.CSSProperties}
        >
          <h2 className="text-sm font-bold uppercase tracking-wide" style={{ color: "var(--badge-orchestration-fg)" }}>
            Systèmes &amp; orchestration
          </h2>
          <ul className="flex flex-col gap-2.5 text-sm text-muted">
            {systemsSkills.map((s) => (
              <li key={s} className="flex gap-2.5">
                <span style={{ color: "var(--badge-orchestration-fg)" }}>▸</span>
                {s}
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* Featured projects */}
      <section className="flex flex-col gap-8">
        <div className="flex items-end justify-between">
          <h2 className="text-3xl font-extrabold tracking-tight">Projets à fort impact</h2>
          <Link href="/projects" className="text-sm font-semibold hover:underline" style={{ color: "var(--accent)" }}>
            Tous les projets →
          </Link>
        </div>
        <div className="grid gap-5 sm:grid-cols-2">
          {featured.map((p) => (
            <ProjectCard key={p.slug} project={p} />
          ))}
        </div>
      </section>
    </div>
  );
}
