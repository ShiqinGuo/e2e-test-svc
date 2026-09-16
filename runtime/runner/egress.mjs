// This process runs in a separate, dual-homed container. Tests cannot edit its policy.
import http from 'node:http';
import net from 'node:net';
import dns from 'node:dns/promises';
import fs from 'node:fs';
const policy = JSON.parse(fs.readFileSync('/policy.json','utf8'));
const allowed = new Set(policy.allowedOrigins);
const denied = new Set(policy.platformOrigins || []);
const deniedPorts = new Set(['4100','5173','2375','2376',...(policy.platformOrigins||[]).map(raw=>new URL(raw).port).filter(port=>port&&port!=='80'&&port!=='443')]);
const deniedEndpoints = Promise.all((policy.platformOrigins||[]).map(async raw=>{const url=new URL(raw);try{return (await dns.lookup(url.hostname,{all:true})).map(item=>`${item.address}:${url.port||(url.protocol==='https:'?'443':'80')}`);}catch{return [];}})).then(items=>new Set(items.flat()));
function target(raw) {
  const url = new URL(raw);
  if (!['http:','https:'].includes(url.protocol) || url.username || url.password || !allowed.has(url.origin) || denied.has(url.origin)) throw new Error('Origin is not permitted by the selected environment');
  if (deniedPorts.has(url.port || (url.protocol==='https:'?'443':'80'))) throw new Error('Platform/control-plane port is blocked');
  if (/^(localhost|.*\.localhost)$/i.test(url.hostname)) throw new Error('Loopback destination is blocked');
  return url;
}
async function resolve(host,port) {
  const records = await dns.lookup(host, {all:true});
  const endpoints=await deniedEndpoints;
  // Private test networks may be explicitly allowed; loopback, link-local metadata and multicast may not.
  const safe = records.find(({address}) => !endpoints.has(`${address}:${port}`) && !/^(127\.|0\.|169\.254\.|22[4-9]\.|23\d\.|255\.)/.test(address) && !['::','::1'].includes(address) && !/^(fe80:|ff|::ffff:(127\.|169\.254\.))/i.test(address));
  if (!safe) throw new Error('Destination address is blocked');
  return safe;
}
const server = http.createServer(async (req,res) => {
  try {
    const url = target(req.url);
    if (url.protocol !== 'http:') throw new Error('HTTPS requires CONNECT');
    const address = await resolve(url.hostname,url.port || '80');
    const headers = {...req.headers, host:url.host};
    delete headers['proxy-authorization']; delete headers['proxy-connection'];
    const upstream = http.request({host:address.address,port:url.port || 80,method:req.method,path:url.pathname+url.search,headers,timeout:30000}, reply => {
      res.writeHead(reply.statusCode || 502,reply.headers); reply.pipe(res);
    });
    upstream.on('error',()=>{if (!res.headersSent) res.writeHead(502);res.end('Upstream unavailable');});
    upstream.on('timeout',()=>upstream.destroy()); req.pipe(upstream);
  } catch {res.writeHead(403);res.end('Environment target denied');}
});
server.on('connect',async (req,client,head) => {
  try {
    // Playwright's APIRequestContext also uses CONNECT for plain HTTP targets.
    let url;try{url=target(`https://${req.url}`);}catch{url=target(`http://${req.url}`);}
    const port=url.port || (url.protocol==='https:'?'443':'80');
    const address = await resolve(url.hostname,port);
    const upstream = net.connect({host:address.address,port:Number(port)},()=>{
      client.write('HTTP/1.1 200 Connection Established\r\n\r\n');if(head.length)upstream.write(head);client.pipe(upstream);upstream.pipe(client);
    });
    upstream.setTimeout(60000,()=>upstream.destroy());
    upstream.on('error',()=>client.destroy());client.on('error',()=>upstream.destroy());client.on('close',()=>upstream.destroy());
  } catch {client.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');}
});
server.on('clientError',(_error,socket)=>socket.end('HTTP/1.1 400 Bad Request\r\n\r\n'));
server.listen(3128,'0.0.0.0');
