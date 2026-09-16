// Technical fixture for Python ApiAction serialization compatibility.
import http from 'node:http';
let orderExists=false;
const requests=[];
http.createServer(async(req,res)=>{
  let body='';for await(const part of req)body+=part;
  const shape={method:req.method,path:req.url,body,bodyBytes:Buffer.byteLength(body),contentType:req.headers['content-type']||null};
  requests.push(shape);
  res.setHeader('Content-Type','application/json');
  if(req.url==='/shape'){res.end(JSON.stringify(shape));return;}
  if(req.url==='/plain'){res.setHeader('Content-Type','text/plain');res.end('plain response');return;}
  if(req.method==='POST'&&req.url==='/orders'){orderExists=true;res.writeHead(201);res.end(JSON.stringify({id:'regression-order'}));return;}
  if(req.method==='DELETE'&&req.url==='/orders/regression-order'){orderExists=false;res.writeHead(204);res.end();return;}
  if(req.url==='/state'){res.end(JSON.stringify({orderExists,requests}));return;}
  res.writeHead(404);res.end('{}');
}).listen(8080,'0.0.0.0');
