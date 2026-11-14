// #584: the address check MC's web fetch applies to every hop (shared by mc-fetch.mjs), and MC's egress proxy, which
// stays allowlist-only (model providers) and now also refuses an allowlisted name that resolves to a private address.
// Synthetic only: fake DNS answers, and "upstream" is a local plain-HTTP server reached through the tunnel. No network.
import assert from 'node:assert/strict';
import http from 'node:http';
import net from 'node:net';
import test from 'node:test';
import { addressRefusal, checkTarget, createEgressServer, DEFAULT_ALLOW, isPublicAddress, parseTarget } from '../../docker/mc-agent-container/egress-proxy.mjs';

const REFUSED = {
  'RFC1918 10/8': '10.1.2.3',
  'RFC1918 172.16/12 low': '172.16.0.1',
  'RFC1918 172.16/12 high': '172.31.255.255',
  'RFC1918 192.168/16': '192.168.1.1',
  'loopback': '127.0.0.1',
  'loopback other': '127.255.0.9',
  'metadata': '169.254.169.254',
  'VPC DNS': '169.254.169.253',
  'link-local': '169.254.1.1',
  'CGNAT low': '100.64.0.1',
  'CGNAT high': '100.127.255.255',
  'this network': '0.0.0.0',
  'multicast': '224.0.0.1',
  'broadcast': '255.255.255.255',
  'TEST-NET-3': '203.0.113.7',
  'IPv6 loopback': '::1',
  'IPv6 unspecified': '::',
  'IPv6 ULA': 'fc00::1',
  'IPv6 ULA EC2 metadata': 'fd00:ec2::254',
  'IPv6 link-local': 'fe80::1',
  'IPv6 multicast': 'ff02::1',
  'IPv6 documentation': '2001:db8::1',
  'Teredo': '2001:0:4136:e378::1',
  'IPv4-mapped metadata': '::ffff:169.254.169.254',
  'IPv4-mapped metadata (hex)': '::ffff:a9fe:a9fe',
  'IPv4-mapped RFC1918': '::ffff:10.0.0.1',
  'IPv4-mapped loopback': '::ffff:127.0.0.1',
  'IPv4-mapped CGNAT': '::ffff:100.64.0.1',
  'IPv4-mapped 172.16': '::FFFF:172.20.0.2',
  'NAT64 metadata': '64:ff9b::a9fe:a9fe',
  '6to4 metadata': '2002:a9fe:a9fe::1',
  'IPv4-compatible': '::169.254.169.254',
  'zone id': 'fe80::1%eth0',
  'not an IP': 'example.com',
};

const PUBLIC = ['93.184.215.14', '1.1.1.1', '8.8.8.8', '172.15.255.255', '172.32.0.1', '100.63.255.255',
  '100.128.0.1', '169.255.0.1', '2606:4700:4700::1111', '2a00:1450:4001:80b::200e', '::ffff:1.1.1.1'];

test('each refused range is refused', () => {
  for (const [name, ip] of Object.entries(REFUSED)) {
    assert.ok(addressRefusal(ip), `${name} (${ip}) should be refused`);
    assert.equal(isPublicAddress(ip), false, name);
  }
});

test('public addresses are allowed', () => {
  for (const ip of PUBLIC) assert.equal(addressRefusal(ip), null, `${ip} should be public`);
});

test('CONNECT targets parse strictly', () => {
  assert.deepEqual(parseTarget('Docs.Example.org:443'), { host: 'docs.example.org', port: 443 });
  assert.deepEqual(parseTarget('[2606:4700::1111]:443'), { host: '2606:4700::1111', port: 443 });
  for (const bad of ['', 'example.com', 'exa mple.com:443', 'a@b:443', '[nothex]:443', 'http://x:443']) {
    assert.equal(parseTarget(bad), null, bad);
  }
});

const dnsOf = (table) => async (host) => {
  if (!(host in table)) throw Object.assign(new Error('not found'), { code: 'ENOTFOUND' });
  return table[host].map((address) => ({ address, family: net.isIPv6(address) ? 6 : 4 }));
};

const DNS = dnsOf({
  'docs.example.org': ['93.184.215.14'],
  'internal.example.org': ['10.0.0.5'],
  'metadata-alias.example.org': ['169.254.169.254'],
  'mixed.example.org': ['93.184.215.14', '192.168.0.10'],
  'ula.example.org': ['fd00:ec2::254'],
  'mapped.example.org': ['::ffff:169.254.169.254'],
  'v6.example.org': ['2606:4700:4700::1111', '1.1.1.1'],
  'api.anthropic.com': ['160.79.104.10'],
  '169.254.169.254': ['169.254.169.254'],
});

