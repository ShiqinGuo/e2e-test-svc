import fs from 'node:fs';
import { makeRedactor } from './redact.mjs';
export const input = JSON.parse(fs.readFileSync('/work/input.json','utf8'));
export const redact = makeRedactor(input);
export function emit(type,data) { process.stdout.write('@@E2E@@'+JSON.stringify({type,timestamp:new Date().toISOString(),data:redact.object(data)})+'\n'); }
export function loadVariables() {return JSON.parse(fs.readFileSync('/work/variables.json','utf8'));}
