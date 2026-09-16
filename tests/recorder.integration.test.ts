import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import test from 'node:test';
import WebSocket from 'ws';
import { DockerRecorder } from '../src/runtime/recorder.js';

const exec = promisify(execFile);
test('Docker recorder gateway starts with isolated egress, exports bindings, and removes credentials', { skip: process.env.RUN_DOCKER_TESTS !== '1', timeout: 160_000 }, async () => {
  const fixture = createServer((_request, response) => { response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><h1>Private technical recording fixture</h1>'); });
  fixture.listen(0, '0.0.0.0'); await once(fixture, 'listening');
  const port = (fixture.address() as { port: number }).port;
  const url = `http://host.docker.internal:${port}/`;
  const workDir = await mkdtemp(path.join(os.tmpdir(), 'e2e-recorder-'));
  const runtime = new DockerRecorder();
  const id = randomUUID();
  try {
    let ready = false;
    let error = '';
    await runtime.start({ id, workDir, url, environment: { websites: { main: url }, apiBases: {}, roles: [] }, expiresAt: new Date(Date.now() + 120_000).toISOString() }, { ready: () => { ready = true; }, error: message => { error = message; } });
    assert.equal(error, ''); assert.equal(ready, true);
    const target = runtime.target(id)!;
    assert.equal(target.host, '127.0.0.1');
    assert.equal((await fetch(`http://${target.host}:${target.port}/`)).status, 401);
    const view = await fetch(`http://${target.host}:${target.port}/index.html`, { headers: { 'x-recorder-token': target.token } });
    assert.equal(view.status, 200); assert.ok((await view.text()).includes('viewer.js'));
    const unauthorizedWs = await new Promise<number>((resolve, reject) => {
      const socket = new WebSocket(`ws://${target.host}:${target.port}/websockify`);
      socket.on('unexpected-response', (_request, response) => { socket.terminate(); resolve(response.statusCode!); });
      socket.on('error', () => {});
      socket.on('open', () => { socket.close(); reject(new Error('Unauthenticated WebSocket was accepted')); });
    });
    assert.equal(unauthorizedWs, 401);
    const rfbHello = await new Promise<string>((resolve, reject) => {
      const socket = new WebSocket(`ws://${target.host}:${target.port}/websockify`, { headers: { 'x-recorder-token': target.token } });
      const timer = setTimeout(() => { socket.terminate(); reject(new Error('RFB handshake timed out')); }, 5000);
      socket.once('message', data => { clearTimeout(timer); socket.close(); resolve(data.toString()); });
      socket.once('error', reject);
    });
    assert.ok(rfbHello.startsWith('RFB '));
    assert.equal((await fetch(`http://${target.host}:${target.port}/../../work/input.json`, { headers: { 'x-recorder-token': target.token } })).status, 404);
    const containers = (await exec('docker', ['ps', '--filter', `label=e2e.recording=${id}`, '--format', '{{json .Names}}'], { windowsHide: true })).stdout.trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
    const recorderName = containers.find((name: string) => !name.includes('relay'));
    const blocked = await exec('docker', ['exec', recorderName, 'node', '-e', "fetch('http://1.1.1.1',{signal:AbortSignal.timeout(1500)}).then(()=>process.exit(1),()=>process.exit(0))"], { windowsHide: true });
    assert.equal(blocked.stderr, '');
    const output = await runtime.stop(id);
    assert.ok(output.code.includes('process.env["E2E_WEBSITE_main"]!'));
    assert.deepEqual(output.checks, []);
    assert.equal(runtime.target(id), undefined);
    await assert.rejects(readFile(path.join(workDir, 'input.json')));
    assert.deepEqual(await runtime.stop(id), output);
  } finally {
    await runtime.close(); fixture.close();
    assert.equal(path.dirname(path.resolve(workDir)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(workDir).startsWith('e2e-recorder-'));
    await rm(workDir, { recursive: true, force: true });
  }
});

test('stopping while Docker recording starts does not leak recording containers or credentials', { skip: process.env.RUN_DOCKER_TESTS !== '1', timeout: 90_000 }, async () => {
  const runtime = new DockerRecorder();
  const workDir = await mkdtemp(path.join(os.tmpdir(), 'e2e-recorder-'));
  const id = randomUUID();
  const url = 'http://host.docker.internal:18999/';
  let ready = false;
  try {
    const starting = runtime.start({ id, workDir, url, environment: { websites: { main: url }, apiBases: {}, roles: [] }, expiresAt: new Date(Date.now() + 60_000).toISOString() }, { ready: () => { ready = true; }, error: () => {} });
    await new Promise(resolve => setTimeout(resolve, 450));
    const stopping = runtime.stop(id);
    await Promise.all([starting, stopping]);
    assert.equal(ready, false);
    assert.equal(runtime.target(id), undefined);
    const containers = await exec('docker', ['ps', '-aq', '--filter', `label=e2e.recording=${id}`], { windowsHide: true });
    assert.equal(containers.stdout.trim(), '');
    await assert.rejects(readFile(path.join(workDir, 'input.json')));
  } finally {
    await runtime.close();
    assert.equal(path.dirname(path.resolve(workDir)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(workDir).startsWith('e2e-recorder-'));
    await rm(workDir, { recursive: true, force: true });
  }
});
