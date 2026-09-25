"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { VIDEOMINING_API } from "@/lib/demoConfig";

type Keyframe = { rank: number; frame_index: number; filename: string };
type Scene = { index: number; start_ms: number; end_ms: number; transcript: string; keyframes: Keyframe[] };
type Result = {
  metadata: { width: number; height: number; duration: number };
  language: string | null;
  scenes: Scene[];
};
type JobStatus = { status: "queued" | "processing" | "done" | "error"; stage: string; progress: number; error: string | null };

// Same ~50-language set as the production translation Cloud Function
// (everything Argos Translate can translate) — kept in sync with
// demos/video-mining/pipeline/translation.py's SUPPORTED_LANGUAGES.
const LANGUAGES: Record<string, string> = {
  ar: "Arabe", bg: "Bulgare", ca: "Catalan", cs: "Tchèque",
  da: "Danois", de: "Allemand", el: "Grec", en: "Anglais",
  es: "Espagnol", et: "Estonien", eu: "Basque", fa: "Persan",
  fi: "Finnois", fr: "Français", ga: "Irlandais", gl: "Galicien",
  he: "Hébreu", hi: "Hindi", hu: "Hongrois", id: "Indonésien",
  it: "Italien", ja: "Japonais", ko: "Coréen", ky: "Kirghize",
  lt: "Lituanien", lv: "Letton", nb: "Norvégien (Bokmål)",
  nl: "Néerlandais", pb: "Portugais (Brésil)", pl: "Polonais",
  pt: "Portugais", ro: "Roumain", ru: "Russe", sk: "Slovaque",
  sl: "Slovène", sq: "Albanais", sv: "Suédois", th: "Thaï",
  tr: "Turc", uk: "Ukrainien", ur: "Ourdou", vi: "Vietnamien",
  zh: "Chinois (simplifié)", zt: "Chinois (traditionnel)",
};

const STAGES: { key: string; label: string }[] = [
  { key: "scene_detection", label: "Détection de scènes" },
  { key: "keyframes", label: "Sélection de keyframes" },
  { key: "transcription", label: "Transcription" },
];

function Spinner() {
  return (
    <span
      className="inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent"
      style={{ color: "var(--accent)" }}
    />
  );
}

