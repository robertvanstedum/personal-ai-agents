// MC's only way out (spec_mc_agent_container v0.2 §4): an HTTP CONNECT proxy that tunnels TLS to an exact allowlist of
// model-provider hostnames and refuses everything else. MC's container sits on an internal network with no route out, so
// this allowlist is enforced by the network, not by MC's cooperation. Plain HTTP and non-443 ports are refused.
//
// #584: an allowlisted name is also resolved here and refused if ANY address it resolves to is not public (private,
// loopback, link-local incl. 169.254.169.254, CGNAT, IPv6 ULA/link-local, IPv4-mapped forms, ...); the tunnel goes to
// the checked address. The same address check (addressRefusal / checkTarget, exported) is what mc-fetch.mjs applies to
// every hop of MC's web fetch, which runs in its own container: this proxy does NOT open the web to MC's container.
// Logs one JSON line per decision: host, verdict, and for refusals the reason. Node standard library only.
import dns from 'node:dns';
import http from 'node:http';
import net from 'node:net';

const PROVIDER_HOSTS = [
  // Anthropic: Claude Code on the plan (API, OAuth token exchange)
  'api.anthropic.com', 'claude.ai', 'platform.claude.com', 'console.anthropic.com',
  // xAI: Grok OAuth and API
  'api.x.ai', 'auth.x.ai', 'accounts.x.ai',
  // OpenAI: Codex OAuth and API
  'api.openai.com', 'auth.openai.com', 'chatgpt.com',
];
const DNS_TIMEOUT_MS = 5000;

// ---- address check -------------------------------------------------------------------------------------------------

function v4Num(ip) {
  if (!net.isIPv4(ip)) return null;
  return ip.split('.').reduce((n, o) => n * 256 + Number(o), 0);
}

const V4_REFUSED = [
  ['0.0.0.0', 8],        // "this network"
  ['10.0.0.0', 8],       // RFC 1918
  ['100.64.0.0', 10],    // CGNAT (RFC 6598)
  ['127.0.0.0', 8],      // loopback
  ['169.254.0.0', 16],   // link-local, incl. 169.254.169.254 instance metadata and 169.254.169.253 VPC DNS
  ['172.16.0.0', 12],    // RFC 1918 (Docker networks live here)
  ['192.0.0.0', 24],     // IETF protocol assignments
  ['192.0.2.0', 24],     // TEST-NET-1
  ['192.88.99.0', 24],   // 6to4 relay anycast
  ['192.168.0.0', 16],   // RFC 1918
  ['198.18.0.0', 15],    // benchmarking
  ['198.51.100.0', 24],  // TEST-NET-2
  ['203.0.113.0', 24],   // TEST-NET-3
  ['224.0.0.0', 4],      // multicast
  ['240.0.0.0', 4],      // reserved, incl. 255.255.255.255
].map(([base, bits]) => [v4Num(base), bits]);

function v4Refusal(n) {
  for (const [base, bits] of V4_REFUSED) {
    const size = 2 ** (32 - bits);
    if (n >= base && n < base + size) return `${numToV4(base)}/${bits}`;
  }
  return null;
}

function numToV4(n) {
  return [24, 16, 8, 0].map((s) => Math.floor(n / 2 ** s) % 256).join('.');
}

// IPv6 text -> eight 16-bit groups, or null. Accepts "::" compression and a trailing dotted quad; refuses zone ids.
function v6Groups(ip) {
  if (!net.isIPv6(ip) || ip.includes('%')) return null;
  let text = ip.toLowerCase();
  const tail = /(\d+\.\d+\.\d+\.\d+)$/.exec(text);
  if (tail) {
    const n = v4Num(tail[1]);
    if (n === null) return null;
    text = text.slice(0, -tail[1].length) + `${Math.floor(n / 65536).toString(16)}:${(n % 65536).toString(16)}`;
  }
  const [head, rest] = text.split('::');
  const a = head ? head.split(':') : [];
  const b = rest !== undefined && rest !== '' ? rest.split(':') : [];
  const fill = text.includes('::') ? 8 - a.length - b.length : 0;
  const groups = [...a, ...Array(fill).fill('0'), ...b].map((g) => parseInt(g, 16));
  return groups.length === 8 && groups.every((g) => Number.isInteger(g) && g >= 0 && g <= 0xffff) ? groups : null;
}

function v6Refusal(g) {
  const embeddedV4 = (g[6] * 65536) + g[7];
  const zeroTo = (k) => g.slice(0, k).every((x) => x === 0);
  // IPv4-mapped ::ffff:a.b.c.d: judge the IPv4 address it carries (a private one is refused, a public one allowed).
  if (zeroTo(5) && g[5] === 0xffff) {
    const why = v4Refusal(embeddedV4);
    return why ? `IPv4-mapped ${why}` : null;
  }
  if (zeroTo(8)) return '::/128 (unspecified)';
  if (zeroTo(7) && g[7] === 1) return '::1/128 (loopback)';
  if (g[0] === 0x64 && g[1] === 0xff9b) return '64:ff9b::/32 (NAT64, carries IPv4)';
  if (g[0] === 0x2002) return '2002::/16 (6to4, carries IPv4)';
  if ((g[0] & 0xfe00) === 0xfc00) return 'fc00::/7 (unique local)';
  if ((g[0] & 0xffc0) === 0xfe80) return 'fe80::/10 (link-local)';
  if ((g[0] & 0xffc0) === 0xfec0) return 'fec0::/10 (site-local)';
  if ((g[0] & 0xff00) === 0xff00) return 'ff00::/8 (multicast)';
  if ((g[0] & 0xe000) !== 0x2000) return 'outside 2000::/3 (not global unicast)';
  if (g[0] === 0x2001 && g[1] < 0x0200) return '2001::/23 (IETF special use, incl. Teredo)';
  if (g[0] === 0x2001 && g[1] === 0x0db8) return '2001:db8::/32 (documentation)';
  if (g[0] === 0x3fff && g[1] < 0x1000) return '3fff::/20 (documentation)';
  return null;
}

