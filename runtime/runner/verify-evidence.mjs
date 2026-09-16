// Acceptance-only utility. The marker is synthetic fixture data, never a real credential.
import fs from 'node:fs';
import path from 'node:path';
import {unzipSync} from 'fflate';
let archives=0,records=0;
function check(name,data) {
  if(/\.(png|jpe?g|webp|gif)$/i.test(name))return;
  const text=Buffer.from(data).toString('utf8');
  if(text.includes('DO-NOT-LEAK'))throw new Error('Synthetic secret leaked into evidence');
  if(/\.(trace|network)$/.test(name))for(const row of text.split('\n'))if(row.trim()){JSON.parse(row);records++;}
}
function walk(dir) {for(const entry of fs.readdirSync(dir,{withFileTypes:true})){const file=path.join(dir,entry.name);if(entry.isDirectory())walk(file);else if(entry.isFile()){const bytes=fs.readFileSync(file);if(file.endsWith('.zip')){archives++;for(const [name,data] of Object.entries(unzipSync(bytes)))check(name,data);}else check(file,bytes);}}}
walk('/evidence');console.log(JSON.stringify({archives,records,syntheticSecretsFound:0}));
