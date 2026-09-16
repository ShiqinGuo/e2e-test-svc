// Isolated technical acceptance fixture. Never a business environment.
import http from 'node:http';
const orders=new Map();let sequence=0;
for(const [port,label] of [[8080,'Fixture A'],[8081,'Fixture B']])http.createServer(async(req,res)=>{
  let raw='';for await(const part of req)raw+=part;
  const url=new URL(req.url,'http://fixture');
  res.setHeader('Content-Type','application/json');
  if(req.method==='POST'&&url.pathname==='/orders'){const id=String(++sequence);orders.set(id,{id,label,...JSON.parse(raw||'{}')});res.writeHead(201);res.end(JSON.stringify({id}));return;}
  if(req.method==='DELETE'&&url.pathname.startsWith('/orders/')){orders.delete(url.pathname.split('/').at(-1));res.writeHead(204);res.end();return;}
  if(url.pathname.startsWith('/orders/')){const value=orders.get(url.pathname.split('/').at(-1));res.writeHead(value?200:404);res.end(JSON.stringify(value||{error:'not found'}));return;}
  if(url.pathname==='/state'){res.end(JSON.stringify({orders:[...orders.values()],role:req.headers['x-test-role'],cookie:req.headers.cookie}));return;}
  if(url.pathname==='/health'){res.end(JSON.stringify({ok:true,label}));return;}
  res.setHeader('Content-Type','text/html');res.end(`<!doctype html><html><head><title>${label}</title></head><body><h1>${label}</h1><label>Order<input aria-label="Order" value="draft-42"></label><button id="submit">Submit order</button><p id="result"></p><output aria-label="Created order ID" id="order-id"></output><p id="error" role="alert"></p><script>
document.querySelector('#submit').addEventListener('click',async()=>{
  const reference=document.querySelector('input').value;
  try{const reply=await fetch('/orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reference})});if(!reply.ok)throw new Error('Create order failed: '+reply.status);const order=await reply.json();document.querySelector('#result').textContent=reference;document.querySelector('#order-id').textContent=order.id;document.querySelector('#error').textContent='';}
  catch(error){document.querySelector('#error').textContent=error.message;}
});</script></body></html>`);
}).listen(port,'0.0.0.0');
