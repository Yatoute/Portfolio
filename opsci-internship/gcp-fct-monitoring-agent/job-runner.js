// job-runner.js — exécuté par le Cloud Run Job "monitoring-agent-runner".
// Draine le dossier d'état (DEMANDS_DIR, sur GCS via gcsfuse) : pour chaque demande, lance l'agent
// claude (/monitor-scrapper) qui lit/synchronise/supprime les fichiers de demande. S'arrête quand
// le dossier reste vide. Facturé uniquement pendant l'exécution (Cloud Run Job → scale-to-zero natif).

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

function logDebug(message) {
  for (const line of String(message).split("\n")) {
    if (line.trim()) process.stdout.write(JSON.stringify({ severity: "DEBUG", message: line }) + "\n");
  }
}
const log = (...a) => logDebug(a.join(" "));

const WORK_DIR    = process.env.WORK_DIR || "/workspace";
const GCP_PROJECT = process.env.GCP_PROJECT || "demo-project";
const GCP_REGION  = process.env.GCP_DB_ZONE || "europe-west1";
const DEMANDS_DIR = process.env.DEMANDS_DIR || "/tmp/monitor_demands";
const MAX_DEMAND_ATTEMPTS = 3;
const IDLE_EXIT_MS = 90 * 1000; // dossier vide ce délai (laisse gcsfuse rafraîchir + absorbe une rafale) → fin du Job

fs.mkdirSync(DEMANDS_DIR, { recursive: true });

function listDemandFiles() {
  try { return fs.readdirSync(DEMANDS_DIR).filter((f) => f.endsWith(".json")).sort(); }
  catch { return []; }
}

async function getMetadataToken() {
  const res = await fetch(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    { headers: { "Metadata-Flavor": "Google" } });
  return (await res.json()).access_token;
}

// Pause/reprise d'une file Cloud Tasks (du SCRAPPER monitoré)
async function queueOp(queueName, op) {
  try {
    const token = await getMetadataToken();
    const url = `https://cloudtasks.googleapis.com/v2/projects/${GCP_PROJECT}/locations/${GCP_REGION}/queues/${queueName}:${op}`;
    const res = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: "{}",
    });
    if (res.ok) log(`${op === "pause" ? "⏸️" : "▶️"} Queue ${queueName} ${op}`);
    else log(`⚠️ ${op} ${queueName} : ${res.status} ${await res.text()}`);
  } catch (e) { log(`⚠️ ${op} ${queueName} erreur : ${e.message}`); }
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
  if (user_prompt) parts.push(`user_prompt="${String(user_prompt).replace(/"/g, '\\"')}"`);
  if (args) parts.push(args);
  return `/monitor-scrapper ${parts.join(" ")}`.trim();
}

function makeLineParser(onLine) {
  let buf = "";
  return (chunk) => {
    buf += chunk.toString();
    const lines = buf.split("\n"); buf = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      let ev; try { ev = JSON.parse(line); } catch { continue; }
      if (ev.type === "assistant") for (const b of (ev.message?.content || [])) {
        if (b.type === "text" && b.text) onLine(b.text);
        else if (b.type === "tool_use") onLine(`🔧 ${b.name}${b.input?.description ? ": " + b.input.description : ""}`);
      }
    }
  };
}

// Rafraîchit le dossier de la CF sur origin/main avant le run (parité avec l'ancien startAgent)
function gitCheckoutCf(cfDir) {
  return new Promise((resolve) => {
    const f = spawn("git", ["-C", WORK_DIR, "fetch", "origin", "main"]);
    f.on("close", () => {
      if (!cfDir) return resolve();
      const co = spawn("git", ["-C", WORK_DIR, "checkout", "FETCH_HEAD", "--", cfDir]);
      co.on("close", resolve); co.on("error", resolve);
    });
    f.on("error", resolve);
  });
}

function runClaude(prompt) {
  return new Promise((resolve) => {
    const proc = spawn(
      "claude",
      ["--print", "--verbose", "--output-format", "stream-json", "--dangerously-skip-permissions", prompt],
      { cwd: WORK_DIR, env: { ...process.env, DEMANDS_DIR }, stdio: ["ignore", "pipe", "pipe"] });
    proc.stdout.on("data", makeLineParser(logDebug));
    proc.stderr.on("data", (c) => logDebug(`stderr: ${c.toString()}`));
    proc.on("close", (code) => resolve(code === 0));
    proc.on("error", (e) => { log(`❌ claude error : ${e.message}`); resolve(false); });
  });
}

const attempts = {}; // garde anti-boucle (demande "poison")

async function processDemand(file) {
  const p = path.join(DEMANDS_DIR, file);
  let demand;
  try { demand = JSON.parse(fs.readFileSync(p, "utf8")); }
  catch (e) { log(`⚠️ demande illisible ${file} (${e.message}) — suppression`); try { fs.unlinkSync(p); } catch {} return; }

  attempts[demand.id] = (attempts[demand.id] || 0) + 1;
  if (attempts[demand.id] > MAX_DEMAND_ATTEMPTS) {
    log(`🛑 demande ${demand.id} non traitée après ${MAX_DEMAND_ATTEMPTS} tentatives → quarantaine (.failed)`);
    try { fs.renameSync(p, `${p}.failed`); } catch { try { fs.unlinkSync(p); } catch {} }
    return;
  }

  const start = Date.now();
  log(`▶ run — primary=${demand.cf_dir || "?"} demand=${demand.id} tentative=${attempts[demand.id]} en_file=${listDemandFiles().length}`);

  // Suspendre la file du SCRAPPER (1 fois pour ce run) ; l'agent la reprend en ÉTAPE 5e
  if (demand.queue_name) await queueOp(demand.queue_name, "pause");
  await gitCheckoutCf(demand.cf_dir);
  const ok = await runClaude(buildPrompt(demand.body, demand.cf_url, demand.queue_name));
  log(`${ok ? "✅" : "❌"} run done — primary=${demand.cf_dir || "?"} duration=${((Date.now() - start) / 1000).toFixed(1)}s`);

  // Filet : agent en erreur (n'a pas atteint sa reprise 5e) → reprendre la file pour ne pas la bloquer
  if (!ok && demand.queue_name) await queueOp(demand.queue_name, "resume");

  // Progression garantie : on retire le fichier primaire (l'agent l'a normalement déjà supprimé en
  // ÉTAPE 0/6 ; unlink idempotent). Les demandes liées sont supprimées par l'agent ; les indépendantes
  // restent et seront traitées à l'itération suivante.
  try { fs.unlinkSync(p); } catch {}
}

async function main() {
  log(`🚀 job-runner démarré — DEMANDS_DIR=${DEMANDS_DIR}`);
  let lastNonEmpty = Date.now();
  while (true) {
    const files = listDemandFiles();
    if (files.length === 0) {
      if (Date.now() - lastNonEmpty > IDLE_EXIT_MS) { log("✅ dossier d'état vide — fin du Job"); return; }
      await new Promise((r) => setTimeout(r, 5000));
      continue;
    }
    lastNonEmpty = Date.now();
    await processDemand(files[0]);
  }
}

main().then(() => process.exit(0)).catch((e) => { log(`💥 job-runner crash : ${e.message}`); process.exit(1); });
