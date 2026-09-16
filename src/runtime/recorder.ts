import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomBytes, createHash } from 'node:crypto';
import { mkdir, readFile, writeFile, unlink } from 'node:fs/promises';
import path from 'node:path';
import { parse } from '@babel/parser';
import { createNetworkSandbox } from './runner.js';

const exec = promisify(execFile);
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const safeId = (id: string) => createHash('sha256').update(id).digest('hex').slice(0, 24);
const docker = async (...args: string[]) => (await exec('docker', args, { timeout: 60_000, maxBuffer: 1024 * 1024, windowsHide: true })).stdout.trim();
export type RecorderCheck = { id: string; title: string; kind: 'ui'; expected?: string };
export type RecordingOutput = { code: string; checks: RecorderCheck[] };
type RecordingInput = { id: string; workDir: string; url: string; website?: string; environment: any; role?: string; expiresAt: string; platformOrigins?: string[] };
type Session = {
  input: RecordingInput; name: string; relayName: string; token: string; port?: number;
  sandbox?: Awaited<ReturnType<typeof createNetworkSandbox>>;
  timer?: NodeJS.Timeout; monitor?: NodeJS.Timeout; stopped: boolean; output?: RecordingOutput;
  starting?: Promise<void>; cleanup?: Promise<void>; finishing?: Promise<RecordingOutput>;
};

/** Rebind codegen's literal navigations; the runner freezes these values per run. */
export function bindRecordingCode(code: string, websites: Record<string, string>, options: { website?: string; secretVariables?: Record<string, string> } = {}): RecordingOutput {
  const source = parse(code, { sourceType: 'module', plugins: ['typescript'] });
  const replacements: { start: number; end: number; text: string }[] = [];
  const checks: RecorderCheck[] = [];
  const entries = Object.entries(websites).map(([name, raw]) => ({ name, raw, url: new URL(raw) })).sort((a, b) => Number(b.name === options.website) - Number(a.name === options.website));
  const secrets = Object.entries(options.secretVariables || {}).filter(([, value]) => value.length > 0);
  const readable = (text: string) => secrets.reduce((result, [name, value]) => result.split(value).join(`[secret:${name}]`), text);
  const summary = (text: string, limit: number) => Array.from(readable(text)).slice(0, limit).join('');
  const visit = (node: any) => {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'CallExpression' && node.callee.type === 'MemberExpression') {
      const method = node.callee.property.name;
      const argument = node.arguments[0];
      if (method === 'goto' && argument?.type === 'StringLiteral') {
        let target: URL | undefined;
        try { target = new URL(argument.value); } catch { /* Relative URL uses runner baseURL. */ }
        if (target) {
          const website = entries.find(entry => target!.href === entry.url.href) || entries.find(entry => target!.origin === entry.url.origin);
          if (!website) throw new Error('录制代码包含当前环境未命名的网站导航');
          const binding = `process.env[${JSON.stringify(`E2E_WEBSITE_${website.name}`)}]!`;
          const expression = target.href === website.url.href ? binding : `new URL(${JSON.stringify(target.pathname + target.search + target.hash)}, ${binding}).href`;
          replacements.push({ start: argument.start, end: argument.end, text: expression });
        }
      }
      if (/^(toBeVisible|toContainText|toHaveText|toHaveValue|toBeChecked|toMatchAriaSnapshot)$/.test(method) && node.callee.object.type === 'CallExpression' && node.callee.object.callee.name === 'expect') {
        const statement = code.slice(node.start, node.end);
        checks.push({ id: `check-${checks.length + 1}`, kind: 'ui', title: summary(statement, 200), expected: summary(argument ? code.slice(argument.start, argument.end) : method === 'toBeVisible' ? 'visible' : method, 4000) });
      }
      if (method !== 'goto') for (const item of node.arguments) {
        if (item.type !== 'StringLiteral') continue;
        const match = secrets.find(([, value]) => value === item.value);
        if (match) replacements.push({ start: item.start, end: item.end, text: `process.env[${JSON.stringify(`E2E_VAR_${match[0]}`)}]!` });
      }
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') visit(value);
    }
  };
  visit(source);
  for (const edit of replacements.sort((a, b) => b.start - a.start)) code = code.slice(0, edit.start) + edit.text + code.slice(edit.end);
  return { code, checks };
}

