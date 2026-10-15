// Copy the built web app into the phone app as its read-only viewer (www/browse/), so the app can browse
// the library from S3 when no Mac is on. Run after `npm run build` in web/ (npm run bundle does both).
import { cpSync, existsSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const dist = join(here, '..', '..', 'src', 'shoebox', 'web', 'dist')
const out = join(here, '..', 'www', 'browse')
if (!existsSync(join(dist, 'index.html'))) {
  console.error(`No web build at ${dist}: run npm run build in web/ first`)
  process.exit(1)
}
rmSync(out, { recursive: true, force: true })
cpSync(dist, out, { recursive: true })
rmSync(join(out, 'sw.js'), { force: true })          // no service worker inside the app
console.log(`viewer bundled into ${out}`)
