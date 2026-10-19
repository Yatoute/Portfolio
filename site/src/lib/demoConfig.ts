// Fly.io hosts the compute (torch, FastAPI, Gradio) — these demo pages call
// them as pure JSON/SSE APIs from the browser. Navigation never leaves the
// Next.js site; only background fetch() calls cross origin.
export const GPTLIGHT_API = "https://gptlight-spam-yatoute-demo.fly.dev";
export const CHATPDF_API = "https://chatpdf-yatoute-demo.fly.dev";

// Hugging Face Spaces (ZeroGPU) — same pattern (pure Gradio REST API calls,
// no iframe), GPU allocated per-request rather than a dedicated instance.
// File inputs go through POST /gradio_api/upload first (multipart, field
// "files"), then the returned path is referenced in the /call payload as
// {"path": <path>, "meta": {"_type": "gradio.FileData"}}. The GPU-gated
// /gradio_api/call step itself goes through /api/hf-call (see src/lib/hfCall.ts)
// rather than being called directly — see that route for why.
export const DETECTION_IMAGES_API = "https://yatoute-detection-images-ia.hf.space";
export const DETECTION_VIDEOS_API = "https://yatoute-detection-videos-ia.hf.space";
export const ASR_TOGOLAIS_API = "https://yatoute-asr-langues-togolaises.hf.space";
export const TRADUCTION_TOGOLAIS_API = "https://yatoute-traduction-langues-togolaises.hf.space";
export const VIDEOMINING_API = "https://yatoute-video-mining-pipeline.hf.space";
