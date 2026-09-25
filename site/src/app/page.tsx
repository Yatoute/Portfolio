import Link from "next/link";
import { ProjectCard } from "@/components/ProjectCard";
import { HeroMetric } from "@/components/HeroMetric";
import { projects } from "@/lib/projects";

const modelLevelSkills = [
  "CTranslate2 — optimisation d'inférence GPU",
  "QLoRA / LoRA fine-tuning (LLaMA-Factory)",
  "GPT-2 from scratch + chargement de checkpoints officiels",
  "WhisperX — transcription & alignement",
  "OpenCLIP — embeddings & sélection de keyframes",
  "Ollama — déploiement de VLM en local",
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
          En stage chez Opsci AI, j&apos;ai construit un moteur de traduction qui a
          réduit les coûts d&apos;infrastructure de 90% et multiplié la vitesse par 77,
          un pipeline de video mining traitant des milliers de vidéos par heure, et
          un agent de monitoring autonome qui diagnostique et corrige des Cloud
          Functions de scraping en production. En parallèle, je fine-tune des
          modèles open-source (GPT-2, Qwen) et je conçois des agents orchestrés
          avec des LLM propriétaires (LangGraph) sur des cas d&apos;usage concrets.
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
            value="-90%"
            label="de coût infra"
            projectSlug="translation-cost-optimization"
            projectName="Moteur de traduction"
          />
          <HeroMetric
            value="×77"
            label="plus rapide par doc"
            projectSlug="translation-cost-optimization"
            projectName="Moteur de traduction"
          />
          <HeroMetric
            value="96.96%"
            label="accuracy spam"
            projectSlug="gptlight"
            projectName="GPT-Light"
          />
          <HeroMetric
            value="~1000"
            label="vidéos/h traitées"
            projectSlug="video-mining-pipeline"
            projectName="Video mining"
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
