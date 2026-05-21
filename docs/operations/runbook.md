# Operations runbook

This file is the single-page reference for installing, restarting, and
inspecting the React + FastAPI deploy. It mirrors
`docs/superpowers/specs/2026-05-20-deployment-design.md` and assumes
the deploy artifacts under `deploy/` (nginx config, systemd unit,
deploy script) have been copied to the target host.

## Install (first time)

```bash
# 1. Lay down the systemd unit
sudo cp deploy/systemd/saa-api.service /etc/systemd/system/saa-api.service
sudo $EDITOR /etc/systemd/system/saa-api.service   # replace <EDIT-ME> with the service-account user
sudo systemctl daemon-reload

# 2. Lay down the nginx site
sudo cp deploy/nginx/stand-alone-analyzer.conf /etc/nginx/sites-available/stand-alone-analyzer
sudo ln -sfn /etc/nginx/sites-available/stand-alone-analyzer /etc/nginx/sites-enabled/stand-alone-analyzer
sudo nginx -t

# 3. Wire the local-disk thumbnail cache symlink (deployment-design §4.1)
sudo mkdir -p /var/cache/stand-alone-analyzer
sudo ln -sfn /var/lib/stand-alone-analyzer/.cache/stand-alone-analyzer/thumbnails \
    /var/cache/stand-alone-analyzer/thumbnails

# 4. Start the service
sudo systemctl enable --now saa-api
sudo systemctl reload nginx
```

## Atomic deploy of a new release

```bash
# After building venv + web/dist on a build host and rsync-ing them
# into /opt/saa/releases/<tag>/, run on the target host:
sudo bash deploy/scripts/deploy.sh <release-tag>
```

`deploy.sh` rotates `/opt/saa/current` and the
`/usr/share/stand-alone-analyzer/web` symlink atomically (`ln -sfn`),
runs `nginx -t`, then `systemctl restart saa-api` + `systemctl reload nginx`.

## Restart / reload

```bash
# Restart FastAPI (drops in-flight SSE; ~30s graceful shutdown window)
sudo systemctl restart saa-api

# Reload nginx (no client-visible drop)
sudo systemctl reload nginx

# Reload nginx after editing /etc/nginx/sites-available/stand-alone-analyzer
sudo nginx -t && sudo systemctl reload nginx
```

## Inspect logs

```bash
# Tail the FastAPI structured-JSON log
sudo journalctl -u saa-api -f -o cat

# Last hour, structured
sudo journalctl -u saa-api --since "1 hour ago" -o json | jq

# nginx access / error logs (Debian/Ubuntu paths)
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

## Health probe

```bash
# Cheap nginx-level probe
curl -s http://localhost/healthz
# -> "ok"

# Deep API probe
curl -s http://localhost/api/v1/health | jq
# -> {ok, version, smb_reachable, manifest_path_writable, ...}
```

## Rollback

Pinned decision #8 (Plan 5): rollback = git-revert the cutover PR,
rebuild the venv + dist, and re-run `deploy.sh` against the previous
release tag.

```bash
sudo bash deploy/scripts/deploy.sh <previous-release-tag>
```

There is NO on-host parallel-run of the old Streamlit app; that code
is gone post-cutover. Within the cutover window itself
(deployment-design §10.3), the old Streamlit binary may still exist
on the host — that fallback applies only between T-1 and T+7 days.

## Manual smoke checklist (run after every deploy)

These steps live in deployment-design §10.3 and Plan 5 Phase 8 Task 26.
They are not automated:

1. Open the SPA at `/`. Index renders, no console errors.
2. Pick the most recently used `analysis_folder/` in the sidebar.
3. Compute tab — the 7 step statuses load.
4. Selector tab — scatter renders, brushing works.
5. Clustering tab — labels load (or empty-state for a fresh project).
6. Explorer tab — 60×60 mosaic opens in <2s, panning is smooth.
7. `sudo systemctl restart saa-api`. Reload the page; state persists
   (manifest is on SMB; not in the dead process).
8. `journalctl -u saa-api --since "5 minutes ago"` — no ERROR lines.

## Environment variables

See deployment-design §8.3 for the canonical list. Key ones:

| Var | Default | Purpose |
|---|---|---|
| `SAA_BIND_HOST` | `127.0.0.1` | uvicorn listen address |
| `SAA_BIND_PORT` | `8000` | uvicorn port |
| `SAA_LOG_LEVEL` | `info` | log verbosity |
| `SAA_LOG_FORMAT` | `json` | `json` or `text` |
| `SAA_ALLOWED_ORIGINS` | `` (empty) | CORS allow-list (CSV) |
| `STAND_ALONE_THUMB_LOCAL_CACHE` | `1` | Opts into local-disk cache redirect |
| `HOME` | `/var/lib/stand-alone-analyzer` | Anchors `~/.cache/...` resolution |

The systemd unit (`deploy/systemd/saa-api.service`) sets these by
default. To override, edit `/etc/stand-alone-analyzer/backend.env`
(referenced via `EnvironmentFile=-` in the unit).

## Post-v1 notes

- TLS: not in v1; deployment-design §7.1 covers Let's Encrypt or
  institutional CA when required.
- SSO: not in v1; deployment-design §7.2 covers the `oauth2-proxy`
  add-on path (no code change, just nginx + a sidecar).
- Eviction: thumbnail cache has no eviction in v1; the manual
  `Clear cache` button per project is the only path. See
  deployment-design §4.3.
