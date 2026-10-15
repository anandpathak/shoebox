# Shoebox

Your family photo shoebox, kept safe and searchable. Shoebox moves the originals from Apple Photos (and from
your phones) into your own Amazon S3 bucket at Deep Archive prices, keeps a small copy of each photo with all its
metadata on your Mac, and gives you an app to browse and search everything from the Mac, an iPhone or an Android
phone:

- search by place, date, people, what's in the photo ("beach", "receipt"), or in plain English ("kids playing in
  the snow"), with labels, faces, text and captions worked out on your Mac (nothing is sent to an AI service);
- several Macs and phones stay in sync through the same bucket; each device can also search offline;
- automatic backup of new photos from phones (iOS/Android app in `app/`);
- database backups to S3 and a recovery path for a lost Mac.

Everything runs on your own machines and your own AWS account. The command is `shoebox`; your data lives in
`~/.shoebox` (an existing `~/.photoctl` from the app's earlier name is moved there automatically).

Videos need `ffmpeg` (`brew install ffmpeg`); without it the original is still archived, just with no compressed copy. Use `--no-videos` to skip them.

For each photo or video:

| Output | Where | Notes |
|---|---|---|
| Original (HEIC/JPEG/RAW…) | `s3://$BUCKET/raw/YYYY/YYYY-MM/<photo_id>.<ext>` | Deep Archive by default, SHA-256 checked by S3 |
| Compressed JPEG | `library/compressed/YYYY/YYYY-MM/<photo_id>.jpg` (+ same key in S3) | 1600px long edge, q80, EXIF/GPS/date/camera kept (via macOS `sips`) |
| Compressed video | `library/compressed/YYYY/YYYY-MM/<photo_id>.mp4` (+ S3) | 720p H.264, CRF 28 via ffmpeg; creation date, GPS, camera kept |
| Video poster frame | `library/compressed/YYYY/YYYY-MM/<photo_id>.jpg` (+ S3) | 1600px frame at 1s, for image processing |
| Sidecar JSON | `library/meta/YYYY/YYYY-MM/<photo_id>.json` (+ same key in S3) | sha256, keys, plus Apple Photos metadata: people, albums, keywords, title, place, labels… |

`photo_id` = first 26 chars of base32(sha256(original)), so `raw/…/X.heic` ↔ `compressed/…/X.jpg` by string substitution.

## Setup

1. Give your terminal app **Full Disk Access** (System Settings → Privacy & Security), then restart it.
2. Make sure AWS credentials work: `aws login` (or `aws sso login`).
3. Create the bucket (works with `terraform` or `tofu`):
   ```sh
   cd infra
   # the Terraform AWS provider can't read `aws login` sessions directly; hand it temporary creds:
   eval "$(aws configure export-credentials --format env)"
   terraform init
   terraform apply            # -var region=ap-south-1 -var aws_profile=... to override
   export SHOEBOX_BUCKET=$(terraform output -raw bucket)
   ```
   This creates a private bucket with encryption, versioning, a TLS-only policy, lifecycle rules (`compressed/` → Standard-IA after 30 days, old versions expire after 30 days, incomplete uploads are aborted after 7 days) and an IAM policy `<bucket>-ingest` (upload/read/restore, no delete) that you can attach to the identity that runs shoebox.
   The bucket has `prevent_destroy`, and `terraform destroy` will refuse to remove it.
   Terraform state is kept locally in `infra/terraform.tfstate`, so keep that file safe.

## Usage

```sh
# select some photos in Photos.app, then:
uv run shoebox archive --selected --dry-run
uv run shoebox archive --selected

uv run shoebox archive --album "Goa 2024"
uv run shoebox archive --from 2024-01-01 --to 2025-01-01
uv run shoebox archive --all --download-missing      # whole library, fetch iCloud-only originals

uv run shoebox status                                # this device, archive counts, catalog size
uv run shoebox verify --sample 100                   # spot-check S3 against recorded sha256
```

Re-running is safe: photos already archived are skipped, and originals already in S3 aren't re-uploaded.
Only one archive run can happen at a time on a device; each run writes a progress log to `~/.shoebox/runs/`.

### Catalog (searchable SQLite)

Every archive run also adds items to `~/.shoebox/catalog.sqlite` (dates, places, cameras, Apple people, albums,
keywords and labels, with full-text search) and makes a 320px thumbnail under `thumb/`.
With `--local-only`, the catalog goes to `<out>/catalog.sqlite` so test runs never touch the real one.

```sh
uv run shoebox catalog stats
uv run shoebox catalog backfill                      # rebuild from ./library/meta sidecars
uv run shoebox catalog backfill --from-s3            # …or from s3://$SHOEBOX_BUCKET/meta/
uv run shoebox catalog export -o library.sqlite.gz   # consistent copy of everything
uv run shoebox catalog export --flavour contributed -o this-mac.sqlite.gz   # only this device's items
uv run shoebox thumbs backfill --bucket "$SHOEBOX_BUCKET"                  # thumbs for older items
uv run shoebox device                                # this device's id and name
```

### Searching by what's in a photo, and who

Photos from Apple Photos come with Apple's labels and the names you gave faces there. Everything else (phone
uploads, backups from the app) is analysed on this Mac:

