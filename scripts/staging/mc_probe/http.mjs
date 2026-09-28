// Tiny HTTP client for the probe's client container (a LAN caller).
//   node http.mjs METHOD URL [JSON_BODY] [--auth] [--timeout MS]
// Prints one JSON line {"status": <code or error kind>, "ms": <elapsed>, "body": <text, capped>}.
// --auth sends Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN (never printed).
const args = process.argv.slice(2);
const auth = args.includes("--auth");
const ti = args.indexOf("--timeout");
const timeout = ti >= 0 ? Number(args[ti + 1]) : 120000;
const [method, url, body] = args.filter((a, i) => !a.startsWith("--") && !(ti >= 0 && i === ti + 1));
const headers = { "content-type": "application/json" };
if (auth) headers.authorization = `Bearer ${process.env.OPENCLAW_GATEWAY_TOKEN || ""}`;
const started = Date.now();
try {
  const r = await fetch(url, { method, headers, body: body && method !== "GET" ? body : undefined, signal: AbortSignal.timeout(timeout) });
  const text = await r.text();
  console.log(JSON.stringify({ status: r.status, ms: Date.now() - started, body: text.slice(0, 50_000_000) }));
} catch (e) {
  const kind = e?.cause?.code || e?.name || "error";
  console.log(JSON.stringify({ status: kind === "ECONNREFUSED" ? "refused" : kind, ms: Date.now() - started, body: "" }));
}
