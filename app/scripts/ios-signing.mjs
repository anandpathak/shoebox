// After you pick your team (and maybe a bundle id) in Xcode's Signing & Capabilities, Xcode writes them into
// the project file. This moves them to app/ios/local-signing.xcconfig (git-ignored), so the public repo never
// carries your Apple team id, and later builds keep using them.
//   npm run ios:signing
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ios = join(dirname(fileURLToPath(import.meta.url)), '..', 'ios')
const pbx = join(ios, 'App', 'App.xcodeproj', 'project.pbxproj')
const local = join(ios, 'local-signing.xcconfig')
let s = readFileSync(pbx, 'utf8')
const found = {}
s = s.replace(/^\s*(DEVELOPMENT_TEAM|PRODUCT_BUNDLE_IDENTIFIER) = ("?)([^";\n]+)\2;\n/gm, (_, key, _q, value) => {
  if (value.trim()) found[key] = value.trim()
  return ''
})
const lines = existsSync(local) ? readFileSync(local, 'utf8').split('\n').filter((l) => l && !/^(DEVELOPMENT_TEAM|PRODUCT_BUNDLE_IDENTIFIER)\s*=/.test(l)) : []
const keep = {}
if (existsSync(local)) for (const m of readFileSync(local, 'utf8').matchAll(/^(DEVELOPMENT_TEAM|PRODUCT_BUNDLE_IDENTIFIER)\s*=\s*(.+)$/gm)) keep[m[1]] = m[2].trim()
const merged = { ...keep, ...found }
if (!Object.keys(merged).length) {
  console.log('Nothing to move. In Xcode: App target → Signing & Capabilities → Team, then run this again.')
  process.exit(0)
}
writeFileSync(local, ['// Yours only (git-ignored): written by npm run ios:signing', ...lines.filter((l) => !l.startsWith('//')),
  ...Object.entries(merged).map(([k, v]) => `${k} = ${v}`), ''].join('\n'))
writeFileSync(pbx, s)
console.log(`Saved ${Object.keys(merged).join(' and ')} in ios/local-signing.xcconfig; the project file is clean again.`)