```sh
uv run shoebox analyze        # label photos and find faces (the server also does this in the background)
uv run shoebox faces          # groups of faces still waiting for a name
```

- **Text in photos** (signs, receipts, documents) is read with Apple's on-device text recognition and is searchable
  everywhere: *"croma invoice"*.
- **Labels** come from Apple's Vision framework (on-device): *beach, mountain, food, dog, document…* plus
  everyday words like *landscape, people, portrait, group, sunset*. They're ordinary tags, so search finds
  them on every device; remove a wrong one like any tag.
- **Faces** are found with OpenCV (two small open models, downloaded once into `~/.shoebox/models`). Similar
  faces are grouped; in the app go to **People → Who's this?** and type a name once. Every photo of that
  person, now and later, can then be searched by name. Faces you named in Apple Photos are learnt
  automatically. *Not a person* hides a group. A wrong match: open the photo and remove that person from it.
- Face fingerprints are biometric data. They live in `~/.shoebox/faces.sqlite` (readable only by you) and aren't
  synced to other devices or put in published catalogs. They *are* included in the database backups in your own
  private, encrypted bucket, so a replacement Mac keeps recognising people; `faces = false` under `[backup]` in
  `config.toml` leaves them out. Names you confirm are part of the catalog and always sync.
- Turn background analysis off with `analysis = false` under `[server]` in `~/.shoebox/config.toml`.

#### Plain-English search and captions (optional AI extras)

```sh
uv sync --extra ai                    # PyTorch + transformers, about 1 GB
uv run shoebox analyze               # also writes captions and search vectors (first run downloads ~2 GB of models)
uv run shoebox search --best "kids playing in the snow"
```

- **Plain-English search** (SigLIP 2): describe what you're after, e.g. *"birthday cake with candles"*, *"Asha at
  the beach in 2024"*. Names of people and dates become filters; the rest is matched by meaning. In the app,
  typed searches show **Best matches** first; **Newest first** switches back to plain keyword order. This
  needs the Mac (it turns your words into the same kind of vector); phones searching offline use keywords.
- **Captions** (Florence-2): a sentence about each photo (*"A brown dog running on a sandy beach"*). Captions
  sync and are part of keyword search on every device, offline too. Edit one in the viewer and yours is kept.
- Both run on the Mac's GPU. Photo vectors stay in `~/.shoebox/embeddings.sqlite` on this Mac.
- Different models: `[analysis] search_model = "…"` / `caption_model = "…"` in `config.toml` (Hugging Face ids).

### Database backups

At the end of every archive run, `state.sqlite`, `catalog.sqlite`, `faces.sqlite`, `device.json` and `config.toml` are backed up to
`s3://$SHOEBOX_BUCKET/backups/<device_id>/<timestamp>/`. Each database is copied consistently and integrity-checked
before upload. The first backup of each month is also kept under `monthly/`.

