# Photoframing web app

React + TypeScript, built with Vite into `src/photoctl/web/dist`, which `photoctl serve` serves.

```sh
npm install
npm run build                 # then: uv run photoctl serve  →  http://127.0.0.1:8765
npm test                      # unit tests (vitest)
npm run e2e                   # browser tests against a seeded server (Playwright, uses your Chrome)
npm run gen:api               # refresh src/api/schema.d.ts after changing the Python API

# live-reload development against a running `photoctl serve`:
PHOTOCTL_TOKEN=$(uv run photoctl serve --print-token) npm run dev
```
