const test = require('node:test');
const assert = require('node:assert/strict');
const { frontendProtocol, assertCompatible, probeRealtime } = require('../scripts/check-realtime.cjs');

test('protocol changes require matching backend major versions', () => {
  assertCompatible('socket.io', '13.11.5');
  assertCompatible('raw-ws', '14.0.0');
  assert.throws(() => assertCompatible('raw-ws', '13.11.5'), /Keep the compatible frontend/);
  assert.throws(() => assertCompatible('socket.io', '14.0.0'), /Keep the compatible frontend/);
  assert.throws(() => assertCompatible('raw-ws', undefined), /Unknown backend version/);
});

test('recognize transport imports and reject unknown source', () => {
  assert.equal(frontendProtocol("import { createWsClient } from '@mx-space/ws-client'"), 'raw-ws');
  assert.equal(frontendProtocol("import { io } from 'socket.io-client'"), 'socket.io');
  assert.throws(() => frontendProtocol('unrecognized transport'), /Cannot determine/);
});

test('Socket.IO must join web namespace and receive a real online-count message', async () => {
  class FakeSocket {
    constructor(url) {
      assert.equal(new URL(url).pathname, '/socket.io/');
      assert.equal(new URL(url).searchParams.get('transport'), 'websocket');
      queueMicrotask(() => this.onmessage({ data: '0{"sid":"example"}' }));
    }
    send(packet) {
      assert.equal(packet, '40/web,');
      queueMicrotask(() => {
        this.onmessage({ data: '40/web,{"sid":"example"}' });
        this.onmessage({ data: '42/web,["message",{"type":"VISITOR_ONLINE","data":{"online":2}}]' });
      });
    }
    close() {}
  }
  const result = await probeRealtime('socket.io', 'https://example.com', { WebSocketCtor: FakeSocket, timeoutMs: 100 });
  assert.equal(result.online, 2);
});

test('an open socket without online-count messages is not a passing check', async () => {
  class SilentSocket {
    constructor() { queueMicrotask(() => this.onopen()); }
    close() {}
  }
  await assert.rejects(probeRealtime('raw-ws', 'https://example.com', { WebSocketCtor: SilentSocket, timeoutMs: 20 }), /did not deliver/);
});
