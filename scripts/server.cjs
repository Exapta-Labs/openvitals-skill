const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = parseInt(process.env.HEALTHSYNC_PORT || "18801", 10);
const LOCAL_TOKEN = process.env.HEALTHSYNC_TOKEN || "";
const DEBUG = process.env.HEALTHSYNC_DEBUG === "1";
const DATA_DIR = process.env.HEALTHSYNC_DATA_DIR || path.join(process.env.HOME || "", ".openclaw", "workspace", "healthsync-server", "data");

if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

const CORS_ORIGIN = DEBUG ? "*" : "";

const server = http.createServer((req, res) => {
  if (CORS_ORIGIN) {
    res.setHeader("Access-Control-Allow-Origin", CORS_ORIGIN);
    res.setHeader("Access-Control-Allow-Methods", "POST, GET, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
  }
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }

  // Auth check (if LOCAL_TOKEN is set)
  if (LOCAL_TOKEN && req.url !== "/") {
    const auth = req.headers.authorization || "";
    if (!auth.startsWith("Bearer ") || auth.slice(7).trim() !== LOCAL_TOKEN) {
      res.writeHead(401, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "unauthorized" }));
      return;
    }
  }

  // Health check
  if (req.method === "GET" && req.url === "/") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok", service: "HealthSync" }));
    return;
  }

  // Receive health data
  if (req.method === "POST" && req.url === "/api/health-sync") {
    let body = "";
    req.on("data", c => { if (body.length < 1e6) body += c; }); // 1MB limit
    req.on("end", () => {
      try {
        const data = JSON.parse(body);
        const ts = new Date().toISOString().replace(/[:.]/g, "-");
        const file = path.join(DATA_DIR, `sync-${ts}.json`);
        fs.writeFileSync(file, JSON.stringify(data, null, 2));
        console.log(`[${new Date().toISOString()}] Sync received: ${Object.keys(data).length} fields`);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "ok", received: Object.keys(data).length }));
      } catch {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "invalid_payload" }));
      }
    });
    return;
  }

  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "not_found" }));
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`HealthSync server listening on 0.0.0.0:${PORT}`);
  if (LOCAL_TOKEN) console.log("Auth: Bearer token required");
  else console.log("Auth: disabled (set HEALTHSYNC_TOKEN to enable)");
});
