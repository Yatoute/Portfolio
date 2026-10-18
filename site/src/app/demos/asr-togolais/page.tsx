"use client";

import { useState } from "react";
import Link from "next/link";
import { ASR_TOGOLAIS_API } from "@/lib/demoConfig";
import { callHfSpace } from "@/lib/hfCall";

const LANGUAGES: { key: string; label: string }[] = [
  { key: "ewe", label: "Éwé" },
  { key: "kabiye", label: "Kabiyè" },
  { key: "moba", label: "Moba" },
];

async function uploadFile(file: File): Promise<string> {
  const form = new FormData();
  form.append("files", file);
  const res = await fetch(`${ASR_TOGOLAIS_API}/gradio_api/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error("Upload échoué.");
  const paths: string[] = await res.json();
  return paths[0];
}

async function callTranscribe(path: string, language: string): Promise<string> {
  const data = await callHfSpace("asr-togolais", [{ path, meta: { _type: "gradio.FileData" } }, language]);
  return data[0] as string;
}

export default function AsrTogolaisDemo() {
  const [language, setLanguage] = useState(LANGUAGES[0].key);
  const [fileName, setFileName] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File) {
    setFileName(file.name);
    setTranscript(null);
    setError(null);
    setLoading(true);
    try {
      const path = await uploadFile(file);
      const text = await callTranscribe(path, language);
      setTranscript(text);
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : "La démo était peut-être en train de démarrer (GPU alloué à la demande, premier appel plus lent le temps de télécharger le modèle Silero VAD) — réessayez dans quelques secondes."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-10 px-6 py-16 sm:py-24">
      <div className="animate-fade-up flex flex-col gap-4">
        <Link href="/projects/asr-langues-togolaises" className="text-sm font-semibold hover:underline" style={{ color: "var(--accent)" }}>
          ← ASR langues togolaises
        </Link>
        <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">
          ASR pour l&apos;<span className="text-gradient">éwé, le kabiyè et le moba</span>
        </h1>
        <p className="text-lg text-muted">
          Whisper (medium) fine-tuné — modèle multilingue joint, WER global 16.3% sur ces trois
          langues absentes de tout modèle pré-entraîné.
        </p>
      </div>

      <div className="card-glass glow-border flex flex-col gap-4 rounded-3xl p-7">
        <div className="flex flex-wrap gap-2">
          {LANGUAGES.map((l) => (
            <button
              key={l.key}
              onClick={() => setLanguage(l.key)}
              className="rounded-full border px-4 py-1.5 text-sm font-semibold transition-colors"
              style={{
                borderColor: language === l.key ? "var(--accent)" : "var(--border)",
                color: language === l.key ? "var(--accent)" : "var(--muted)",
              }}
            >
              {l.label}
            </button>
          ))}
        </div>

        <input
          type="file"
          accept="audio/*"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
          }}
          disabled={loading}
          className="text-sm text-muted file:mr-4 file:rounded-full file:border-0 file:bg-background-2 file:px-4 file:py-2 file:text-sm file:font-semibold"
        />
        {fileName && <p className="text-xs text-muted">{fileName}</p>}

        {loading && <p className="text-sm text-muted">Transcription en cours...</p>}
        {error && <div className="rounded-xl border border-border bg-background-2 p-4 text-sm text-muted">{error}</div>}

        {transcript !== null && (
          <div className="flex flex-col gap-2 border-t border-border pt-5">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted">Transcription</span>
            <p className="rounded-xl border border-border bg-background-2 p-4 text-sm">{transcript}</p>
          </div>
        )}
      </div>
    </div>
  );
}
