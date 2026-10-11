# Recovery runbook

What to do when something on a device breaks. Your photos are never at risk in these situations:
originals, compressed copies and sidecars live in S3 and aren't touched by any of the steps below.

Before any step, make sure you can reach the bucket:

```sh
aws login                                   # or: aws sso login
export PHOTOCTL_BUCKET=<your bucket>        # cd infra && tofu output -raw bucket
uv run photoctl db list --device all        # lists every device that has backups
```

## 1. The catalog or state database is corrupted or deleted (Mac is fine)

Symptoms: `photoctl status` errors, "database disk image is malformed", or the catalog is suddenly empty.

```sh
uv run photoctl db restore                  # latest backup of this Mac; current files kept as *.bak
```

The restore checks every file's checksum and runs SQLite's integrity check before swapping anything
in. It then syncs, which re-applies every change made on any device since that backup (including your own).

Check: `uv run photoctl sync verify` says **converged**.

## 2. Go back to a point in time

```sh
uv run photoctl db list                     # pick a date
uv run photoctl db restore --at 2026-10-01  # latest backup at or before that time
```

Sync runs straight after, so changes made after that date come back from S3. To truly undo an edit,
make a new edit (the change log keeps the newest one) rather than restoring.

## 3. New Mac, or the old Mac was wiped

1. Install the tools: `brew install uv ffmpeg` and clone this repository.
2. `uv sync`, then give your terminal **Full Disk Access** (System Settings → Privacy & Security).
3. `aws login` and `export PHOTOCTL_BUCKET=…`
4. Run:

   ```sh
   uv run photoctl recover
   ```

   Pick the Mac you're replacing from the list. `recover`:
   - restores its identity (`device.json`), settings and both databases from the newest backup;
   - if that device has no backup, adopts its identity from the device registry, starts the catalog
     from the library snapshot and rebuilds the archive state from the sidecars in `meta/`;
   - syncs, so it has every change from every device.
5. Check: `uv run photoctl archive --all --dry-run` should report the photos you already archived as
   **already archived**. `uv run photoctl sync verify` should say **converged**.
6. Re-install the daily backup: `uv run photoctl db schedule install`.

The recovered Mac carries on as the same device. It never reuses a change id, because it reads its
own earlier changes from S3 before making new ones.

## 4. No usable backup at all

Rebuild both databases from what's in S3:

```sh
uv run photoctl catalog rebuild             # snapshot + newer changes (or every change if there's no snapshot)
uv run photoctl catalog rebuild --from-meta # last resort: only the per-photo sidecars
uv run photoctl state rebuild               # which Apple Photos items are archived
```

`--from-meta` loses edits made in the app (titles, tags, people names, favourites, hides): sidecars
only hold what was imported from Apple Photos. Use it only if the snapshot and changes are gone too.

## 5. A phone lost its data

Nothing to restore: open the app, choose **Catalogs → Use on this device** to download the library again.
Its backup progress is rebuilt by re-checking the camera roll against the server.

## What can be lost

Only edits made on a device in the moments between its last sync and the failure. Syncs run after
every archive run, every 60 seconds while the app is open, and on `photoctl sync`.
