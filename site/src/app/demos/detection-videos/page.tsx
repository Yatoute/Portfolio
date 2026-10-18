"use client";

import { useState } from "react";
import Link from "next/link";
import { DETECTION_VIDEOS_API } from "@/lib/demoConfig";
import { callHfSpace } from "@/lib/hfCall";

type Result = { ia: number; reel: number };

async function uploadFile(file: File): Promise<string> {
  const form = new FormData();
  form.append("files", file);
  const res = await fetch(`${DETECTION_VIDEOS_API}/gradio_api/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error("Upload échoué.");
  const paths: string[] = await res.json();
  return paths[0];
}

async function callPredict(path: string): Promise<Result> {
  const data = await callHfSpace("detection-videos", [{ path, meta: { _type: "gradio.FileData" } }]);
  const confidences: { label: string; confidence: number }[] = (data[0] as { confidences: { label: string; confidence: number }[] }).confidences;
  const byLabel = Object.fromEntries(confidences.map((c) => [c.label, c.confidence]));
  return { ia: byLabel["Généré par IA"] ?? 0, reel: byLabel["Réel"] ?? 0 };
}

export default function DetectionVideosDemo() {
  const [preview, setPreview] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File) {
    setPreview(URL.createObjectURL(file));
    setResult(null);
    setError(null);
    setLoading(true);
    try {
      const path = await uploadFile(file);
      const r = await callPredict(path);
      setResult(r);
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : "La démo était peut-être en train de démarrer (GPU alloué à la demande) — réessayez dans quelques secondes. Le traitement prend quelques dizaines de secondes (16 frames échantillonnées, 30 premières secondes de la vidéo)."
      );
    } finally {
      setLoading(false);
    }
  }

  const prediction = result ? (result.ia > result.reel ? "généré par IA" : "réel") : null;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-10 px-6 py-16 sm:py-24">
      <div className="animate-fade-up flex flex-col gap-4">
        <Link href="/projects/detection-videos-ia" className="text-sm font-semibold hover:underline" style={{ color: "var(--accent)" }}>
          ← Détection de vidéos générées par IA
        </Link>
        <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">
          Détection de vidéos <span className="text-gradient">générées par IA</span>
        </h1>
        <p className="text-lg text-muted">
          Tête d&apos;attention temporelle sur embeddings ViT, adaptée par LoRA — rappel 84.8%
          (vs 44.7% pour une moyenne simple de scores par frame), AUC 0.939.
        </p>
      </div>

      <div className="card-glass glow-border flex flex-col gap-4 rounded-3xl p-7">
        <input
          type="file"
          accept="video/*"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
          }}
          disabled={loading}
          className="text-sm text-muted file:mr-4 file:rounded-full file:border-0 file:bg-background-2 file:px-4 file:py-2 file:text-sm file:font-semibold"
        />

        {preview && (
          <video src={preview} controls className="max-h-80 w-fit rounded-xl border border-border" />
        )}

        {loading && <p className="text-sm text-muted">Analyse en cours (échantillonnage + inférence)...</p>}
        {error && <div className="rounded-xl border border-border bg-background-2 p-4 text-sm text-muted">{error}</div>}

        {result && (
          <div className="flex flex-col gap-3 border-t border-border pt-5">
            <div className="flex items-center gap-3">
              <span
                className="rounded-full px-4 py-1.5 text-sm font-extrabold uppercase tracking-wide"
                style={{
                  color: prediction === "généré par IA" ? "var(--badge-infra-fg)" : "var(--badge-model-fg)",
                  background: prediction === "généré par IA" ? "var(--badge-infra-bg)" : "var(--badge-model-bg)",
                }}
              >
                {prediction}
              </span>
              <span className="text-sm text-muted">
                {((prediction === "généré par IA" ? result.ia : result.reel) * 100).toFixed(1)}% de confiance
              </span>
            </div>
            <div className="flex flex-col gap-2">
              {([["ia", "Généré par IA", result.ia], ["reel", "Réel", result.reel]] as const).map(
                ([key, label, value]) => (
                  <div key={key} className="flex items-center gap-3 text-xs">
                    <span className="w-24 uppercase text-muted">{label}</span>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-background-2">
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${value * 100}%`, background: "linear-gradient(90deg, var(--accent), var(--accent-2))" }}
                      />
                    </div>
                    <span className="w-12 text-right text-muted">{(value * 100).toFixed(1)}%</span>
                  </div>
                )
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
