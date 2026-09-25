const express = require("express");
const { spawn } = require("child_process");
const fs = require("fs");

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 8080;

// Tous les logs applicatifs en severity=DEBUG (JSON structuré reconnu par Cloud Logging).
// Les logs gcsfuse restent en DEFAULT (texte brut du sidecar) — filtre severity="DEBUG"
// pour voir uniquement les logs applicatifs sans le bruit gcsfuse.
function logDebug(message) {
  const lines = String(message).split("\n");
  for (const line of lines) {
    if (line.trim()) process.stdout.write(JSON.stringify({ severity: "DEBUG", message: line }) + "\n");
  }
}
console.log = (...args) => logDebug(args.join(" "));
const WORK_DIR = process.env.WORK_DIR || "/workspace";
const GCP_PROJECT = process.env.GCP_PROJECT || "demo-project";
const GCP_REGION = process.env.GCP_DB_ZONE || "europe-west1";

// Config scrappers : cf_url et default_queue par CF
let SCRAPPERS_CONFIG = {};
try {
  SCRAPPERS_CONFIG = JSON.parse(fs.readFileSync("/app/config/scrappers.json", "utf8"));
} catch (e) {
  console.log(`⚠️ Config scrappers non trouvée : ${e.message}`);
}

// Résout cf_url depuis la config si non fourni dans le body
function resolveCfUrl(body) {
  if (body.cf_url) return body.cf_url;
  const cfDir = body.cf || (body.platform ? `gcp-fct-scrapper-${body.platform}` : null);
  return cfDir && SCRAPPERS_CONFIG[cfDir]?.cf_url || null;
}

// Résout queue_name : fourni explicitement, ou default_queue de la config
function resolveQueueName(body) {
  if (body.queue_name) return body.queue_name;
  const cfDir = body.cf || (body.platform ? `gcp-fct-scrapper-${body.platform}` : null);
  return cfDir && SCRAPPERS_CONFIG[cfDir]?.default_queue || null;
}

async function getMetadataToken(audience) {
  const url = `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token`;
  const res = await fetch(url, { headers: { "Metadata-Flavor": "Google" } });
  const data = await res.json();
  return data.access_token;
}

async function pauseQueue(queueName) {
  try {
    const token = await getMetadataToken();
    const url = `https://cloudtasks.googleapis.com/v2/projects/${GCP_PROJECT}/locations/${GCP_REGION}/queues/${queueName}:pause`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
      body: "{}",
    });
    if (res.ok) {
      console.log(`⏸️ Queue ${queueName} suspendue`);
    } else {
      const err = await res.text();
      console.log(`⚠️ Impossible de suspendre la queue ${queueName} : ${res.status} ${err}`);
    }
  } catch (e) {
    console.log(`⚠️ Erreur suspension queue ${queueName} : ${e.message}`);
  }
}

function buildPrompt(body, cfUrl, queueName) {
  const { cf, platform, importation_id, queries, limit, user_prompt, local, args } = body || {};
  const parts = [];
  if (cf) parts.push(`cf=${cf}`);
  else if (platform) parts.push(`cf=gcp-fct-scrapper-${platform}`);
  if (importation_id != null) parts.push(`importation_id=${importation_id}`);
  if (queries) parts.push(`queries=${JSON.stringify(queries)}`);
  if (limit != null) parts.push(`limit=${limit}`);
  if (local) parts.push("local=true");
  if (cfUrl) parts.push(`cf_url=${cfUrl}`);
  if (queueName) parts.push(`queue_name=${queueName}`);
  if (user_prompt) parts.push(`user_prompt="${user_prompt.replace(/"/g, '\\"')}"`);
  if (args) parts.push(args);
  return `/monitor-scrapper ${parts.join(" ")}`.trim();
}

function gitPull(cfDir, cb) {
  const proc = spawn("git", ["-C", WORK_DIR, "fetch", "origin", "main"], {
    env: { ...process.env },
  });
  proc.on("close", (code) => {
    if (code !== 0 || !cfDir) { cb(); return; }
    const co = spawn("git", ["-C", WORK_DIR, "checkout", "FETCH_HEAD", "--", cfDir], {
      env: { ...process.env },
    });
    co.on("close", cb);
    co.on("error", cb);
  });
  proc.on("error", cb);
}

