"use client";

import { useState } from "react";
import Link from "next/link";
import { VIDEOMINING_API } from "@/lib/demoConfig";
import { callHfSpace } from "@/lib/hfCall";

type Scene = { index: number; start_s: number; end_s: number; transcript: string; keyframes: string[] };
type Result = {
  metadata: { width: number; height: number; duration: number; language: string | null };
  scenes: Scene[];
};

// Same ~50-language set as the production translation Cloud Function
// (everything Argos Translate can translate) — kept in sync with
// hf-spaces/video-mining-pipeline/pipeline/translation.py's SUPPORTED_LANGUAGES.
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

function Spinner() {
  return (
    <span
      className="inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent"
      style={{ color: "var(--accent)" }}
    />
  );
}

function fmtTime(s: number) {
  const sec = Math.floor(s);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

async function uploadFile(file: File): Promise<string> {
  const form = new FormData();
  form.append("files", file);
  const res = await fetch(`${VIDEOMINING_API}/gradio_api/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error("Upload échoué.");
  const paths: string[] = await res.json();
  return paths[0];
}

async function callAnalyze(path: string): Promise<Result> {
  const data = await callHfSpace("video-mining-analyze", [{ path, meta: { _type: "gradio.FileData" } }]);
  return data[0] as Result;
}

async function callTranslate(text: string, toCode: string): Promise<{ translated: string; detectedSource: string }> {
  const data = await callHfSpace("video-mining-translate", [text, "Auto-détection", LANGUAGES[toCode]]);
  const [translated, detectedLabel] = data as [string, string];
  const codeToLabel = Object.fromEntries(Object.entries(LANGUAGES).map(([c, l]) => [l, c]));
  return { translated, detectedSource: codeToLabel[detectedLabel] ?? detectedLabel };
}

export default function VideoMiningDemo() {
  const [preview, setPreview] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [translateSource, setTranslateSource] = useState("");
  const [translateResult, setTranslateResult] = useState("");
  const [detectedSource, setDetectedSource] = useState<string | null>(null);
  const [toLang, setToLang] = useState("en");
  const [translating, setTranslating] = useState(false);
  const [translateError, setTranslateError] = useState<string | null>(null);

  async function handleUpload(file: File) {
    setPreview(URL.createObjectURL(file));
    setResult(null);
    setError(null);
    setLoading(true);
    try {
      const path = await uploadFile(file);
      const r = await callAnalyze(path);
      setResult(r);
    } catch (e) {
      setError(
        e instanceof Error && e.message
          ? e.message
          : "La démo était peut-être en train de démarrer (GPU alloué à la demande) — réessayez dans quelques secondes."
      );
    } finally {
      setLoading(false);
    }
  }

  async function handleTranslate() {
    if (!translateSource.trim()) return;
    setTranslating(true);
    setTranslateError(null);
    setDetectedSource(null);
    try {
      const { translated, detectedSource: src } = await callTranslate(translateSource, toLang);
      setTranslateResult(translated);
      setDetectedSource(src);
    } catch (e) {
      setTranslateError(e instanceof Error && e.message ? e.message : "Échec de traduction.");
    } finally {
      setTranslating(false);
    }
  }

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
          Uploadez une courte vidéo (≤3min) — détection de scènes, keyframes CLIP et transcription
          WhisperX, sur GPU à la demande (ZeroGPU).
        </p>
      </div>

      <div className="card-glass glow-border flex flex-col gap-4 rounded-3xl p-7">
        <input
          type="file"
          accept="video/*"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleUpload(f);
          }}
          disabled={loading}
          className="text-sm text-muted file:mr-4 file:rounded-full file:border-0 file:bg-background-2 file:px-4 file:py-2 file:text-sm file:font-semibold"
        />

        {preview && (
          // eslint-disable-next-line jsx-a11y/media-has-caption
          <video controls className="max-h-80 w-fit rounded-xl border border-border" src={preview} />
        )}

        {loading && (
          <div className="flex items-center gap-3 text-sm text-muted">
            <Spinner />
            Analyse en cours (scènes, keyframes CLIP, transcription) — peut prendre 1 à 2 minutes.
          </div>
        )}
        {error && <div className="rounded-xl border border-border bg-background-2 p-4 text-sm text-muted">{error}</div>}
      </div>

      {result && result.scenes.length > 0 && (
        <div className="flex flex-col gap-6">
          <p className="text-sm text-muted">
            {result.metadata.width}×{result.metadata.height} · {result.metadata.duration.toFixed(1)}s ·{" "}
            {result.scenes.length} scène(s) · langue : {result.metadata.language ?? "n/a"}
          </p>
          <div className="flex flex-col gap-4">
            {result.scenes.map((scene) => (
              <div key={scene.index} className="card-glass rounded-2xl p-5">
                <div className="flex items-baseline justify-between pb-3">
                  <span className="font-bold">Scène {scene.index + 1}</span>
                  <span className="font-mono text-xs text-muted">
                    {fmtTime(scene.start_s)} – {fmtTime(scene.end_s)}
                  </span>
                </div>
                {scene.keyframes.length > 0 && (
                  <div className="flex gap-2 overflow-x-auto pb-3">
                    {scene.keyframes.map((uri, i) => (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img key={i} src={uri} alt={`Keyframe scène ${scene.index + 1}`} className="h-24 shrink-0 rounded-lg border border-border" />
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
            Même moteur qu&apos;en production (Argos / CTranslate2, {Object.keys(LANGUAGES).length} langues) —
            la langue source est détectée automatiquement.
          </p>
          <div className="grid gap-3 sm:grid-cols-[1fr_auto_1fr]">
            <div className="flex flex-col gap-2">
              <div className="flex h-[34px] items-center rounded-lg border border-border bg-background-2 px-3 text-sm text-muted">
                {detectedSource ? `Détecté : ${LANGUAGES[detectedSource] ?? detectedSource}` : "Langue source détectée automatiquement"}
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
                  <option key={code} value={code}>{name}</option>
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
