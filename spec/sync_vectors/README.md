# Sync conformance vectors

Each `*.json` file is one scenario for `apply_ops` (LOCAL_APP_PLAN.md §5). Every implementation
(Python in `src/shoebox/catalog/apply.py`, TypeScript in the web/mobile app) must pass all of them.

```jsonc
{
  "name": "lww_same_field",
  "description": "…",
  "ops": [ /* ops valid against ../ops.schema.json */ ],
  "expect": {
    "media":        { "<photo_id>": { "<column>": value, … } },   // subset of media columns
    "tags":         { "<photo_id>": ["beach"] },                  // live (not tombstoned) distinct tags
    "albums":       { "<photo_id>": ["Goa 2025"] },
    "people":       { "<person_id>": { "name": "…", "resolves_to": "<person_id after merges>" } },
    "photo_people": { "<photo_id>": ["Name"] },                    // live people, merges resolved, by name
    "devices":      { "<device_id>": { "<column>": value } },
    "counts":       { "<table>": n },
    "fts":          { "<query>": ["<photo_id>", …] },            // MATCH results on media_fts
    "tombstones":   { "<add_op>": "<removed_by>" }
  }
}
```

Runners apply the ops in at least these orders, each into a fresh catalog, and check every
expectation each time: forward; reversed; one op at a time in reverse; and duplicated (every other
op, then all, then all reversed).

Regenerate after changing scenarios: `uv run python scripts/gen_sync_vectors.py`. Expectations are
written by hand in that script and are never computed by the code under test.