function makeLineParser(onText, onTool) {
  let buf = "";
  return (chunk) => {
    buf += chunk.toString();
    const lines = buf.split("\n");
    buf = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      let event;
      try { event = JSON.parse(line); } catch { continue; }
      if (event.type === "assistant") {
        for (const block of (event.message?.content || [])) {
          if (block.type === "text" && block.text) {
            onText(block.text);
          } else if (block.type === "tool_use" && onTool) {
            const hint = block.input?.description || block.input?.command
                      || block.input?.file_path || block.input?.query || "";
            onTool(`🔧 ${block.name}${hint ? ": " + hint : ""}`);
          }
        }
      }
    }
  };
}

function logRequest(route, req, cfUrl, queueName) {
  const b = req.body || {};
  const user_prompt = b.user_prompt ? b.user_prompt.slice(0, 120) + (b.user_prompt.length > 120 ? "…" : "") : undefined;
  console.log(JSON.stringify({
    route,
    method: req.method,
    url: `${req.protocol}://${req.hostname}${req.originalUrl}`,
    cf: b.cf,
    cf_url: cfUrl,
    queue_name: queueName,
    importation_id: b.importation_id,
    platform: b.platform,
    user_prompt,
  }));
}

// ─────────────────────────────────────────────────────────────────────────────
// Dossier d'état des demandes (sur GCS via gcsfuse → partagé Service↔Job). Le Service ne fait QUE :
// déposer la demande + déclencher le Cloud Run Job "monitoring-agent-runner" s'il n'en tourne pas
// déjà → 200 immédiat. Le run claude (long) se fait dans le Job (facturé à la durée, scale-to-zero).
const path = require("path");
const DEMANDS_DIR = process.env.DEMANDS_DIR || "/tmp/monitor_demands";
const MONITORING_JOB = process.env.MONITORING_JOB || "monitoring-agent-runner";
fs.mkdirSync(DEMANDS_DIR, { recursive: true });

let streamRunning = false; // /monitor/stream tourne dans le Service (notebook) — exclusif avec le Job

function listDemandFiles() {
  try { return fs.readdirSync(DEMANDS_DIR).filter((f) => f.endsWith(".json")).sort(); }
  catch { return []; }
}

function sanitizeId(s) { return String(s).replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 80); }

// Écrit la demande sur disque AVANT acquittement (aucune perte si crash ensuite).
// Nom de fichier = task name Cloud Tasks si présent → idempotent (une re-livraison écrase le même fichier).
function writeDemand(req, cfUrl, queueName) {
  const b = req.body || {};
  const taskName = req.get("X-CloudTasks-TaskName") || req.get("X-AppEngine-TaskName");
  const id = taskName ? sanitizeId(taskName) : `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const cfDir = b.cf || (b.platform ? `gcp-fct-scrapper-${b.platform}` : null);
  const demand = { id, received_at: new Date().toISOString(), cf_dir: cfDir, body: b, cf_url: cfUrl, queue_name: queueName };
  fs.writeFileSync(path.join(DEMANDS_DIR, `${id}.json`), JSON.stringify(demand, null, 2));
  return demand;
}

// Une exécution du Job est-elle en cours ? ("running" = pas de completionTime, créée < 3h pour
// ignorer une exécution bloquée qui sinon empêcherait tout déclenchement futur).
async function isMonitoringJobRunning() {
  try {
    const token = await getMetadataToken();
    const url = `https://run.googleapis.com/v2/projects/${GCP_PROJECT}/locations/${GCP_REGION}/jobs/${MONITORING_JOB}/executions?pageSize=20`;
    const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) { console.log(`⚠️ list executions : ${res.status} ${await res.text()}`); return false; }
    const data = await res.json();
    const now = Date.now();
    return (data.executions || []).some((e) =>
      !e.completionTime && e.createTime && (now - Date.parse(e.createTime)) < 3 * 60 * 60 * 1000);
  } catch (e) { console.log(`⚠️ isMonitoringJobRunning : ${e.message}`); return false; }
}

// Déclenche une exécution du Cloud Run Job (il draine tout le dossier d'état puis s'arrête).
async function triggerMonitoringJob() {
  try {
    const token = await getMetadataToken();
    const url = `https://run.googleapis.com/v2/projects/${GCP_PROJECT}/locations/${GCP_REGION}/jobs/${MONITORING_JOB}:run`;
    const res = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: "{}",
    });
    if (res.ok) console.log(`🚀 Job ${MONITORING_JOB} déclenché`);
    else console.log(`⚠️ déclenchement Job ${MONITORING_JOB} : ${res.status} ${await res.text()}`);
  } catch (e) { console.log(`⚠️ triggerMonitoringJob : ${e.message}`); }
}