/** Why `ip` may not be reached, or null when it is a public address. Anything that is not an IP is refused. */
export function addressRefusal(ip) {
  const s = String(ip || '').trim();
  const n = v4Num(s);
  if (n !== null) return v4Refusal(n);
  const g = v6Groups(s.replace(/^\[|\]$/g, ''));
  if (g) return v6Refusal(g);
  return 'not an IP address';
}

export const isPublicAddress = (ip) => addressRefusal(ip) === null;

// ---- CONNECT target ------------------------------------------------------------------------------------------------

/** "host:443" or "[v6]:443" -> {host, port}, or null when malformed. */
export function parseTarget(target) {
  const t = String(target || '');
  let m = /^\[([0-9a-fA-F:.]+)\]:(\d{1,5})$/.exec(t);
  if (m) return net.isIPv6(m[1]) ? { host: m[1].toLowerCase(), port: Number(m[2]) } : null;
  m = /^([a-zA-Z0-9_.-]{1,253}):(\d{1,5})$/.exec(t);
  return m ? { host: m[1].toLowerCase().replace(/\.$/, ''), port: Number(m[2]) } : null;
}

function defaultLookup(host) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('dns timeout')), DNS_TIMEOUT_MS);
    dns.lookup(host, { all: true, verbatim: true }, (err, addrs) => {
      clearTimeout(timer);
      if (err) reject(err); else resolve(addrs);
    });
  });
}

/**
 * Decide one destination ("host:443"). With `allow` (the proxy), the host must be on it; with allow=null (mc-fetch),
 * any host. Then resolves it (an IP literal resolves to itself), refuses if ANY address is not public, and returns the
 * checked address to connect to. {ok:true, host, address, family} or {ok:false, host, reason}.
 */
export async function checkTarget(target, { lookup = defaultLookup, allow = null } = {}) {
  const parsed = parseTarget(target);
  if (!parsed) return { ok: false, host: String(target || '').slice(0, 100), reason: 'malformed target' };
  const { host, port } = parsed;
  if (port !== 443) return { ok: false, host, reason: `port ${port} (only 443)` };
  if (allow && !new Set(allow).has(host)) return { ok: false, host, reason: 'not on the provider allowlist' };
  let addrs;
  try {
    addrs = (await lookup(host)) || [];
  } catch (e) {
    return { ok: false, host, reason: `dns: ${e.code || e.message}` };
  }
  if (!addrs.length) return { ok: false, host, reason: 'dns: no addresses' };
  for (const a of addrs) {
    const why = addressRefusal(a.address);
    if (why) return { ok: false, host, reason: `resolves to ${a.address} (${why})` };
  }
  const pick = addrs.find((a) => a.family === 4) || addrs[0];
  return { ok: true, host, address: pick.address, family: pick.family };
}

// ---- server --------------------------------------------------------------------------------------------------------

const defaultLog = (entry) => console.log(JSON.stringify({ at: new Date().toISOString(), ...entry }));
const defaultConnect = (address, family) => net.connect({ host: address, port: 443, family });

export const DEFAULT_ALLOW = Object.freeze([...PROVIDER_HOSTS]);

export function createEgressServer({
  lookup = defaultLookup,
  connect = defaultConnect,
  allow = DEFAULT_ALLOW,
  log = defaultLog,
} = {}) {
  const server = http.createServer((req, res) => {
    log({ verdict: 'refused', host: (req.headers.host || '').slice(0, 100), reason: 'plain HTTP' });
    res.writeHead(403, { 'content-type': 'text/plain' }).end('egress: plain HTTP refused\n');
  });

  server.on('connect', async (req, client, head) => {
    client.on('error', () => client.destroy());
    const v = await checkTarget(req.url, { lookup, allow });
    if (!v.ok) {
      log({ verdict: 'refused', host: v.host, reason: v.reason });
      client.end('HTTP/1.1 403 Forbidden\r\ncontent-type: text/plain\r\n\r\negress: destination not allowed\n');
      return;
    }
    // Connect to the address that was checked, not the name: no second resolution.
    const upstream = connect(v.address, v.family);
    const close = () => { upstream.destroy(); client.destroy(); };
    let established = false;
    upstream.once('connect', () => {
      established = true;
      log({ verdict: 'tunnel', host: v.host, address: v.address });
      client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head && head.length) upstream.write(head);
      upstream.pipe(client);
      client.pipe(upstream);
    });
    upstream.on('error', (e) => {
      if (!established) {
        log({ verdict: 'failed', host: v.host, address: v.address, reason: e.code || e.message });
        client.end('HTTP/1.1 502 Bad Gateway\r\ncontent-type: text/plain\r\n\r\negress: upstream unreachable\n');
        upstream.destroy();
        return;
      }
      close();
    });
    client.on('close', () => upstream.destroy());
    upstream.setTimeout(10 * 60 * 1000, close);
  });
  return server;
}

if (process.argv[1] && process.argv[1].endsWith('egress-proxy.mjs')) {
  const PORT = Number(process.env.EGRESS_PORT || 3128);
  const allow = (process.env.EGRESS_ALLOW || PROVIDER_HOSTS.join(','))
    .split(',').map((h) => h.trim().toLowerCase()).filter(Boolean);
  createEgressServer({ allow }).listen(PORT, '0.0.0.0', () => console.log(JSON.stringify({ listening: PORT, allow })));
}
