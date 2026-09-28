// Polls the probe's LAN listener the way a CoS caller reaches it (another
// container on the network), once a second, and prints each change:
//   <iso time> <state>   where state is "refused", "http <code>" or an error kind.
// Usage: node lan-poller.mjs http://mc-probe:18789/v1/models <seconds> [token-env-name]
const [url, seconds = "400", tokenEnv = "OPENCLAW_GATEWAY_TOKEN"] = process.argv.slice(2);
const token = process.env[tokenEnv] || "";
const end = Date.now() + Number(seconds) * 1000;
let last = "";
while (Date.now() < end) {
  let state;
  try {
    const r = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {}, signal: AbortSignal.timeout(3000) });
    state = `http ${r.status}`;
  } catch (e) {
    const code = e?.cause?.code || e?.name || "error";
    state = code === "ECONNREFUSED" ? "refused" : code;
  }
  if (state !== last) { console.log(`${new Date().toISOString()} ${state}`); last = state; }
  await new Promise((res) => setTimeout(res, 1000));
}
console.log(`${new Date().toISOString()} end ${last}`);