test('a public host is allowed and pinned to the checked address (IPv4 preferred)', async () => {
  assert.deepEqual(await checkTarget('docs.example.org:443', { lookup: DNS }),
    { ok: true, host: 'docs.example.org', address: '93.184.215.14', family: 4 });
  assert.equal((await checkTarget('v6.example.org:443', { lookup: DNS })).address, '1.1.1.1');
});

test('DNS that resolves to a private, metadata, ULA or mapped address is refused', async () => {
  for (const host of ['internal.example.org', 'metadata-alias.example.org', 'ula.example.org', 'mapped.example.org']) {
    const v = await checkTarget(`${host}:443`, { lookup: DNS });
    assert.equal(v.ok, false, host);
    assert.match(v.reason, /^resolves to /, host);
  }
});

test('one private address among public ones refuses the host', async () => {
  const v = await checkTarget('mixed.example.org:443', { lookup: DNS });
  assert.equal(v.ok, false);
  assert.match(v.reason, /192\.168\.0\.10/);
});

test('non-443 ports, malformed targets and DNS failures are refused', async () => {
  assert.match((await checkTarget('docs.example.org:80', { lookup: DNS })).reason, /port 80/);
  assert.match((await checkTarget('docs.example.org:22', { lookup: DNS })).reason, /port 22/);
  assert.equal((await checkTarget('garbage', { lookup: DNS })).ok, false);
  assert.match((await checkTarget('nowhere.example.org:443', { lookup: DNS })).reason, /^dns: ENOTFOUND/);
});

test('numeric host forms that the system resolver turns into metadata are refused (real resolver, no network)', async () => {
  // getaddrinfo reads "2852039166" and "0xa9fea9fe" as 169.254.169.254; whatever it does, nothing may be admitted.
  for (const target of ['2852039166:443', '0xa9fea9fe:443', '169.254.169.254:443', '127.1:443', 'localhost:443']) {
    assert.equal((await checkTarget(target)).ok, false, target);
  }
});

test('with an allowlist (the proxy), only listed hosts pass, and still address-checked', async () => {
  const allow = DEFAULT_ALLOW;
  assert.match((await checkTarget('docs.example.org:443', { lookup: DNS, allow })).reason, /not on the provider allowlist/);
  assert.equal((await checkTarget('api.anthropic.com:443', { lookup: DNS, allow })).ok, true);
  const badDns = dnsOf({ 'api.anthropic.com': ['10.9.9.9'] });
  assert.equal((await checkTarget('api.anthropic.com:443', { lookup: badDns, allow })).ok, false);
});

// ---- through the real proxy server ---------------------------------------------------------------------------------

async function rig({ allow = [...DEFAULT_ALLOW, 'docs.example.org', 'metadata-alias.example.org', '169.254.169.254'] } = {}) {
  // "Upstream": a plain HTTP server standing in for every public site. "/" redirects to the instance metadata address;
  // "/to-alias" redirects to a public-looking name whose DNS answer is the metadata address; "/page" is a page.
  const site = http.createServer((req, res) => {
    if (req.url === '/') res.writeHead(302, { location: 'https://169.254.169.254/latest/meta-data/' }).end();
    else if (req.url === '/to-alias') res.writeHead(301, { location: 'https://metadata-alias.example.org/x' }).end();
    else if (req.url === '/to-docs') res.writeHead(302, { location: 'https://docs.example.org/page' }).end();
    else res.writeHead(200, { 'content-type': 'text/plain' }).end('public page');
  });
  await new Promise((r) => site.listen(0, '127.0.0.1', r));
  const connected = [];
  const logs = [];
  const proxy = createEgressServer({
    lookup: DNS,
    allow,
    connect: (address) => { connected.push(address); return net.connect(site.address().port, '127.0.0.1'); },
    log: (e) => logs.push(e),
  });
  await new Promise((r) => proxy.listen(0, '127.0.0.1', r));
  const close = () => { proxy.close(); site.close(); };
  return { proxyPort: proxy.address().port, connected, logs, close };
}

function connectVia(proxyPort, target) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: proxyPort, method: 'CONNECT', path: target });
    req.on('connect', (res, socket) => resolve({ status: res.statusCode, socket }));
    req.on('error', reject);
    req.end();
  });
}

