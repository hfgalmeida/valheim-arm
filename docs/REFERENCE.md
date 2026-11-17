# Server reference

Installation starts in the [README](../README.md). Run commands below in your installation directory unless a section says otherwise:

```sh
cd ~/valheim-arm
```

- [Configuration](#configuration)
- [Network and firewall](#network)
- [Oracle Cloud walkthrough](#oracle-cloud)
- [Panel and certificates](#certificate)
- [Backups and persistent files](#backups)
- [Cloud backups](#cloud-backups)
- [BepInEx and plugins](#mods)
- [Players and panel accounts](#players)
- [Alerts](#alerts)
- [Image updates and rollback](#image-updates)
- [Support reports](#support)

<a id="configuration"></a>
## Configuration

Use **Game settings** for world/access rules, **Server settings** for schedules/alerts, and **Mods** for BepInEx. Each section saves independently. Panel overrides take precedence over `.env`; **Use Compose** removes an override. Restart the game to apply game settings. Selecting a different world preserves existing world files.

Host/image/FEX options require editing `.env` and recreating containers. Stop the game and create a backup before an intentional interruption:

```sh
sudo nano .env
sudo docker compose config --quiet
sudo docker compose up -d
```

| Variable | Default | Meaning |
|---|---|---|
| `VALHEIM_IMAGE` | `hfgalmeida/valheim-arm:latest` | Public image; use `:1.0.0` or a digest to pin it |
| `SERVER_IP` | Required | Reachable public IPv4 for HTTPS/certificate |
| `SERVER_NAME` / `WORLD_NAME` | `Valheim ARM` / `Dedicated` | Server listing and selected save |
| `SERVER_PASSWORD` | Required | Game password, 5–128 characters |
| `PUBLIC` / `CROSSPLAY` | `true` / `false` | Listing and PlayFab crossplay |
| `PRESET` | Empty | normal, casual, easy, hard, hardcore, immersive, hammer |
| `SAVE_INTERVAL` / `NATIVE_BACKUPS` | `1800` / `4` | Save interval in seconds and game-native backup count |
| `TIMEZONE` | `America/Sao_Paulo` | IANA time zone for schedules and displayed dates |
| `RESTART_AT` / `BACKUP_AT` | `05:00` / `05:00` | Daily `HH:MM` or `off`; matching times share maintenance |
| `AUTO_UPDATE` | `true` | Daily game-update check near scheduled restart |
| `RETAIN_DAILY` / `RETAIN_WEEKLY` | `7` / `4` | Distinct daily/weekly backup representatives |
| `BEPINEX` | `false` | Enable the bundled package |
| `VALHEIM_MEMORY_LIMIT` | `10g` | Game/supervisor container RAM ceiling |
| `VALHEIM_MEMORY_SWAP_LIMIT` | `14g` | Combined RAM + swap ceiling; does not create swap |

The game publishes UDP 2456–2457. The panel publishes TCP 443; API port 8080 remains internal.

### World modifiers

An empty value uses the world default. Individual modifiers override the preset.

| Variable | Non-empty values |
|---|---|
| `MODIFIER_COMBAT` | veryeasy, easy, hard, veryhard |
| `MODIFIER_DEATHPENALTY` | casual, veryeasy, easy, hard, hardcore |
| `MODIFIER_RESOURCES` | muchless, less, more, muchmore, most |
| `MODIFIER_RAIDS` | none, muchless, less, more, muchmore |
| `MODIFIER_PORTALS` | casual, hard, veryhard |
| `KEY_NOBUILDCOST`, `KEY_PLAYEREVENTS`, `KEY_PASSIVEMOBS`, `KEY_NOMAP` | true or false |

### Memory and FEX

Check host memory/swap before changing limits:

```sh
free -h
swapon --show
sudo docker stats --no-stream valheim-server
```

The default 10 GiB RAM / 14 GiB combined limit leaves room on a 12 GiB host. Equal RAM and combined limits disable container swap. Host swap is required to use it; heavy swapping can slow the game, and swap does not guarantee protection from OOM termination.

| Advanced variable | Default | Meaning |
|---|---|---|
| `FEX_GAME_MODE` | `auto` | `standard`, `compatible`, or automatic compatibility retry after an early fatal startup |
| `FEX_DISKCACHE` | `0` | Optional persistent JIT cache in `data/cache` |
| `FEX_DISKCACHEMAXFILESIZE` | `1073741824` | Main cache-file limit; index may use additional space |
| `FEX_MAXINST` / `FEX_MULTIBLOCK` | `5000` / `1` | JIT block settings |
| `FEX_TSOENABLED` | `1` | Preserve x86 memory ordering |

Compatibility mode uses full code checks, disables MonoHacks/multiblock/disk cache, and keeps TSO. It can reduce performance and is not a proven fix for every crash. Automatic selection lasts until the supervisor restarts; each running game keeps its selected profile. See **Overview → Server details → Startup and recovery**. FEX itself is updated by installing a new image.

<a id="network"></a>
## Network and firewall

Allow inbound **UDP 2456–2457** and **TCP 443** in both provider/router rules and the host firewall. Keep your existing SSH rule. For a public game/panel use source `0.0.0.0/0`; you do not need to register your home computer's IP. To restrict panel access, use your chosen source CIDR in the provider firewall instead.

For active UFW, the installer adds these rules without enabling/disabling UFW or changing other rules. To inspect or add them manually:

```sh
sudo ufw status verbose
sudo ufw allow 2456:2457/udp
sudo ufw allow 443/tcp
sudo docker compose ps
sudo ss -lntup
```

Docker publishes ports through its own forwarding/NAT rules and may bypass UFW restrictions. Keep provider/router restrictions in place; do not disable Docker firewall management. [Docker firewall documentation](https://docs.docker.com/engine/network/packet-filtering-firewalls/).

<a id="oracle-cloud"></a>
## Oracle Cloud walkthrough

1. Create an **ARM64 Compute instance**, such as an Ampere A1 shape, with a supported Ubuntu ARM64 image. An example allocation is **2 OCPUs and 12 GB RAM**, with at least 30 GB free storage.
2. Use a public subnet, an assigned public IPv4, and an internet-gateway route. Preserve the SSH ingress rule for your administration network.
3. Open **Compute → Instances → your instance**, then its **primary VNIC** and subnet. Open an associated **Security List** and add ingress rules. Alternatively, use a **Network Security Group** attached to that VNIC.
4. Add the following **stateful** rules, leaving the **source port range as All**:

   | Source CIDR | Protocol | Destination ports |
   |---|---|---|
   | `0.0.0.0/0` | UDP | `2456–2457` |
   | `0.0.0.0/0` | TCP | `443` |

5. Keep working egress access so SteamCMD, image downloads, and crossplay services can connect out. Additional network policies must allow the same traffic. See [OCI security rules](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securityrules.htm).
6. Connect using your SSH key and the public instance IP, then run the installer:

```sh
ssh -i ~/.ssh/server.key ubuntu@YOUR_SERVER_IP
curl -fL https://raw.githubusercontent.com/hfgalmeida/valheim-arm/main/install.sh -o install-valheim.sh
sudo bash install-valheim.sh
```

The installer cannot change cloud security lists, NSGs, or routing. A public TCP 443 rule permits panel access from changing networks; it does not require your computer's public IP. The panel still requires authentication.

<a id="certificate"></a>
## Panel and certificates

Open `https://YOUR_SERVER_IP` and sign in as `admin`. If the server IP changes, edit `SERVER_IP` in `.env` and apply `sudo docker compose up -d`.

### Trust the local certificate once per computer

On the server, export **only the public root certificate** and note its SHA256 fingerprint:

```sh
sudo docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./valheim-root.crt
sudo chmod 644 valheim-root.crt
openssl x509 -in valheim-root.crt -noout -fingerprint -sha256
```

Replace the SSH username, key, IP, and installation path below for your host.

**Windows 10/11:** if `Get-Command scp` fails, install the OpenSSH client in an administrator PowerShell:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

Then, in the browser user's PowerShell:

```powershell
scp -i "$env:USERPROFILE\.ssh\server.key" ubuntu@YOUR_SERVER_IP:/home/ubuntu/valheim-arm/valheim-root.crt "$env:USERPROFILE\Downloads\valheim-root.crt"
$rootCert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new("$env:USERPROFILE\Downloads\valheim-root.crt")
$sha256 = [System.Security.Cryptography.SHA256]::Create()
[BitConverter]::ToString($sha256.ComputeHash($rootCert.RawData)).Replace("-", ":")
$sha256.Dispose()
$rootCert.Dispose()
```

Compare that fingerprint with the server output. Only if they match, import for the current user and reopen the browser:

```powershell
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\valheim-root.crt" -CertStoreLocation Cert:\CurrentUser\Root
```

**Ubuntu/Debian desktop:**

```sh
sudo apt-get update
sudo apt-get install -y openssh-client ca-certificates
scp -i ~/.ssh/server.key ubuntu@YOUR_SERVER_IP:/home/ubuntu/valheim-arm/valheim-root.crt ./valheim-root.crt
openssl x509 -in valheim-root.crt -noout -fingerprint -sha256
```

After matching the server fingerprint:

```sh
sudo cp valheim-root.crt /usr/local/share/ca-certificates/valheim-root.crt
sudo update-ca-certificates
```

Browsers with their own certificate store may require importing it there too. Keep the `caddy_data` volume to preserve the certificate authority. [Microsoft OpenSSH instructions](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse).

### Recover the primary administrator password

```sh
sudo docker compose run --rm --no-deps valheim set-password
```

This invalidates existing sessions. Additional accounts are managed in **Administration**.

<a id="backups"></a>
## Backups and persistent files

Use **Backups** to download archives or restore with the game stopped. Restoration replaces saves after validation and preserves a pre-restore backup. A full backup contains world saves, selected settings, and plugin configuration; it does not include the image, plugin DLLs, cloud credentials, or panel passwords.

The bind mount `./data:/data` survives `docker compose down`, container replacement, and image updates. Removing the host directory/disk does not. Keep off-host copies.

The installer grants the invoking SSH user ongoing access to saves, backups, editable overrides, and mods. Private credentials and internal state remain restricted. To prepare access again after changing SSH users:

```sh
sudo docker compose run --rm --no-deps --user 0:0 \
  --cap-add CHOWN --cap-add FOWNER --cap-add DAC_OVERRIDE \
  valheim prepare-access --access-uid "$(id -u)"
```

Use shared subdirectories rather than recursively changing ownership/permissions on `data`:

```sh
ls data/saves data/backups data/mods
```

To import a world, stop the game and back it up first. Copy its complete matching `.fwl`, `.db`/`.db2`, and chunk directories, preserving relative names. For example, from the computer holding a `worlds_local` directory:

```sh
scp -i ~/.ssh/server.key -r ./worlds_local/. ubuntu@YOUR_SERVER_IP:/home/ubuntu/valheim-arm/data/saves/worlds_local/
```

Select the matching world name in **Game settings**, then start the game. Do not mix generations of chunked saves or copy a world while it is being written.

<a id="cloud-backups"></a>
## Cloud backups

On your computer, install [rclone](https://rclone.org/downloads/) and create one authorized remote:

```sh
rclone config
rclone config file
```

Choose OneDrive, Dropbox, or Google Drive and complete browser authorization. In **Backups → Cloud backups**, import the account section from `rclone.conf`, choose a relative folder such as `Valheim/backups`, enable uploads, and save. The saved connection is never displayed again.

New complete backups and checksums are uploaded automatically. Failed uploads retry; pending local copies are protected from retention. Cloud files are not deleted automatically. Changing the folder/account redirects pending uploads to the new destination. Cloud restoration requires downloading the complete archive back to the server; the panel restores local archives.

<a id="mods"></a>
## BepInEx and plugins

In **Mods**, enable the bundled BepInEx package, save, and restart. Plugin compatibility varies; check the session diagnostics and use trusted plugins.

With the game stopped, upload individual managed DLLs up to 16 MiB. Confirm replacements; one previous version is kept. **Delete** preserves a recovery copy under **Deleted plugins**; restoration never overwrites an installed DLL. Configuration files are preserved. Recovery copies use disk space and are not pruned automatically.

Use the editor for generated UTF-8 files up to 32 KiB under `BepInEx/config`. Save while stopped, then start the game. Other files can be managed through SSH:

```sh
scp -i ~/.ssh/server.key ./Example.dll ubuntu@YOUR_SERVER_IP:/home/ubuntu/valheim-arm/data/mods/BepInEx/plugins/
scp -i ~/.ssh/server.key ubuntu@YOUR_SERVER_IP:/home/ubuntu/valheim-arm/data/mods/BepInEx/config/example.cfg ./example.cfg
scp -i ~/.ssh/server.key ./example.cfg ubuntu@YOUR_SERVER_IP:/home/ubuntu/valheim-arm/data/mods/BepInEx/config/example.cfg
```

Advanced custom packages use `BEPINEX_URL` (HTTPS) plus `BEPINEX_SHA256`; both are unnecessary for the bundled package.

<a id="players"></a>
## Players and panel accounts

In **Players**, select **Administrators**, **Banned**, or **Permitted**; add/remove IDs and **Save list**. A non-empty permitted list restricts access to those IDs.

Known-player suggestions show registered names/platforms and explicit IDs observed in logs. Other-platform transport IDs require checking/correcting against the game's F2 panel. **Identify or confirm a player** saves your label and exact ID, without granting permissions. Add the confirmed player to a list and save separately. Do not use join codes, ZDOIDs, or connection handles.

In **Administration**, create additional administrator/operator accounts, deactivate accounts, or reset passwords. First sign-in and password resets require a new personal password. Deactivation/password changes invalidate sessions and pending requests. The primary `admin` account remains available for recovery.

| Capability | Administrator | Operator |
|---|---|---|
| State, metrics, join code, start | Yes | Yes |
| Stop/restart | Immediate, with clean-save checks | Waits for confirmed zero players and applicable Steam checks |
| Settings, mods, permissions, backups, updates | Yes | No |
| Full logs, accounts, audit history | Yes | No |
| Own operation results and password change | Yes | Yes |

<a id="alerts"></a>
## Alerts

In **Server settings → Alerts and monitoring**, choose independent local-backup/cloud-upload deadlines (default **36 hours**; **0 disables** a check). Without a prior backup, the grace period survives restarts. Cloud checks apply only when uploads are enabled. Dates/deadlines appear in **Backups**, and overdue/unavailable copies generate panel warnings even without Discord.

To notify Discord, create a channel webhook under **Channel settings → Integrations → Webhooks**, enter it, enable notifications, save, and **Send test**. An empty URL keeps the saved credential.

Alerts cover explicit backup/upload failures, overdue copies, suspended recovery, and low disk space. Categories notify on failure/recovery with a five-minute minimum interval and retries. The loop checks every 30 seconds. Host/network outages can prevent delivery, so this does not replace external monitoring.

<a id="image-updates"></a>
## Image updates and rollback

Game updates are automatic; image updates replace FEX, panel, and proxy. Use **Server settings → Update image**; the installer prepares a restricted host service without exposing the Docker socket to containers.

Before upgrading a pre-1.0 installation, refresh its host updater first. Run these commands **from your existing installation directory**; the installer preserves its files and leaves running containers in place:

```sh
curl -fL https://raw.githubusercontent.com/hfgalmeida/valheim-arm/main/install.sh -o /tmp/install-valheim.sh
sudo env VALHEIM_INSTALL_DIR="$PWD" bash /tmp/install-valheim.sh
```

Then request the image update in the panel. Repeat this host refresh when release notes require new host tooling.

The CLI performs the same controlled update:

```sh
sudo python3 update-server.py
```

It checks the new image, stops cleanly, creates a backup, switches both containers, and verifies readiness. Failed verification attempts rollback. To pin an explicit version or restore the recorded previous image:

```sh
sudo python3 update-server.py --image hfgalmeida/valheim-arm:1.0.0
sudo python3 update-server.py --rollback
```

If a completed manual recovery left an obsolete updater record, verify/reconcile without switching images:

```sh
sudo python3 update-server.py --reconcile
```

A stopped-game preference is preserved. Older images that cannot honor it are refused before shutdown. If automatic rollback cannot verify readiness, complete recovery on the host before retrying. Updating the image does not overwrite `.env`, your Compose configuration, or certificate volumes.

<a id="support"></a>
## Support reports

From the installation directory:

```sh
sudo python3 scripts/collect-diagnostics.py --include-logs
```

The local archive contains selected host/Docker facts, image/FEX versions, container health/limits, non-secret configuration, startup/maintenance information, plugin filenames/sizes, and optional recent redacted logs. It does not bundle saves, credential files, plugin binaries, full environment/Compose output, or private keys. Omit `--include-logs` for metadata only. Collection does not restart or modify the game and does not upload anything.

Download the archive to your computer, replacing the filename printed by the collector:

```sh
scp -i ~/.ssh/server.key ubuntu@YOUR_SERVER_IP:/home/ubuntu/valheim-arm/valheim-diagnostics-TIMESTAMP.tar.gz ./
```

Extract and review before sharing:

```sh
tar -xzf valheim-diagnostics-TIMESTAMP.tar.gz
```

Attach the reviewed report to a [GitHub bug report](https://github.com/hfgalmeida/valheim-arm/issues/new/choose). Redaction is best-effort, particularly for arbitrary third-party plugin messages. If the installed collector is missing, download a current copy on the host:

```sh
curl -fL https://raw.githubusercontent.com/hfgalmeida/valheim-arm/main/scripts/collect-diagnostics.py -o /tmp/valheim-collect-diagnostics.py
sudo python3 /tmp/valheim-collect-diagnostics.py --directory "$PWD" --include-logs
```
