# Upgrading Lumogis

This page is for an install made from the two release files (`docker-compose.yml` and `example.env`, see the [quickstart](quickstart.md)). A git checkout updates differently: see [From a git checkout](#from-a-git-checkout).

Run every command below from the install folder, the one that holds `docker-compose.yml` and `.env`.

---

## Updating

1. **Back up first** (see [Backup and restore](#backup-and-restore)):

   ```bash
   docker compose run --rm backup /scripts/backup/backup.sh run
   ```

2. **Read the release notes** on the [Releases page](https://github.com/lumogis/lumogis/releases). Each one says whether `docker-compose.yml` or `example.env` changed (see [When the compose file changed](#when-the-compose-file-changed)).

3. **Pull the new images and restart:**

   ```bash
   docker compose pull && docker compose up -d
   ```

   The `&&` matters: `up` runs only when every image was pulled, so a failed download leaves the running version alone. Database migrations run on the orchestrator's first start, then Lumogis is back.

4. **Check it is healthy:**

   ```bash
   docker compose ps
   curl -fsS http://localhost:8000/health
   ```

Updating stops Lumogis for a minute or two. Avoid the backup hour (03:00 by default): recreating the backup container stops a backup that is running.

---

## When the compose file changed

`docker compose pull` refreshes the images, not `docker-compose.yml`, which you downloaded once. When a release note says the compose file changed:

1. Download the new one over the old (your `.env` stays as it is):

   ```bash
   curl -fsSL -o docker-compose.yml https://github.com/lumogis/lumogis/releases/latest/download/docker-compose.yml
   ```

2. Compare the new `example.env` with your `.env` and copy over any new settings you want; the defaults work without them:

   ```bash
   curl -fsSL https://github.com/lumogis/lumogis/releases/latest/download/example.env | diff .env - | less
   ```

3. Then `docker compose pull && docker compose up -d` as above.

If you forget, **Admin → Diagnostics** shows the warning `compose_file_stale` (the file does not match the images you run), and the orchestrator log says the same at start-up.

**Never edit `docker-compose.yml`:** the next download would undo it. Put local changes in **`docker-compose.override.yml`** next to it, which Compose merges automatically. The **Settings → Ingest paths** page already writes its folder mounts there.

---

## Choosing the version

`LUMOGIS_VERSION` in `.env` decides which images Compose pulls:

| `LUMOGIS_VERSION` | You get |
| --- | --- |
| `release` (default) | The latest release; `docker compose pull` moves you to the next one |
| `0.13` | The newest `0.13.x` release |
| `0.13.0` | Exactly that release (no leading `v`) |

For a fixed build that can never change, pin each image by digest in `docker-compose.override.yml`. Each release lists its digests in `image-digests.txt`, next to the two files:

```yaml
services:
  orchestrator:
    image: ghcr.io/lumogis/lumogis-orchestrator@sha256:…
```

Every image is signed with build provenance by the public repository's workflow. To check one: `gh attestation verify oci://ghcr.io/lumogis/lumogis-orchestrator@sha256:… --repo lumogis/lumogis`.

---

## Going back

Database migrations only go forward, so going back to an older release needs the backup you took before updating.

1. Set the previous version in `.env`, for example `LUMOGIS_VERSION=0.12.0`, and download that release's `docker-compose.yml` (`https://github.com/lumogis/lumogis/releases/download/v0.12.0/docker-compose.yml`) if the compose file changed in between.
2. Restore the backup taken before the update (below).
3. `docker compose pull && docker compose up -d`.

Going back without a restore is not supported: an older orchestrator may not understand a database a newer one migrated.

---

## Backup and restore

The `backup` service runs a snapshot every night (03:00, `TZ` in `.env`) into `BACKUP_HOST_DIR` (default `./ai-workspace/backups`) and keeps a week of daily and a month of weekly snapshots. What is and is not covered, and how the files are stored: [Backup and restore](../guides/backup-restore.md).

| What | Command |
| --- | --- |
| Back up now | `docker compose run --rm backup /scripts/backup/backup.sh run` |
| List snapshots | `ls ai-workspace/backups/snapshots/` |
| Restore one | `docker compose stop orchestrator lumogis-web` then `docker compose run --rm -it backup /scripts/backup/restore.sh <YYYYMMDD-HHMMSS> --yes`, then `docker compose up -d` |

Keep a copy of `.env` somewhere safe as well: the backup does not include it, and it holds the key that decrypts saved passwords.

---

## Running Compose from somewhere else

The release `docker-compose.yml` tells the restart service where the install lives through `${PWD}`, the folder Compose runs in. Always run it from the install folder. If you start Lumogis from elsewhere (`docker compose -f /path/to/docker-compose.yml`, Portainer, a systemd unit), set the working directory or `PWD` to the install folder, or restarts from **Settings** cannot find it. Files Lumogis writes stay owned by your user: it runs as the owner of the install folder.

---

## From a git checkout

A checkout's `docker-compose.yml` builds the Lumogis images from the source:

```bash
git pull && make update
```

`make update` records the running images for `make rollback`, pulls the images it does not build, rebuilds the Lumogis images, copies capture media and the stop-entities list out of the old container, restarts and checks health. `make backup` and `make restore SNAPSHOT=<YYYYMMDD-HHMMSS>` run the commands above.

### Moving a git checkout to the two release files

The release files cover the default stack. Installs that use LibreChat (`COMPOSE_PROFILES=librechat`) or the GPU, speech or LiteLLM overlays stay on a checkout for now.

0. Bring the checkout to the current release first: `git pull && make update`. This copies capture media and the stop-entities list out of the old orchestrator container, which `docker compose down` would delete.
1. Back up: `make backup`.
2. In a **new** folder, download the two files ([quickstart](quickstart.md), step 2), but do not start yet.
3. Copy your old `.env` over the downloaded one. Keep `COMPOSE_PROJECT_NAME` (default `lumogis`), so the new install reuses the same database and index volumes. Remove `COMPOSE_FILE`, unless it names only files you carry over.
4. Point `WORKSPACE_HOST_DIR`, `FILESYSTEM_ROOT` and `BACKUP_HOST_DIR` at the old folders, for example `WORKSPACE_HOST_DIR=/home/me/lumogis/ai-workspace`, or move those folders into the new one.
5. Copy `docker-compose.override.yml` if you have one.
6. In the old folder run `docker compose down`, then in the new folder `docker compose up -d`.
