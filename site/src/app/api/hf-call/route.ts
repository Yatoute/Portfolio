import { NextRequest, NextResponse } from "next/server";

// Dispatches the GPU-gated /gradio_api/call/<fn> step of a Hugging Face Space
// server-side so the request carries HF_TOKEN (never exposed to the browser).
// Anonymous calls to a ZeroGPU Space share a tiny, easily-exhausted quota
// ("ZeroGPU quota exceeded") — authenticating with the owner's token uses the
// account's real ZeroGPU allowance instead.
//
// Only the dispatch (POST, returns immediately with an event_id) goes through
// this route — the actual wait for the result happens client-side, polling
// the Space directly (see src/lib/hfCall.ts). That poll doesn't need auth and
// can take as long as a slow transcription/generation needs, unconstrained by
// Vercel's serverless function duration limit (60s on Hobby).
//
// File uploads (/gradio_api/upload) are NOT quota-gated and stay direct from
// the browser to the Space too — both because they don't need auth and to
// avoid Vercel's serverless body-size limit on large video/audio uploads.
export const maxDuration = 30;

const SPACE_URLS: Record<string, string> = {
  "detection-images": "https://yatoute-detection-images-ia.hf.space",
  "detection-videos": "https://yatoute-detection-videos-ia.hf.space",
  "asr-togolais": "https://yatoute-asr-langues-togolaises.hf.space",
  "traduction-togolais": "https://yatoute-traduction-langues-togolaises.hf.space",
};

const ALLOWED_FNS: Record<string, string> = {
  "detection-images": "predict",
  "detection-videos": "predict",
  "asr-togolais": "transcribe",
  "traduction-togolais": "translate",
};

export async function POST(req: NextRequest) {
  const { space, data } = await req.json();
  const baseUrl = SPACE_URLS[space];
  const fn = ALLOWED_FNS[space];
  if (!baseUrl || !fn || !Array.isArray(data)) {
    return NextResponse.json({ error: "Requête invalide." }, { status: 400 });
  }

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (process.env.HF_TOKEN) headers.Authorization = `Bearer ${process.env.HF_TOKEN}`;

  const callRes = await fetch(`${baseUrl}/gradio_api/call/${fn}`, {
    method: "POST",
    headers,
    body: JSON.stringify({ data }),
  });
  if (!callRes.ok) {
    return NextResponse.json({ error: `Appel API échoué (${callRes.status}).` }, { status: 502 });
  }
  const { event_id } = await callRes.json();

  return NextResponse.json({ pollUrl: `${baseUrl}/gradio_api/call/${fn}/${event_id}` });
}
