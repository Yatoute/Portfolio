import type { Metadata } from "next";
import { ProjectCard } from "@/components/ProjectCard";
import { projects } from "@/lib/projects";

export const metadata: Metadata = {
  title: "Projets — Yatoute MINTOAMA",
  description: "Études de cas de production et projets personnels de fine-tuning et d'ingénierie IA.",
};

export default function ProjectsPage() {
  const stageProjects = projects.filter((p) => p.group === "stage");
  const persoProjects = projects.filter((p) => p.group === "perso");

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-20 px-6 py-20">
      <div className="animate-fade-up flex flex-col gap-4">
        <h1 className="text-5xl font-extrabold tracking-tight sm:text-6xl">
          <span className="text-gradient">Projets</span>
        </h1>
        <p className="max-w-2xl text-lg text-muted">
          Cinq études de cas issues d&apos;un stage en production (Opsci AI), et six
          projets personnels de fine-tuning et d&apos;ingénierie de modèles.
        </p>
      </div>

      <section className="flex flex-col gap-6">
        <h2 className="text-sm font-bold uppercase tracking-[0.2em] text-muted">Stage — Opsci AI</h2>
        <div className="grid gap-5 sm:grid-cols-2">
          {stageProjects.map((p) => (
            <ProjectCard key={p.slug} project={p} />
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-6">
        <h2 className="text-sm font-bold uppercase tracking-[0.2em] text-muted">Projets personnels</h2>
        <div className="grid gap-5 sm:grid-cols-2">
          {persoProjects.map((p) => (
            <ProjectCard key={p.slug} project={p} />
          ))}
        </div>
      </section>
    </div>
  );
}
