import { createServer } from 'node:http';
import { connect } from 'node:net';
import { timingSafeEqual } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { WebSocketServer } from 'ws';

/** The API injects this token only after checking session/project ownership. */
export function createRecorderGateway({ token, status, stop }) {
  const expectedToken = Buffer.from(token);
  const authorized = request => {
    const incoming = Buffer.from(String(request.headers['x-recorder-token'] || ''));
    return incoming.length === expectedToken.length && timingSafeEqual(incoming, expectedToken);
  };
  const server = createServer(async (request, response) => {
    if (!authorized(request)) { response.writeHead(401).end('Unauthorized'); return; }
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    const url = new URL(request.url, 'http://recorder');
    if (url.pathname === '/health') {
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify(status())); return;
    }
    if (url.pathname === '/stop' && request.method === 'POST') { await stop(); response.end('{}'); return; }
    const file = url.pathname === '/' || url.pathname === '/index.html' ? 'index.html' : url.pathname === '/viewer.js' ? 'viewer.js' : null;
    if (!file) { response.writeHead(404).end(); return; }
    try {
      response.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript; charset=utf-8' : 'text/html; charset=utf-8');
      response.end(await readFile(new URL(`public/${file}`, import.meta.url)));
    } catch { response.writeHead(500).end(); }
  });
  const sockets = new WebSocketServer({ noServer: true, maxPayload: 16 * 1024 * 1024 });
  server.on('upgrade', (request, socket, head) => {
    if (!authorized(request) || new URL(request.url, 'http://recorder').pathname !== '/websockify' || !status().ready) {
      socket.end('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n'); return;
    }
    sockets.handleUpgrade(request, socket, head, ws => {
      const vnc = connect(5900, '127.0.0.1');
      vnc.on('data', data => { if (ws.readyState === ws.OPEN) ws.send(data); });
      vnc.on('error', () => ws.close());
      vnc.on('close', () => ws.close());
      ws.on('message', data => vnc.write(Buffer.from(data)));
      ws.on('close', () => vnc.destroy());
      ws.on('error', () => vnc.destroy());
    });
  });
  return server;
}
