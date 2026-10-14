const fs = require('node:fs');
const { randomUUID } = require('node:crypto');

function frontendProtocol(source) {
  if (source.includes('@mx-space/ws-client')) return 'raw-ws';
  if (source.includes('socket.io-client')) return 'socket.io';
  throw new Error('Cannot determine the theme realtime protocol; review before deployment.');
}

function assertCompatible(protocol, version) {
  if (!/^\d+\.\d+\.\d+(?:[-+].*)?$/.test(version || '')) throw new Error('Unknown backend version.');
  if (!['raw-ws', 'socket.io'].includes(protocol)) throw new Error('Unknown frontend protocol.');
  const major = Number(version.split('.')[0]);
  if ((protocol === 'raw-ws') !== (major >= 14)) {
    throw new Error(`Theme uses ${protocol}, but core ${version} uses ${major >= 14 ? 'raw WebSocket' : 'Socket.IO'}. Keep the compatible frontend until backend and proxy are updated together.`);
  }
}

function probeRealtime(protocol, gateway, { WebSocketCtor = WebSocket, timeoutMs = 15000 } = {}) {
  const url = new URL(gateway);
  if (!['https:', 'http:', 'wss:', 'ws:'].includes(url.protocol)) throw new Error('Invalid gateway URL.');
  url.protocol = ['https:', 'wss:'].includes(url.protocol) ? 'wss:' : 'ws:';
  url.pathname = protocol === 'raw-ws' ? '/ws/web' : '/socket.io/';
  url.search = '';
  url.searchParams.set('socket_session_id', `deployment-probe-${randomUUID()}`);
  url.searchParams.set('lang', 'zh-CN');
  if (protocol === 'socket.io') {
    url.searchParams.set('EIO', '4');
    url.searchParams.set('transport', 'websocket');
  }
  return new Promise((resolve, reject) => {
    let finished = false;
    let connected = false;
    let online;
    let ws;
    const timer = setTimeout(() => finish(new Error('WebSocket did not deliver an online-count event. Check gateway proxy and backend.')), timeoutMs);
    function finish(error) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (ws) ws.close();
      if (error) reject(error); else resolve({ protocol, online, transport: 'websocket' });
    }
    function ready() {
      if (connected && Number.isInteger(online) && online > 0) finish();
    }
    try { ws = new WebSocketCtor(url.href); } catch (error) { finish(error); return; }
    ws.onopen = () => { if (protocol === 'raw-ws') connected = true; };
    ws.onerror = () => finish(new Error('WebSocket upgrade failed. Check gateway proxy and backend.'));
    ws.onclose = () => finish(new Error('WebSocket closed before an online-count event.'));
    ws.onmessage = ({ data }) => {
      const packet = String(data);
      if (protocol === 'socket.io') {
        if (packet.startsWith('0')) { ws.send('40/web,'); return; }
        if (packet === '2') { ws.send('3'); return; }
        if (packet.startsWith('40/web,')) { connected = true; ready(); return; }
        if (packet.startsWith('44/web,')) { finish(new Error('Socket.IO web namespace rejected connection.')); return; }
        if (!packet.startsWith('42/web,')) return;
        try {
          const [event, payload] = JSON.parse(packet.slice('42/web,'.length));
          const message = typeof payload === 'string' ? JSON.parse(payload) : payload;
          if (event === 'message' && message.type === 'VISITOR_ONLINE') online = message.data?.online;
        } catch { return; }
      } else {
        try {
          const message = JSON.parse(packet);
          if (message.event === 'visitor.online') online = message.payload?.online;
        } catch { return; }
      }
      ready();
    };
  });
}

async function main() {
  const target = process.argv[2];
  if (!target) throw new Error('Pass the theme worker source path or --protocol=socket.io|raw-ws.');
  const protocol = target.startsWith('--protocol=') ? target.slice('--protocol='.length) : frontendProtocol(fs.readFileSync(target, 'utf8'));
  const api = process.env.NEXT_PUBLIC_API_URL;
  const gateway = process.env.NEXT_PUBLIC_GATEWAY_URL;
  if (!api || !gateway) throw new Error('API and gateway URLs are required.');
  const response = await fetch(`${api.replace(/\/$/, '')}/info`, { signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(`Backend info returned HTTP ${response.status}.`);
  const body = await response.json();
  const version = body.data?.version;
  assertCompatible(protocol, version);
  const result = await probeRealtime(protocol, gateway);
  console.log(JSON.stringify({ backend: version, ...result }));
}

module.exports = { frontendProtocol, assertCompatible, probeRealtime };
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
