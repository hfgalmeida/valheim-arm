# Changelog

## 1.0.1

- Resume automatic recovery after a fatal game process exits, even if forced termination was previously blocked to preserve saves.
- Keep manual stop preferences, container shutdown, retry delays, and the five-failure limit in effect.
- Summarize identical fatal-signal log floods without hiding distinct diagnostics.
- Preserve existing saves and the unclean-exit marker; never create a backup or restore a world as part of fatal recovery.

## 1.0.0

First general ARM64 release.

- English panel, CLI messages, notifications, installation, and documentation.
- Docker Compose installation with separate game/supervisor and HTTPS proxy containers.
- Persistent saves, selective SSH file access, backups/restoration, cloud exports, and overdue-backup monitoring.
- Administrator/operator accounts, first-sign-in password changes, and audit history.
- Crossplay controls and conservative player-presence checks for protected maintenance.
- Bundled optional BepInEx, plugin upload/recovery/deletion, and a configuration editor.
- Restricted host image updater with readiness checks and rollback.
- Startup diagnostics, bounded recovery, and optional FEX compatibility behavior.
- Local redacted diagnostics collector and structured GitHub bug reports.

### Upgrading

Back up first, then use **Server settings → Update image** or `sudo python3 update-server.py`. Existing world files, passwords, accounts, and run preferences are preserved. Pre-1.0 operation states are normalized when read. The installer preserves existing Compose/.env files; rerun the current installer when host-side tooling or integration needs refreshing.

### Scope

The image requires compatible Linux ARM64 hardware with 4 KiB pages. Third-party plugin/platform compatibility varies. The FEX compatibility profile is a mitigation with potential performance cost, not a guarantee against native crashes. Keep independent off-host backups and report problems with a reviewed diagnostics archive.
Pre-1.0 installations must [refresh the host updater](docs/REFERENCE.md#image-updates) before updating the image to 1.0.0.