function StageTracker({ status }: { status: JobStatus }) {
  const currentIndex =
    status.status === "queued" ? -1 : STAGES.findIndex((s) => s.key === status.stage);

  return (
    <div className="flex flex-col gap-3">
      {STAGES.map((stage, i) => {
        const isDone = currentIndex > i || status.status === "done";
        const isActive = currentIndex === i && status.status === "processing";
        return (
          <div key={stage.key} className="flex items-center gap-3 text-sm">
            {isDone ? (
              <span
                className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-background"
                style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
              >
                ✓
              </span>
            ) : isActive ? (
              <Spinner />
            ) : (
              <span className="h-4 w-4 shrink-0 rounded-full border border-border" />
            )}
            <span className={isDone || isActive ? "font-semibold text-foreground" : "text-muted"}>
              {stage.label}
            </span>
            {isActive && (
              <span className="font-mono text-xs text-muted">
                {Math.round(status.progress * 100)}%
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function fmtTime(ms: number) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export default function VideoMiningDemo() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [translateSource, setTranslateSource] = useState("");
  const [translateResult, setTranslateResult] = useState("");
  const [detectedSource, setDetectedSource] = useState<string | null>(null);
  const [toLang, setToLang] = useState("en");
  const [translating, setTranslating] = useState(false);
  const [translateError, setTranslateError] = useState<string | null>(null);

  function poll(id: string) {
    if (pollRef.current) clearTimeout(pollRef.current);
    pollRef.current = setTimeout(async () => {
      try {
        const res = await fetch(`${VIDEOMINING_API}/api/jobs/${id}`);
        const data: JobStatus = await res.json();
        setStatus(data);
        if (data.status === "done") {
          const r = await fetch(`${VIDEOMINING_API}/api/jobs/${id}/result`);
          setResult(await r.json());
        } else if (data.status !== "error") {
          poll(id);
        }
      } catch {
        setUploadError("Connexion perdue avec la démo — réessayez.");
      }
    }, 2500);
  }

  async function handleUpload(file: File) {
    setUploadError(null);
    setResult(null);
    setStatus(null);
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch(`${VIDEOMINING_API}/api/upload`, { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Upload refusé.");
      setJobId(data.job_id);
      setStatus({ status: "queued", stage: "", progress: 0, error: null });
      poll(data.job_id);
    } catch (e) {
      setUploadError(
        e instanceof Error
          ? e.message
          : "La démo est peut-être en veille (elle scale à zéro) — réessayez dans une minute."
      );
    }
  }

  async function handleTranslate() {
    if (!translateSource.trim()) return;
    setTranslating(true);
    setTranslateError(null);
    setDetectedSource(null);
    try {
      const res = await fetch(`${VIDEOMINING_API}/api/translate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: translateSource, to_code: toLang }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Échec de traduction.");
      setTranslateResult(data.translated);
      setDetectedSource(data.detected_source ?? null);
    } catch (e) {
      setTranslateError(e instanceof Error ? e.message : "Échec de traduction.");
    } finally {
      setTranslating(false);
    }
  }

  const isProcessing = status && status.status !== "done" && status.status !== "error";

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-10 px-6 py-16 sm:py-24">
      <div className="animate-fade-up flex flex-col gap-4">
        <Link href="/projects/video-mining-pipeline" className="text-sm font-semibold hover:underline" style={{ color: "var(--accent)" }}>
          ← Video mining pipeline
        </Link>
        <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">
          Video <span className="text-gradient">mining</span> en direct
        </h1>
        <p className="text-lg text-muted">
          Uploadez une courte vidéo (≤3min, ≤25MB) — détection de scènes, keyframes CLIP et
          transcription WhisperX, sur CPU seul.
        </p>
      </div>

      {!jobId && (
        <div className="card-glass glow-border flex flex-col items-start gap-4 rounded-3xl p-7">
          <button
            onClick={() => fileInputRef.current?.click()}
            className="rounded-full px-6 py-3 text-sm font-bold text-background transition-transform hover:scale-105"
            style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
          >
            Choisir une vidéo
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleUpload(f);
            }}
          />
          {uploadError && <p className="text-sm text-muted">{uploadError}</p>}
        </div>
      )}

      {isProcessing && status && (
        <div className="card-glass flex flex-col gap-5 rounded-3xl p-7">
          <div className="flex items-center gap-3">
            <Spinner />
            <p className="text-sm font-semibold">
              {status.status === "queued" ? "En file d'attente..." : "Traitement en cours..."}
            </p>
          </div>
          <StageTracker status={status} />
          <p className="text-xs text-muted">
            Sur CPU, ça peut prendre 1 à 5 minutes selon la longueur du clip.
          </p>
        </div>
      )}

      {status?.status === "error" && (
        <div className="card-glass rounded-3xl p-7 text-sm text-muted">Erreur : {status.error}</div>
      )}

      {result && jobId && (
        <div className="flex flex-col gap-6">
          <div className="card-glass overflow-hidden rounded-3xl p-4">
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <video controls className="w-full rounded-xl" src={`${VIDEOMINING_API}/api/jobs/${jobId}/video`} />
            <p className="pt-3 text-xs text-muted">
              {result.metadata.width}×{result.metadata.height} · {result.metadata.duration.toFixed(1)}s ·{" "}
              {result.scenes.length} scène(s) · langue : {result.language ?? "n/a"}
            </p>
          </div>

          <div className="flex flex-col gap-4">
            {result.scenes.map((scene) => (
              <div key={scene.index} className="card-glass rounded-2xl p-5">
                <div className="flex items-baseline justify-between pb-3">
                  <span className="font-bold">Scène {scene.index + 1}</span>
                  <span className="font-mono text-xs text-muted">
                    {fmtTime(scene.start_ms)} – {fmtTime(scene.end_ms)}
                  </span>
                </div>
                {scene.keyframes.length > 0 && (
                  <div className="flex gap-2 overflow-x-auto pb-3">
                    {scene.keyframes.map((kf) => (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        key={kf.filename}
                        src={`${VIDEOMINING_API}/api/jobs/${jobId}/keyframes/${kf.filename}`}
                        alt={`Keyframe ${kf.frame_index}`}
                        className="h-24 shrink-0 rounded-lg border border-border"
                      />
                    ))}
                  </div>
                )}
                <p className={`text-sm ${scene.transcript ? "text-foreground" : "italic text-muted"}`}>
                  {scene.transcript || "(pas de parole détectée dans cette scène)"}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-col gap-4">
        <h2 className="text-sm font-bold uppercase tracking-[0.2em] text-muted">Moteur de traduction</h2>
        <div className="card-glass rounded-3xl p-7">
          <p className="pb-4 text-sm text-muted">
            Même moteur qu&apos;en production (Argos / CTranslate2, {Object.keys(LANGUAGES).length}{" "}
            langues), tournant en process. La langue source est détectée automatiquement
            (fast-langdetect) — pas besoin de la préciser.
          </p>
          <div className="grid gap-3 sm:grid-cols-[1fr_auto_1fr]">
            <div className="flex flex-col gap-2">
              <div className="flex h-[34px] items-center rounded-lg border border-border bg-background-2 px-3 text-sm text-muted">
                {detectedSource
                  ? `Détecté : ${LANGUAGES[detectedSource] ?? detectedSource}`
                  : "Langue source détectée automatiquement"}
              </div>
              <textarea
                value={translateSource}
                onChange={(e) => setTranslateSource(e.target.value)}
                placeholder="Texte à traduire..."
                rows={4}
                className="rounded-xl border border-border bg-background-2 p-3 text-sm outline-none focus:border-border-strong"
              />
            </div>
            <div className="hidden items-center justify-center sm:flex">→</div>
            <div className="flex flex-col gap-2">
              <select
                value={toLang}
                onChange={(e) => setToLang(e.target.value)}
                className="rounded-lg border border-border bg-background-2 px-3 py-1.5 text-sm"
              >
                {Object.entries(LANGUAGES).map(([code, name]) => (
                  <option key={code} value={code}>
                    {name}
                  </option>
                ))}
              </select>
              <textarea
                value={translateResult}
                readOnly
                placeholder="Traduction..."
                rows={4}
                className="rounded-xl border border-border bg-background-2 p-3 text-sm text-muted outline-none"
              />
            </div>
          </div>
          <button
            onClick={handleTranslate}
            disabled={translating || !translateSource.trim()}
            className="mt-4 rounded-full px-5 py-2 text-sm font-bold text-background transition-transform hover:scale-105 disabled:opacity-50 disabled:hover:scale-100"
            style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
          >
            {translating ? "Traduction..." : "Traduire"}
          </button>
          {translateError && <p className="pt-3 text-sm text-muted">{translateError}</p>}
        </div>
      </div>
    </div>
  );
}
