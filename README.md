# photoctl

Phase 1: archive Apple Photos originals (photos and videos) to S3 and keep small copies with all metadata locally for processing.

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
   export PHOTOCTL_BUCKET=$(terraform output -raw bucket)
   ```
   This creates a private bucket with encryption, versioning, a TLS-only policy, lifecycle rules (`compressed/` → Standard-IA after 30 days, old versions expire after 30 days, incomplete uploads are aborted after 7 days) and an IAM policy `<bucket>-ingest` (upload/read/restore, no delete) that you can attach to the identity that runs photoctl.
   The bucket has `prevent_destroy`, and `terraform destroy` will refuse to remove it.
   Terraform state is kept locally in `infra/terraform.tfstate`, so keep that file safe.

## Usage

```sh
# select some photos in Photos.app, then:
uv run photoctl archive --selected --dry-run
uv run photoctl archive --selected

uv run photoctl archive --album "Goa 2024"
uv run photoctl archive --from 2024-01-01 --to 2025-01-01
uv run photoctl archive --all --download-missing      # whole library, fetch iCloud-only originals

uv run photoctl status                                # counts from ~/.photoctl/state.sqlite
uv run photoctl verify --sample 100                   # spot-check S3 against recorded sha256
```

Re-running is safe: photos already archived are skipped, and originals already in S3 aren't re-uploaded.

## "Moving" out of Photos

Photos has no API for deleting photos, so photoctl never deletes. Archived photos go into a **"Archived to S3"** album.
After `verify` passes, open that album in Photos, select all, and delete. Turn this off with `--no-mark-album`.

Deep Archive restores take about 12 hours. If you want to get originals back quickly in the first few weeks, use `--storage-class GLACIER_IR`.

## Contributing safely

After cloning, enable the leak guard once:

```sh
git config core.hooksPath .githooks
```

The pre-commit hook blocks photos and videos, SQLite databases, Terraform state, `.env`/config files and anything that looks like an AWS key or private key. `library/` and `test-output/` hold personal photos and are git-ignored.