app.get("/health", (_req, res) => res.json({ status: "ok", pending: listDemandFiles().length, stream_running: streamRunning }));

// POST /monitor — dépose la demande dans le dossier d'état GCS, déclenche le Cloud Run Job s'il n'en
// tourne pas déjà, et répond 200 TOUT DE SUITE (la demande est durable → livraison Cloud Tasks
// terminée). Le run claude se fait dans le Job (facturé à la durée) — pas de keepalive, pas de min=1.
app.post("/monitor", async (req, res) => {
  const cfUrl     = resolveCfUrl(req.body);
  const queueName = resolveQueueName(req.body);
  logRequest("/monitor", req, cfUrl, queueName);

  const demand = writeDemand(req, cfUrl, queueName);

  if (streamRunning) {
    console.log(`stream en cours — demande ${demand.id} déposée (drainée après le stream)`);
  } else if (await isMonitoringJobRunning()) {
    console.log(`Job déjà en cours — demande ${demand.id} sera drainée par l'exécution courante`);
  } else {
    await triggerMonitoringJob();
  }

  res.status(200).json({ status: "accepted", demand_id: demand.id });
});

// POST /monitor/stream — SSE : actions de Claude Code en temps réel (notebook)
app.post("/monitor/stream", async (req, res) => {
  const cfUrl     = resolveCfUrl(req.body);
  const queueName = resolveQueueName(req.body);
  logRequest("/monitor/stream", req, cfUrl, queueName);

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();

  const { cf, platform } = req.body || {};
  const cfDir = cf || (platform ? `gcp-fct-scrapper-${platform}` : null);

  const send = (type, data) => res.write(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`);
  const start = Date.now();
  console.log(`▶ monitor/stream start — cf=${cfDir || "?"} queue=${queueName || "none"}`);

  // Exclusif avec le Job (sinon le stream du Service ET le Job toucheraient les mêmes profils GCS).
  if (streamRunning || await isMonitoringJobRunning()) {
    send("error", "Un monitoring tourne déjà (stream Service ou Job Cloud Run) — réessaie plus tard.");
    return res.end();
  }
  streamRunning = true;

  if (queueName) await pauseQueue(queueName);

  const prompt = buildPrompt(req.body, cfUrl, queueName);

  // Fin du stream : libère le verrou ; s'il reste des demandes Cloud Tasks déposées entre-temps,
  // on déclenche le Job pour les drainer.
  const finalize = () => {
    streamRunning = false;
    if (listDemandFiles().length > 0) {
      console.log(`↻ demandes en file après le stream → déclenchement du Job`);
      triggerMonitoringJob();
    }
  };

  gitPull(cfDir, () => {
    const proc = spawn("claude", ["--print", "--verbose", "--output-format", "stream-json", "--dangerously-skip-permissions", prompt], {
      cwd: WORK_DIR,
      env: { ...process.env, DEMANDS_DIR },
      stdio: ["ignore", "pipe", "pipe"],
    });

    res.on("close", () => { if (!proc.killed) proc.kill(); });

    proc.stdout.on("data", makeLineParser(
      (text) => { logDebug(text); send("output", text); },
      (toolLine) => { logDebug(toolLine); send("output", `\n${toolLine}\n`); }
    ));
    proc.stderr.on("data", (chunk) => send("error", chunk.toString()));

    proc.on("close", (code) => {
      const duration = ((Date.now() - start) / 1000).toFixed(1);
      console.log(`${code === 0 ? "✅" : "❌"} monitor/stream done — cf=${cfDir || "?"} exit=${code} duration=${duration}s`);
      send("done", { success: code === 0, exit_code: code, duration_s: parseFloat(duration) });
      res.end();
      finalize();
    });
    proc.on("error", (err) => {
      console.log(`❌ monitor/stream error — cf=${cfDir || "?"} err=${err.message}`);
      send("error", err.message);
      res.end();
      finalize();
    });
  });
});

process.on("SIGPIPE", () => {});

app.listen(PORT, () => console.log(`monitoring-agent listening on :${PORT}`));
