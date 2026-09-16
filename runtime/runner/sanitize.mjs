import fs from 'node:fs';
import path from 'node:path';
import {unzipSync,zipSync} from 'fflate';
import {makeRedactor} from './redact.mjs';
export function sanitizeArtifacts(root,input) {
  const redact=makeRedactor(input);
  function scrubText(value) {
    try{return JSON.stringify(redact.object(JSON.parse(value)));}catch{}
    return value.split('\n').map(line=>{try{return JSON.stringify(redact.object(JSON.parse(line)));}catch{return redact.text(line);}}).join('\n');
  }
  function textBytes(bytes) {
    const text=Buffer.from(bytes).toString('utf8');return !text.includes('\uFFFD')&&!text.includes('\0')?Buffer.from(scrubText(text)):bytes;
  }
  function walk(dir) {
    for(const entry of fs.readdirSync(dir,{withFileTypes:true})) {
      const file=path.join(dir,entry.name);
      if(entry.isSymbolicLink()){fs.unlinkSync(file);continue;}
      if(entry.isDirectory()){walk(file);continue;}if(!entry.isFile())continue;
      if(fs.statSync(file).size>128*1024*1024){fs.unlinkSync(file);continue;}
      const source=fs.readFileSync(file);
      if(source[0]===0x50&&source[1]===0x4b&&source[2]===0x03&&source[3]===0x04) {
        // Decompression runs only in this bounded utility container, never in the API or bridge process.
        const files=unzipSync(source,{filter:entry=>entry.originalSize<=128*1024*1024});
        let total=0;for(const [name,bytes] of Object.entries(files)){total+=bytes.length;if(total>256*1024*1024)throw new Error('Expanded archive exceeds evidence limit');if(!/\.(png|jpe?g|webp|gif|woff2?|ttf)$/i.test(name))files[name]=textBytes(bytes);}
        fs.writeFileSync(file,zipSync(files));
      } else if(!/\.(png|jpe?g|webp|gif|woff2?|ttf)$/i.test(file))fs.writeFileSync(file,textBytes(source));
    }
  }
  walk(root);
}
if(process.argv[1]==='/opt/runner/sanitize.mjs')sanitizeArtifacts('/work/artifacts',JSON.parse(fs.readFileSync('/work/input.json','utf8')));
