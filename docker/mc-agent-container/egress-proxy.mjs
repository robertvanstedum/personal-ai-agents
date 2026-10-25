// MC's only way out (spec_mc_agent_container v0.2 §4): an HTTP CONNECT proxy that tunnels TLS to an exact allowlist of
// model-provider hostnames and refuses everything else. MC's container sits on an internal network with no route out, so
// this allowlist is enforced by the network, not by MC's cooperation. Plain HTTP and non-443 ports are refused.
// Logs one line per decision (host and verdict only). Node standard library only.
import http from 'node:http';
import net from 'node:net';

const PORT = Number(process.env.EGRESS_PORT || 3128);
const ALLOW = new Set((process.env.EGRESS_ALLOW || [
  // Anthropic: Claude Code on the plan (API, OAuth token exchange)
  'api.anthropic.com', 'claude.ai', 'platform.claude.com', 'console.anthropic.com',
  // xAI: Grok OAuth and API
  'api.x.ai', 'auth.x.ai', 'accounts.x.ai',
  // OpenAI: Codex OAuth and API
  'api.openai.com', 'auth.openai.com', 'chatgpt.com',
].join(',')).split(',').map((h) => h.trim().toLowerCase()).filter(Boolean));

const log = (verdict, host) => console.log(JSON.stringify({ at: new Date().toISOString(), verdict, host }));

export function allowed(target) {
  const m = /^([a-z0-9.-]+):(\d+)$/i.exec(target || '');
  if (!m) return null;
  const host = m[1].toLowerCase();
  return m[2] === '443' && ALLOW.has(host) ? host : null;
}

const server = http.createServer((req, res) => {
  log('refused-plain-http', (req.headers.host || '').slice(0, 100));
  res.writeHead(403, { 'content-type': 'text/plain' }).end('egress: plain HTTP refused\n');
});

server.on('connect', (req, client, head) => {
  const host = allowed(req.url);
  if (!host) {
    log('refused', String(req.url).slice(0, 100));
    client.end('HTTP/1.1 403 Forbidden\r\ncontent-type: text/plain\r\n\r\negress: destination not allowed\n');
    return;
  }
  const upstream = net.connect(443, host, () => {
    log('tunnel', host);
    client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
    if (head && head.length) upstream.write(head);
    upstream.pipe(client);
    client.pipe(upstream);
  });
  const close = () => { upstream.destroy(); client.destroy(); };
  upstream.on('error', close);
  client.on('error', close);
  upstream.setTimeout(10 * 60 * 1000, close);
});

if (process.argv[1] && process.argv[1].endsWith('egress-proxy.mjs')) {
  server.listen(PORT, '0.0.0.0', () => console.log(JSON.stringify({ listening: PORT, allow: [...ALLOW] })));
}