```sh
uv run shoebox db backup                             # back up now
uv run shoebox db list                               # this device's backups, newest first
uv run shoebox db list --device all                  # every device that has backups
uv run shoebox db restore                            # latest backup; current files kept as .bak
uv run shoebox db restore --at 2026-10-01            # point in time
uv run shoebox db restore --device <id>              # on a new Mac: carry on as that device
```

## The app (Mac, iPhone, Android)

```sh
cd web && npm install && npm run build && cd ..   # once, and after updating
uv run shoebox serve                              # http://127.0.0.1:8765 on this Mac
```

### On your phones

1. Install **Tailscale** on the Mac and on each phone, signed in with the same account.
   In the Tailscale admin console turn on **MagicDNS** and **HTTPS certificates** (DNS page).
2. Start the app for phones:
   ```sh
   uv run shoebox serve --setup-tailscale         # runs `tailscale serve --bg 8765` the first time
   ```
   It prints `https://<your-mac>.<tailnet>.ts.net` and a QR code. Scan it with the phone camera.
3. Install it as an app: **iPhone** Safari → Share → *Add to Home Screen*; **Android** Chrome → ⋮ → *Install app*.
4. Family members: add their Tailscale logins to `~/.shoebox/config.toml`:
   ```toml
   [server]
   allowed_logins = ["you@example.com", "partner@example.com"]
   ```

Nothing is exposed to the internet: the address only works for devices in your tailnet, and every
request carries the Tailscale identity the server checks.

### On the same Wi-Fi, without Tailscale

```sh
uv run shoebox serve --lan        # prints http://<mac-ip>:8765, a pairing code and a QR code
uv run shoebox pair               # a new code (they last 30 minutes), while the server runs
uv run shoebox paired             # paired phones;  `shoebox paired revoke "<name>"` cuts one off
```

Scan the QR code with the phone (or type the address into the app and enter the code). Each phone gets its own
token; nothing gets in without pairing. The connection is plain http, so use this on your home Wi-Fi only. Over
http, browsers turn off offline mode, on-device search and *Install app*; the native app's backup works.

### Automatic backup from phones (the native app)

A web page can't read the camera roll, so automatic backup needs the iPhone/Android app in [`app/`](app/README.md).
It's the same web app, loaded from your Mac over Tailscale, plus a plugin that lists new photos and videos, hashes
them on the phone, asks the Mac which ones it doesn't have (`POST /api/backup/check`), and uploads only those
(`POST /api/uploads/raw`, sent by iOS/Android in the background). Turn it on under **Sync → Back up this phone**.

The native projects need Xcode (iPhone) or Android Studio (Android) to build; see [app/README.md](app/README.md).
Without the app, phones can still pick photos by hand under **Uploads**.

## A second Mac

Every Mac keeps its own catalog and syncs through the same bucket; it doesn't need its own Photos library.

```sh
git clone <this repo> && cd shoebox
brew install uv ffmpeg && uv sync
aws login                                      # an identity with the <bucket>-ingest policy
export SHOEBOX_BUCKET=<your bucket>
uv run shoebox sync                           # starts from the library snapshot, then catches up
uv run shoebox sync verify                    # "converged" once every device has synced
uv run shoebox devices                        # every device, its platform and when it last synced
```

To replace a lost or wiped Mac (carry on as the same device), use `uv run shoebox recover` instead;
see [docs/RECOVERY.md](docs/RECOVERY.md).

## "Moving" out of Photos

Photos has no API for deleting photos, so shoebox never deletes. Archived photos go into a **"Archived to S3"** album.
After `verify` passes, open that album in Photos, select all, and delete. Turn this off with `--no-mark-album`.

Deep Archive restores take about 12 hours. If you want to get originals back quickly in the first few weeks, use `--storage-class GLACIER_IR`.

## Contributing safely

After cloning, enable the leak guard once:

```sh
git config core.hooksPath .githooks
```

Commit with a no-reply address so your email isn't in the history (the hook refuses gmail/outlook/icloud and similar):

```sh
git config user.email <your-github-username>@users.noreply.github.com
```

The pre-commit hook blocks photos and videos, SQLite databases, Terraform state, `.env`/config files and anything that looks like an AWS key or private key. `library/` and `test-output/` hold personal photos and are git-ignored.
