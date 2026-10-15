# Shoebox web app

React + TypeScript, built with Vite into `src/shoebox/web/dist`, which `shoebox serve` serves.

```sh
npm install
npm run build                 # then: uv run shoebox serve  →  http://127.0.0.1:8765
npm test                      # unit tests (vitest)
npm run e2e                   # browser tests against a seeded server (Playwright, uses your Chrome)
npm run gen:api               # refresh src/api/schema.d.ts after changing the Python API

# live-reload development against a running `shoebox serve`:
SHOEBOX_TOKEN=$(uv run shoebox serve --print-token) npm run dev
```
