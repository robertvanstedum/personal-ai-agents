// A fixed TCP forwarder (MC container phase 2, plan D2). Each rule is LISTEN_HOST:LISTEN_PORT=TARGET_HOST:TARGET_PORT,
// set by the operator in MC_FORWARD (comma-separated). Nothing the model sends can change a destination: the bytes are
// piped as they are, and the rules are read once at start.
//   mc-tools-door (on mc-internal + the portal's network): 0.0.0.0:5011=minimoi-portal:5011
//   inside mc-agent (so the plugin's loopback-only endpoint rule holds): 127.0.0.1:5011=mc-tools-door:5011
import net from 'node:net';

const RULE = /^(127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})=([A-Za-z0-9.-]{1,253}):(\d{2,5})$/;
const rules = String(process.env.MC_FORWARD || '').split(',').map(s => s.trim()).filter(Boolean);
if (!rules.length) { console.error('tcp-forward: REFUSED: MC_FORWARD is empty'); process.exit(78); }
for (const rule of rules) {
  const m = RULE.exec(rule);
  if (!m) { console.error(`tcp-forward: REFUSED: bad rule ${JSON.stringify(rule)}`); process.exit(78); }
  const [, host, port, toHost, toPort] = m;
  net.createServer(client => {
    const upstream = net.connect({host: toHost, port: Number(toPort)});
    const close = () => { client.destroy(); upstream.destroy(); };
    client.on('error', close); upstream.on('error', close);
    client.pipe(upstream).pipe(client);
  }).listen(Number(port), host, () => console.error(`tcp-forward: ${host}:${port} -> ${toHost}:${toPort}`));
}