export class DockerRecorder {
  private sessions = new Map<string, Session>();
  private image: string;
  private networkImage: string;
  private startupTimeoutMs: number;
  constructor(options: { image?: string; networkImage?: string; startupTimeoutMs?: number } = {}) {
    this.image = options.image || process.env.RECORDER_IMAGE || 'e2e-recorder:1.63.0';
    this.networkImage = options.networkImage || this.image;
    this.startupTimeoutMs = options.startupTimeoutMs || 75_000;
  }
  async available(): Promise<{ available: boolean; reason?: string }> {
    try {
      if (await docker('info', '--format', '{{.OSType}}') !== 'linux') return { available: false, reason: '录制需要 Docker Linux 容器' };
      await docker('image', 'inspect', this.image, '--format', '{{.Id}}');
      return { available: true };
    } catch { return { available: false, reason: `录制镜像不可用，请构建 ${this.image}` }; }
  }
  async start(input: RecordingInput, handlers: { ready: () => void; error: (message: string) => void }): Promise<void> {
    if (this.sessions.has(input.id)) throw new Error('Recording already exists');
    if (!Number.isFinite(Date.parse(input.expiresAt)) || Date.parse(input.expiresAt) <= Date.now()) throw new Error('Recording expiry must be in the future');
    const named = Object.values(input.environment.websites || {}) as string[];
    if (!named.includes(input.url)) throw new Error('Recording URL must match a named environment website');
    if (input.role && !input.environment.roles?.some((role: any) => role.name === input.role)) throw new Error('Recording role does not belong to the environment');
    const session: Session = { input: { ...input, workDir: path.resolve(input.workDir) }, name: `e2e-recording-${safeId(input.id)}`, relayName: `e2e-recording-relay-${safeId(input.id)}`, token: randomBytes(32).toString('hex'), stopped: false };
    this.sessions.set(input.id, session);
    session.starting = this.initialize(session, handlers);
    await session.starting;
  }
  private async initialize(session: Session, handlers: { ready: () => void; error: (message: string) => void }): Promise<void> {
    const input = session.input;
    const named = Object.values(input.environment.websites || {}) as string[];
    try {
      const available = await this.available();
      if (!available.available) throw new Error(available.reason);
      if (session.stopped) { handlers.error('录制会话已停止'); return; }
      await mkdir(session.input.workDir, { recursive: true });
      const policyFile = path.join(session.input.workDir, 'egress-policy.json');
      const allowedOrigins = [...new Set([...named, ...Object.values(input.environment.apiBases || {}), ...(input.environment.allowedOrigins || [])].map(value => new URL(String(value)).origin))];
      const policy = { allowedOrigins, platformOrigins: input.platformOrigins || (process.env.TRUSTED_ORIGINS || 'http://localhost:4100,http://localhost:5173').split(',') };
      await writeFile(policyFile, JSON.stringify(policy), { mode: 0o600 });
      session.sandbox = await createNetworkSandbox(`recorder-${safeId(input.id)}`, policy, policyFile, this.networkImage);
      await writeFile(path.join(session.input.workDir, 'input.json'), JSON.stringify({ ...input, proxy: session.sandbox.proxyAddress, gatewayToken: session.token }), { mode: 0o600 });
      await docker('run', '-d', '--name', session.name, '--label', 'e2e.platform=true', '--label', `e2e.recording=${input.id}`, '--init', '--network', session.sandbox.network, '--dns', '127.0.0.1',
        '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=256', '--cpus=2', '--memory=2g',
        '--shm-size=512m', '--tmpfs', '/tmp:rw,nosuid,nodev,size=512m,mode=1777',
        '--mount', `type=bind,source=${session.input.workDir},target=/work`, this.image);
      const inspect = JSON.parse(await docker('inspect', session.name));
      const recorderAddress = inspect[0].NetworkSettings.Networks[session.sandbox.network].IPAddress;
      await docker('run', '-d', '--name', session.relayName, '--label', 'e2e.platform=true', '--label', `e2e.recording=${input.id}`, '--network', 'bridge', '--dns', '127.0.0.1',
        '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=64', '--memory=128m', '--cpus=0.25',
        '-p', '127.0.0.1::6080', '--entrypoint', 'node', this.image, '/opt/recorder/relay.mjs', recorderAddress);
      await docker('network', 'connect', session.sandbox.network, session.relayName);
      const binding = await docker('port', session.relayName, '6080/tcp');
      session.port = Number(binding.split(':').at(-1));
      if (!session.port) throw new Error('Recording gateway was not published');
      const started = Date.now();
      while (Date.now() - started < this.startupTimeoutMs) {
        if (session.stopped) { handlers.error('录制会话已停止'); return; }
        try {
          const response = await fetch(`http://127.0.0.1:${session.port}/health`, { headers: { 'x-recorder-token': session.token }, signal: AbortSignal.timeout(2000) });
          const status = await response.json() as { ready: boolean; error: string | null };
          if (status.error) throw Object.assign(new Error(status.error), { fatal: true });
          if (status.ready) {
            session.timer = setTimeout(() => { void this.stop(input.id).then(() => handlers.error('录制会话已到期')).catch(() => handlers.error('录制会话到期后停止失败')); }, Math.max(1, Date.parse(input.expiresAt) - Date.now()));
            session.timer.unref();
            session.monitor = setInterval(() => {
              if (session.stopped) return;
              void fetch(`http://127.0.0.1:${session.port}/health`, { headers: { 'x-recorder-token': session.token }, signal: AbortSignal.timeout(2500) })
                .then(response => response.json()).then((health: any) => { if (!health.ready) throw new Error('Recorder stopped'); })
                .catch(() => { if (!session.stopped) void this.stop(input.id).then(() => handlers.error('录制浏览器连接已中断'), () => handlers.error('录制浏览器连接已中断')); });
            }, 5000);
            session.monitor.unref(); handlers.ready(); return;
          }
        } catch (error: any) { if (error.fatal) throw error; }
        await delay(350);
      }
      throw new Error('录制浏览器启动超时');
    } catch (error: any) {
      const cancelled = session.stopped;
      session.stopped = true;
      await this.cleanup(session);
      handlers.error(cancelled ? '录制会话已停止' : error.message?.includes('录制') ? error.message : '录制容器启动失败');
    }
  }
  target(id: string): { host: string; port: number; token: string } | undefined {
    const session = this.sessions.get(id);
    return session?.port && !session.stopped ? { host: '127.0.0.1', port: session.port, token: session.token } : undefined;
  }
  async read(id: string): Promise<RecordingOutput> {
    const session = this.sessions.get(id);
    if (!session) throw new Error('Recording not found');
    if (session.output) return session.output;
    let code = '';
    try { code = await readFile(path.join(session.input.workDir, 'recording.spec.ts'), 'utf8'); } catch (error: any) { if (error.code !== 'ENOENT') throw error; }
    return bindRecordingCode(code, session.input.environment.websites || {}, { website: session.input.website, secretVariables: session.input.environment.secretVariables });
  }
  async stop(id: string): Promise<RecordingOutput> {
    const session = this.sessions.get(id);
    if (!session) throw new Error('Recording not found');
    session.stopped = true;
    clearTimeout(session.timer);
    clearInterval(session.monitor);
    session.finishing ??= this.finish(session);
    return session.finishing;
  }
  private async finish(session: Session): Promise<RecordingOutput> {
    await session.starting;
    await this.cleanup(session);
    if (session.output) return session.output;
    try {
      session.output = await this.read(session.input.id);
      await writeFile(path.join(session.input.workDir, 'recording.spec.ts'), session.output.code, { mode: 0o600 });
      session.input.environment = { websites: session.input.environment.websites };
      session.token = '';
      return session.output;
    }
    finally {
      await unlink(path.join(session.input.workDir, 'input.json')).catch(() => {});
      await unlink(path.join(session.input.workDir, 'egress-policy.json')).catch(() => {});
    }
  }
  private async cleanup(session: Session): Promise<void> {
    session.cleanup ??= (async () => {
      if (session.port) await fetch(`http://127.0.0.1:${session.port}/stop`, { method: 'POST', headers: { 'x-recorder-token': session.token }, signal: AbortSignal.timeout(5000) }).catch(() => {});
      await docker('rm', '-f', session.relayName).catch(() => {});
      await docker('rm', '-f', session.name).catch(() => {});
      await session.sandbox?.close();
      await unlink(path.join(session.input.workDir, 'input.json')).catch(() => {});
      await unlink(path.join(session.input.workDir, 'egress-policy.json')).catch(() => {});
    })();
    await session.cleanup;
  }
  async close(): Promise<void> { await Promise.allSettled([...this.sessions.keys()].map(id => this.stop(id))); }
}
