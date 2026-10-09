import { build } from 'vite';
import { createHash } from 'node:crypto';
import { readFile, realpath, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// The same build runs locally and on Vercel. Cache only the public app shell.
const root = await realpath(fileURLToPath(new URL('..', import.meta.url)));
const output = resolve(root, 'public');
// public is generated only; refuse cleanup through a junction or outside this repo.
const existing = await realpath(output).catch((error) => {
  if (error.code !== 'ENOENT') throw error;
  return output;
});
if (dirname(output) !== root || existing !== output)
  throw new Error('Unsafe public output directory');
const result = await build({ build: { outDir: output, emptyOutDir: true } });
const assets = (Array.isArray(result) ? result : [result])
  .flatMap((item) => item.output)
  .filter((item) => /\.(js|css)$/.test(item.fileName))
  .map((item) => '/' + item.fileName);
const template = await readFile('frontend/sw.template.js', 'utf8');
const version = createHash('sha256')
  .update(assets.join('\n'))
  .update(template)
  .digest('hex')
  .slice(0, 12);
await writeFile(
  'public/sw.js',
  template
    .replace('__BUILD_VERSION__', version)
    .replace('"__BUILD_ASSETS__"', JSON.stringify(assets)),
);
