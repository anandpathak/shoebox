# Shoebox demo

`index.html` is a self-contained presenter page for the project. Open it in a browser:

```sh
open demo/index.html
```

It has six sections:

- **Features**: what the system does, with status (Built / Next / Planned / Optional) and the milestone that delivers each part.
- **Archive flow**: steps through how one photo or video is archived, using the numbers from real test runs (IMG_5368.HEIC, IMG_1520.MOV).
- **Phone app**: interactive iPhone / Android mockup covering search, the viewer, starting a run on the Mac, uploading from the phone, offline mode and per-device catalogs. The photo library in the mockup is example data.
- **Sync & conflicts**: a working simulator of the operation log, where two phones edit offline and then sync to the same result.
- **Architecture**: a diagram, the S3 layout, the conflict rules, the cost estimate and the tech stack.
- **Roadmap**: milestones M0–M13 and the open decisions from `docs/TASKS.md`.

The source of truth for the design is `docs/LOCAL_APP_PLAN.md`. Update this page when the plan changes.