// One hop: CONNECT, then (only if tunnelled) a GET over the tunnel. Plain HTTP stands in for TLS: the tunnel is bytes.
async function hop(proxyPort, url) {
  const u = new URL(url);
  const { status, socket } = await connectVia(proxyPort, `${u.hostname.replace(/^\[|\]$/g, '')}:${u.port || 443}`);
  if (status !== 200) { socket.destroy(); return { refused: true, status }; }
  return await new Promise((resolve, reject) => {
    const req = http.request({ createConnection: () => socket, path: u.pathname, headers: { host: u.host, connection: 'close' } });
    req.on('response', (res) => {
      let body = '';
      res.on('data', (d) => { body += d; });
      res.on('end', () => resolve({ status: res.statusCode, location: res.headers.location, body }));
    });
    req.on('error', reject);
    req.end();
  });
}

// Follow redirects the way OpenClaw's guarded fetch does with a trusted env proxy: a new tunnel per hop.
async function follow(proxyPort, url, maxRedirects = 3) {
  const trail = [];
  let current = url;
  for (let i = 0; i <= maxRedirects; i += 1) {
    const r = await hop(proxyPort, current);
    trail.push({ url: current, ...r });
    if (r.refused || !r.location) return trail;
    current = new URL(r.location, current).toString();
  }
  return trail;
}

test('a public fetch tunnels to the checked address, and the log names host and verdict', async () => {
  const r = await rig();
  try {
    const trail = await follow(r.proxyPort, 'https://docs.example.org/page');
    assert.equal(trail.length, 1);
    assert.equal(trail[0].status, 200);
    assert.equal(trail[0].body, 'public page');
    assert.deepEqual(r.connected, ['93.184.215.14']);
    assert.deepEqual(r.logs, [{ verdict: 'tunnel', host: 'docs.example.org', address: '93.184.215.14' }]);
  } finally { r.close(); }
});

test('a redirect from a public page to 169.254.169.254 is refused at the hop', async () => {
  const r = await rig();
  try {
    const trail = await follow(r.proxyPort, 'https://docs.example.org/');
    assert.equal(trail[0].status, 302);
    assert.deepEqual(trail[1], { url: 'https://169.254.169.254/latest/meta-data/', refused: true, status: 403 });
    assert.deepEqual(r.connected, ['93.184.215.14'], 'never connected to the metadata address');
    assert.equal(r.logs.at(-1).verdict, 'refused');
    assert.equal(r.logs.at(-1).host, '169.254.169.254');
    assert.match(r.logs.at(-1).reason, /169\.254\.0\.0\/16/);
  } finally { r.close(); }
});

test('a redirect to a public-looking name that resolves to metadata is refused at the hop', async () => {
  const r = await rig();
  try {
    const trail = await follow(r.proxyPort, 'https://docs.example.org/to-alias');
    assert.equal(trail.at(-1).refused, true);
    assert.equal(trail.at(-1).status, 403);
    assert.deepEqual(r.connected, ['93.184.215.14']);
    assert.equal(r.logs.at(-1).host, 'metadata-alias.example.org');
  } finally { r.close(); }
});

test('a public-to-public redirect is followed, each hop checked', async () => {
  const r = await rig();
  try {
    const trail = await follow(r.proxyPort, 'https://docs.example.org/to-docs');
    assert.deepEqual(trail.map((t) => t.status), [302, 200]);
    assert.deepEqual(r.connected, ['93.184.215.14', '93.184.215.14']);
    assert.equal(r.logs.filter((l) => l.verdict === 'tunnel').length, 2);
  } finally { r.close(); }
});

test('plain HTTP through the proxy is refused', async () => {
  const r = await rig();
  try {
    const status = await new Promise((resolve, reject) => {
      http.get({ host: '127.0.0.1', port: r.proxyPort, path: 'http://docs.example.org/', headers: { host: 'docs.example.org' } },
        (res) => { res.resume(); resolve(res.statusCode); }).on('error', reject);
    });
    assert.equal(status, 403);
    assert.deepEqual(r.connected, []);
    assert.deepEqual(r.logs, [{ verdict: 'refused', host: 'docs.example.org', reason: 'plain HTTP' }]);
  } finally { r.close(); }
});

test('the proxy (default allowlist) refuses a public non-provider host: MC\'s container has no open web', async () => {
  const r = await rig({ allow: DEFAULT_ALLOW });
  try {
    const { status, socket } = await connectVia(r.proxyPort, 'docs.example.org:443');
    socket.destroy();
    assert.equal(status, 403);
    assert.deepEqual(r.connected, []);
    assert.deepEqual(r.logs, [{ verdict: 'refused', host: 'docs.example.org', reason: 'not on the provider allowlist' }]);
    const ok = await connectVia(r.proxyPort, 'api.anthropic.com:443');
    ok.socket.destroy();
    assert.equal(ok.status, 200);
    assert.deepEqual(r.connected, ['160.79.104.10']);
  } finally { r.close(); }
});
