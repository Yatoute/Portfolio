import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "CV — Yatoute MINTOAMA",
  description: "CV de Yatoute MINTOAMA, AI Engineer.",
};

export default function CvPage() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-16 sm:py-24">
      <h1 className="animate-fade-up text-5xl font-extrabold tracking-tight">
        <span className="text-gradient">CV</span>
      </h1>
      <p className="text-lg text-muted">Version PDF à jour, prête pour vos candidatures.</p>
      <a
        href="/cv-yatoute-mintoama.pdf"
        download
        className="w-fit rounded-full px-6 py-3 text-sm font-bold text-background transition-transform hover:scale-105"
        style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
      >
        Télécharger le CV (PDF)
      </a>
      <div className="card-glass overflow-hidden rounded-2xl">
        <iframe src="/cv-yatoute-mintoama.pdf" className="h-[80vh] w-full" title="CV Yatoute MINTOAMA" />
      </div>
    </div>
  );
}
