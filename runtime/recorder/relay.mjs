// Fixed-target ingress only. It cannot select arbitrary network destinations.
import { createServer, connect, isIP } from 'node:net';
const target = process.argv[2];
if (!isIP(target)) throw new Error('Recorder relay requires an exact container IP');
createServer(client => {
  const upstream = connect(6080, target);
  client.pipe(upstream); upstream.pipe(client);
  client.on('error', () => upstream.destroy());
  upstream.on('error', () => client.destroy());
  client.on('close', () => upstream.destroy());
  upstream.on('close', () => client.destroy());
}).listen(6080, '0.0.0.0');
