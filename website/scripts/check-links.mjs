// Fails the build if any internal link or asset in dist/ points nowhere.
import { readFile, readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';

const dist = new URL('../dist/', import.meta.url).pathname;
async function* html(dir) {
  for (const e of await readdir(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) yield* html(p);
    else if (e.name.endsWith('.html')) yield p;
  }
}
const exists = async (p) => { try { await stat(p); return true; } catch { return false; } };
const resolve = async (url) => {
  const path = decodeURIComponent(url.split(/[?#]/)[0]);
  if (!path || path === '/') return exists(join(dist, 'index.html'));
  const f = join(dist, path);
  return (await exists(f)) && ((await stat(f)).isFile() || exists(join(f, 'index.html'))) || exists(f + '.html') || exists(join(f, 'index.html'));
};

let bad = 0, n = 0;
for await (const file of html(dist)) {
  const src = await readFile(file, 'utf8');
  const ids = new Set([...src.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
  for (const m of src.matchAll(/\s(?:href|src)="([^"]+)"/g)) {
    const u = m[1];
    if (/^(https?:|mailto:|data:|\/\/)/.test(u)) continue;
    n++;
    if (u.startsWith('#')) { if (u.length > 1 && !ids.has(u.slice(1))) { bad++; console.error(`${file.replace(dist, '')}: missing anchor ${u}`); } continue; }
    if (u.startsWith('/') && !(await resolve(u))) { bad++; console.error(`${file.replace(dist, '')}: broken link ${u}`); }
  }
}
console.log(`check-links: ${n} internal links, ${bad} broken`);
process.exit(bad ? 1 : 0);
