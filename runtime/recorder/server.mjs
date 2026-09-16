import { readFile, writeFile } from 'node:fs/promises';
import { launchRecorder } from './adapter.mjs';
import { createRecorderGateway } from './gateway.mjs';

const input = JSON.parse(await readFile('/work/input.json', 'utf8'));
let ready = false;
let failure = null;
let recorder;
const stop = async () => {
  ready = false;
  await recorder?.context.close().catch(() => {});
  await recorder?.browser.close().catch(() => {});
};
const server = createRecorderGateway({ token: input.gatewayToken, status: () => ({ ready, error: failure }), stop });
server.listen(6080, '0.0.0.0');
try {
  recorder = await launchRecorder(input);
  ready = true;
  recorder.browser.on('disconnected', () => { ready = false; failure = '录制浏览器已关闭'; });
  await writeFile('/work/ready.json', JSON.stringify({ ready: true }));
} catch {
  failure = '录制浏览器启动或目标网站导航失败';
  await writeFile('/work/ready.json', JSON.stringify({ ready: false, error: failure }));
}
process.on('SIGTERM', async () => { await stop(); server.close(); process.exit(0); });
