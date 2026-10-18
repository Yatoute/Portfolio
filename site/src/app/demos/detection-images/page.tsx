"use client";

import { useState } from "react";
import Link from "next/link";
import { DETECTION_IMAGES_API } from "@/lib/demoConfig";

type Result = { ia: number; reel: number };

async function uploadFile(file: File): Promise<string> {
  const form = new FormData();
  form.append("files", file);
  const res = await fetch(`${DETECTION_IMAGES_API}/gradio_api/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error("Upload échoué.");
  const paths: string[] = await res.json();
  return paths[0];
}

async function callPredict(path: string): Promise<Result> {
  const res = await fetch("/api/hf-call", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ space: "detection-images", data: [{ path, meta: { _type: "gradio.FileData" } }] }),
  });
  const { data, error } = await res.json();
  if (!res.ok || error) throw new Error(error || "Appel API échoué.");
  const confidences: { label: string; confidence: number }[] = data[0].confidences;
  const byLabel = Object.fromEntries(confidences.map((c) => [c.label, c.confidence]));
  return { ia: byLabel["Généré par IA"] ?? 0, reel: byLabel["Réel"] ?? 0 };
}

export default function DetectionImagesDemo() {
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
    } catch {
      setError(
        "La démo était peut-être en train de démarrer (GPU alloué à la demande) — réessayez dans quelques secondes."
      );
    } finally {
      setLoading(false);
    }
  }

  const prediction = result ? (result.ia > result.reel ? "généré par IA" : "réel") : null;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-10 px-6 py-16 sm:py-24">
      <div className="animate-fade-up flex flex-col gap-4">
        <Link href="/projects/detection-images-ia" className="text-sm font-semibold hover:underline" style={{ color: "var(--accent)" }}>
          ← Détection d&apos;images générées par IA
        </Link>
        <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">
          Détection d&apos;images <span className="text-gradient">générées par IA</span>
        </h1>
        <p className="text-lg text-muted">
          ViT-Small (Community Forensics) fine-tuné par LoRA en deux étages — rappel 89.8%, AUC
          0.971 sur jeu de test tenu à l&apos;écart.
        </p>
      </div>

      <div className="card-glass glow-border flex flex-col gap-4 rounded-3xl p-7">
        <input
          type="file"
          accept="image/*"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
          }}
          disabled={loading}
          className="text-sm text-muted file:mr-4 file:rounded-full file:border-0 file:bg-background-2 file:px-4 file:py-2 file:text-sm file:font-semibold"
        />

        {preview && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={preview} alt="Aperçu" className="max-h-80 w-fit rounded-xl border border-border object-contain" />
        )}

        {loading && <p className="text-sm text-muted">Analyse en cours...</p>}
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
