"use strict";
const $ = (id) => document.getElementById(id);
let csrf = "",
  events = null,
  samples = [],
  currentConfig = null;
let panelUser = null;
let lastPanelState = null;
function isAdmin() {
  return panelUser?.role === "admin";
}
function acceptSession(value) {
  csrf = value.csrf;
  panelUser = value.user || {
    id: "admin",
    username: "admin",
    role: "admin",
    must_change_password: false,
  };
}
const labels = {
  server_name: "Server name",
  world_name: "World name",
  server_password: "Game password (leave blank to keep)",
  server_port: "Port (Compose)",
  public: "Public listing",
  crossplay: "Crossplay",
  preset: "Preset",
  modifier_combat: "Combat",
  modifier_deathpenalty: "Death penalty",
  modifier_resources: "Resources",
  modifier_raids: "Raids",
  modifier_portals: "Portals",
  key_nobuildcost: "No build cost",
  key_playerevents: "Player-based events",
  key_passivemobs: "Passive enemies",
  key_nomap: "No map",
  save_interval: "Save interval (seconds)",
  native_backups: "Native backups",
  timezone: "Time zone",
  restart_at: "Daily restart (HH:MM or off)",
  backup_at: "Daily backup (HH:MM or off)",
  auto_update: "Update game automatically",
  retain_daily: "Daily retention",
  retain_weekly: "Weekly retention",
  bepinex: "Enable BepInEx",
};
function notice(text) {
  $("notice").textContent = text;
  if (text && window.matchMedia("(max-width: 760px)").matches)
    requestAnimationFrame(() => $("notice").scrollIntoView({ block: "nearest" }));
}
async function api(path, method = "GET", body) {
  const headers = { "Content-Type": "application/json" };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const r = await fetch("/api/v1" + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const v = await r.json();
  if (!r.ok) {
    if (r.status === 401) signedOut();
    throw new Error(typeof v.detail === "string" ? v.detail : JSON.stringify(v.detail));
  }
  return v;
}
let imageUpdateTimer = null;
let imageUpdateRequesting = false;
function signedOut() {
  clearInterval(imageUpdateTimer);
  imageUpdateTimer = null;
  if (events) events.close();
  events = null;
  csrf = "";
  panelUser = null;
  lastPanelState = null;
  $("mobile-section").replaceChildren();
  currentConfig = null;
  samples = [];
  maintenanceJobs = [];
  knownPlayerRecords = [];
  $("player-identity-form").reset();
  $("player-identity-details").open = false;
  $("identity").textContent = "";
  $("own-password").hidden = true;
  $("password-change").hidden = true;
  $("password-change-form").reset();
  for (const id of [
    "settings",
    "server-settings",
    "mods-settings",
    "backup-list",
    "user-list",
    "account-list",
    "audit-list",
    "logs",
    "jobs",
    "plugin-list",
    "deleted-plugin-list",
    "mod-config-file",
    "mod-session-plugins",
    "mod-session-issues",
    "known-player",
    "startup-history",
    "account-reset-user",
  ])
    if ($(id)) $(id).replaceChildren();
  for (const id of ["cloud-form", "alerts-form", "account-create-form", "account-reset-form"])
    $(id)?.reset();
  $("dashboard").hidden = true;
  $("logout").hidden = true;
  $("login").hidden = false;
  $("mod-config-content").value = "";
  modConfig = null;
  $("plugin-file").value = "";
  $("plugin-upload-status").textContent = "";
  for (const id of ["user-entries", "player-id", "cloud-config", "alerts-url"]) $(id).value = "";
  for (const id of [
    "cloud-status",
    "alerts-status",
    "local-backup-freshness",
    "cloud-backup-freshness",
    "backup-age-warning",
    "backup-age-warning",
    "local-backup-freshness",
    "cloud-backup-freshness",
    "last-failure",
    "mod-session-status",
    "mod-session-version",
    "mod-session-pending",
    "versions",
    "image-version",
    "startup-summary",
    "run-intent",
  ])
    $(id).textContent = "";
}
function bytes(v) {
  return v >= 1073741824
    ? (v / 1073741824).toFixed(1) + " GiB"
    : v >= 1048576
      ? (v / 1048576).toFixed(1) + " MiB"
      : v >= 1024
        ? (v / 1024).toFixed(1) + " KiB"
        : Math.round(v) + " B";
}
function chart(id, values, ceiling) {
  const c = $(id),
    x = c.getContext("2d"),
    rect = c.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const scale = Math.min(window.devicePixelRatio || 1, 3);
  const w = rect.width,
    h = rect.height;
  const width = Math.round(w * scale),
    height = Math.round(h * scale);
  if (c.width !== width) c.width = width;
  if (c.height !== height) c.height = height;
  x.setTransform(scale, 0, 0, scale, 0, 0);
  x.clearRect(0, 0, w, h);
  x.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--line").trim();
  x.beginPath();
  x.moveTo(0, h - 1);
  x.lineTo(w, h - 1);
  x.stroke();
  if (values.length < 2) return;
  const max = ceiling || Math.max(...values, 1);
  x.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  x.lineWidth = 2;
  x.beginPath();
  values.forEach((v, i) => {
    const a = (i / (values.length - 1)) * w,
      b = h - 6 - Math.min(v / max, 1) * (h - 12);
    i ? x.lineTo(a, b) : x.moveTo(a, b);
  });
  x.stroke();
}
function cloudStatus(c) {
  const last = c.last_success
    ? " · Last upload: " +
      new Date(c.last_success * 1000).toLocaleString() +
      " (" +
      c.last_file +
      ")"
    : "";
  $("cloud-status").textContent =
    (c.enabled
      ? (c.uploading ? "Enviando" : "Enabled") +
        (c.provider ? " · " + c.provider : "") +
        " · Pending: " +
        c.pending
      : "Uploads disabled") +
    last +
    (c.error ? " · " + c.error + ". Automatically retrying in five minutes." : "");
}
async function loadCloud() {
  const c = await api("/cloud");
  $("cloud-enabled").value = String(c.enabled);
  $("cloud-folder").value = c.folder;
  $("cloud-config").value = "";
  $("cloud-file").value = "";
  cloudStatus(c);
}
function alertStatus(a) {
  const names = {
    backup: "backup",
    cloud: "cloud upload",
    game: "game recovery",
    disk: "disk space",
    backup_age: "local backup overdue",
    cloud_age: "cloud upload overdue",
  };
  $("alerts-status").textContent =
    (a.enabled ? "Notifications enabled." : "Notifications disabled.") +
    (a.configured ? " Webhook saved." : " Webhook not configured.") +
    (a.active.length
      ? " Attention: " + a.active.map((key) => names[key] || key).join(", ") + "."
      : " No active incidents.") +
    (a.error ? " " + a.error : "");
  const states = {
    ok: "Within deadline",
    overdue: "Overdue or unavailable",
    grace: "Waiting for first success within the grace period",
    disabled: "Deadline disabled",
    inactive: "Cloud uploads disabled",
    unknown: "Could not verify",
  };
  const date = (value) =>
    value
      ? new Date(value * 1000).toLocaleString("en-GB", {
          timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
        })
      : "no record";
  const attention = [];
  for (const [name, id, label] of [
    ["local", "local-backup-freshness", "Local backup"],
    ["cloud", "cloud-backup-freshness", "Cloud upload"],
  ]) {
    const status = a.freshness?.[name];
    $(id).textContent = status
      ? label +
        " · Last success: " +
        date(status.last_success) +
        ". " +
        (states[status.state] || "Checking") +
        (status.max_age_hours ? ". Deadline: " + status.max_age_hours + " h" : "") +
        (status.due_at ? ". Due: " + date(status.due_at) : "") +
        (status.error ? ". " + status.error : "")
      : label + " · Waiting for a check…";
    if (["overdue", "unknown"].includes(status?.state)) attention.push(label);
  }
  $("backup-age-warning").hidden = !attention.length;
  $("backup-age-warning").textContent = attention.length
    ? "Backups need attention: " + attention.join(", ") + ". See the Backups tab."
    : "";
}
async function loadAlerts() {
  const a = await api("/alerts");
  $("alerts-enabled").value = String(a.enabled);
  $("alerts-url").value = "";
  $("backup-max-age-hours").value = a.backup_max_age_hours ?? 36;
  $("cloud-max-age-hours").value = a.cloud_max_age_hours ?? 36;
  alertStatus(a);
}
function modDiagnostics(d) {
  const states = {
    disabled: "BepInEx disabled for this session.",
    awaiting_start: "Waiting for the game to start with BepInEx.",
    stopped: "Game stopped. Showing the last supervisor session.",
    complete: "BepInEx loader completed; check the warnings and errors below.",
    loading: "BepInEx detected; loading in progress.",
    unconfirmed: "BepInEx enabled, but not yet confirmed in logs.",
  };
  $("mod-session-status").textContent = states[d.state] || "Unknown state.";
  $("mod-session-version").textContent =
    "Detected version: " +
    (d.version || "not identified in logs") +
    (d.started
      ? " · Session started: " +
        new Date(d.started * 1000).toLocaleString("en-GB", {
          timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
        })
      : "");
  $("mod-session-pending").textContent =
    (d.pending_config ? "BepInEx activation changes apply at the next game start. " : "") +
    (d.scan_error || "");
  const plugins = $("mod-session-plugins");
  plugins.replaceChildren();
  for (const plugin of d.plugins) {
    const item = document.createElement("li");
    item.textContent =
      plugin.name +
      " · " +
      plugin.version +
      " · " +
      (plugin.state === "failed"
        ? "Failed or skipped loading"
        : d.complete
          ? "Load attempt recorded; loader finished"
          : "Loading started");
    plugins.append(item);
  }
  if (!d.plugins.length) {
    const item = document.createElement("li");
    item.textContent = "No plugins identified in this session log.";
    plugins.append(item);
  }
  const issues = $("mod-session-issues");
  issues.replaceChildren();
  for (const issue of d.issues) {
    const item = document.createElement("li");
    item.textContent =
      (issue.level === "warning" ? "Warning" : "Error") +
      " · " +
      issue.source +
      ": " +
      issue.message;
    issues.append(item);
  }
  if (!d.issues.length) {
    const item = document.createElement("li");
    item.textContent = "No warnings or errors captured in diagnostics.";
    issues.append(item);
  }
}
function update(s) {
  lastPanelState = s;
  if (isAdmin()) renderStartup(s.startup);
  syncDeletedPluginControls();
  if (s.server_name) $("server-name").textContent = s.server_name;
  if (isAdmin() && s.audit_error) $("audit-warning").textContent = s.audit_error;
  if (s.run_intent) {
    $("run-intent").textContent =
      (s.run_intent.running
        ? "After container restart: start the game automatically."
        : "After container restart: keep the game stopped.") +
      (s.run_intent.error ? " " + s.run_intent.error : "");
  }
  if (s.mods) modDiagnostics(s.mods);
  if (s.alerts) alertStatus(s.alerts);
  if (s.cloud) cloudStatus(s.cloud);
  $("crossplay-info").hidden = !s.crossplay;
  $("join-code").textContent =
    s.join_code ||
    (s.running ? "Waiting for the server code…" : "Available when the server starts");
  $("status").textContent =
    "State: " + s.state + (s.recovery_error ? " — " + s.recovery_error : "");
  $("panel-health").textContent = "Panel: connected to supervisor";
  const diagnostic = s.diagnostics;
  const phases = {
    ready: "Ready for connections",
    starting: "Starting",
    stopping: "Stopping",
    stopped: "Stopped",
    recovering: "Recovering",
    failed: "Failed",
  };
  if (diagnostic) {
    $("status").textContent = "Game: " + (phases[diagnostic.phase] || s.state);
    $("game-detail").textContent =
      diagnostic.detail +
      (diagnostic.retry_in_seconds === null
        ? ""
        : " Next attempt in " + diagnostic.retry_in_seconds + " s.");
    const failure = diagnostic.last_failure;
    $("last-failure").hidden = !failure;
    $("last-failure").textContent = failure
      ? "Last failure (" +
        failure.source +
        ") · " +
        new Date(failure.time * 1000).toLocaleString("en-GB", {
          timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
        }) +
        ": " +
        failure.message
      : "";
  }
  $("players").textContent = s.players.count === null ? "?" : s.players.count;
  const presence = s.players;
  const presenceDetails = [presence.reason];
  if (presence.source === "game_log") {
    presenceDetails.push("Source: game log (Connections)");
    if (typeof presence.updated_at === "number") {
      presenceDetails.push(
        "Observed at " +
          new Date(presence.updated_at * 1000).toLocaleString("en-GB", {
            timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
            hourCycle: "h23",
          }),
      );
    }
    if (typeof presence.age_seconds === "number")
      presenceDetails.push("Age: " + presence.age_seconds + "s");
    if (presence.count === null && typeof presence.last_count === "number")
      presenceDetails.push("Last count: " + presence.last_count + " (not currently confirmed)");
    if (presence.count === 0 && typeof presence.idle_wait_seconds === "number")
      presenceDetails.push(
        presence.idle_wait_seconds > 0
          ? "Zero in log: waiting " + presence.idle_wait_seconds + "s before automatic maintenance"
          : "Zero in log for at least 2 min; other checks still apply",
      );
  }
  if (!s.crossplay && presence.steam?.updated_at) {
    presenceDetails.push(
      "Last maintenance Steam query: " +
        (presence.steam.count === null ? "unavailable" : presence.steam.count + " player(s)") +
        " · age " +
        presence.steam.age_seconds +
        "s",
    );
  }
  $("player-reason").textContent = presenceDetails.join(" · ");
  $("versions").textContent =
    "Installed build: " +
    (s.installed_build || "—") +
    " · Available: " +
    (s.available_build || "—");
  if (s.image_version) renderImageVersion(s.image_version);
  $("pending").textContent = s.pending_config ? "Changes require a restart" : "";
  $("logs").textContent = s.logs
    .map((l) => new Date(l.time * 1000).toLocaleTimeString() + "  " + l.message)
    .join("\n");
  maintenanceJobs = s.jobs;
  $("history-warning").textContent = s.maintenance_history_error || "";
  renderMaintenanceHistory();
  if (s.metric && (!samples.length || samples.at(-1).time !== s.metric.time))
    samples.push(s.metric);
  samples = samples.slice(-720);
  const m = samples.at(-1);
  if (!m) return;
  $("cpu").textContent = m.cpu_percent.toFixed(1) + "%";
  $("memory").textContent = bytes(m.memory_bytes) + " / " + bytes(m.memory_limit);
  $("network").textContent = bytes(m.network_in_bps) + " / " + bytes(m.network_out_bps) + "/s";
  $("disk").textContent = bytes(m.disk_used) + " / " + bytes(m.disk_total);
  $("metric-scope").textContent =
    "Measurement: " +
    m.scope +
    ". Disk: volume filesystem. History: up to one hour; refreshed every 5 seconds.";
  chart(
    "cpu-chart",
    samples.map((x) => x.cpu_percent),
    Math.max(200, ...samples.map((x) => x.cpu_percent)),
  );
  chart(
    "memory-chart",
    samples.map((x) => x.memory_bytes),
    m.memory_limit,
  );
  chart(
    "network-chart",
    samples.map((x) => x.network_in_bps + x.network_out_bps),
  );
  chart(
    "disk-chart",
    samples.map((x) => x.disk_used),
    m.disk_total,
  );
}
function renderStartup(startup) {
  const history = startup?.history || [];
  const outcomes = {
    starting: "Starting",
    ready: "Online",
    stopped: "Stop requested",
    fatal: "Fatal signal",
    crashed: "Queda",
    exited: "Encerrado",
    spawn_failed: "Process creation failed",
    interrupted: "Supervisor interrupted",
  };
  const phases = {
    before_scene: "before first scene",
    unknown: "unknown phase",
    scene: "first scene",
    world: "world",
    ready: "ready",
  };
  $("startup-summary").textContent =
    startup?.error ||
    (history.length
      ? "Last attempt profile: " +
        (history[0].profile === "compatible" ? "compatibility" : "default")
      : "No startup recorded by this version.");
  $("startup-history").replaceChildren();
  for (const item of history) {
    const row = document.createElement("li");
    row.textContent = [
      new Date(item.started * 1000).toLocaleString("en-GB", {
        timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
      }),
      outcomes[item.outcome] || item.outcome,
      item.profile === "compatible" ? "compatibility" : "default",
      phases[item.phase] || item.phase,
      typeof item.signal === "number" ? "signal " + item.signal : "",
      typeof item.exit_code === "number" ? "exit " + item.exit_code : "",
      item.signature === "platform_initializer" ? "PlatformInitializer" : "",
    ]
      .filter(Boolean)
      .join(" · ");
    $("startup-history").append(row);
  }
}
function configGroup(key) {
  if (key.startsWith("bepinex")) return "mods";
  if (
    [
      "server_port",
      "save_interval",
      "native_backups",
      "timezone",
      "restart_at",
      "backup_at",
      "auto_update",
      "retain_daily",
      "retain_weekly",
    ].includes(key)
  )
    return "server";
  return "game";
}
const settingsContainers = { game: "settings", server: "server-settings", mods: "mods-settings" };
async function loadConfig(savedGroup = null) {
  const drafts = {};
  if (savedGroup && currentConfig) {
    for (const key of Object.keys(currentConfig.values)) {
      const input = $("setting-" + key);
      if (configGroup(key) !== savedGroup && input) drafts[key] = input.value;
    }
  }
  currentConfig = await api("/config");
  Object.values(settingsContainers).forEach((id) => $(id).replaceChildren());
  $("server-name").textContent = currentConfig.values.server_name;
  for (const [key, value] of Object.entries(currentConfig.values)) {
    if (["bepinex_url", "bepinex_sha256"].includes(key)) continue;
    const container = $(settingsContainers[configGroup(key)]);
    const heading = {
      server_name: "World and access",
      preset: "Game rules",
      save_interval: "Saving",
      timezone: "Automatic maintenance",
      bepinex: "Mods",
    }[key];
    if (heading) {
      const title = document.createElement("h3");
      title.className = "settings-heading";
      title.textContent = heading;
      container.append(title);
    }
    const row = document.createElement("div");
    row.className = "setting";
    const label = document.createElement("label");
    label.textContent = labels[key] || key;
    let input;
    const options = key.startsWith("modifier_")
      ? currentConfig.modifiers[key.slice(9)]
      : key === "preset"
        ? ["", "normal", "casual", "easy", "hard", "hardcore", "immersive", "hammer"]
        : typeof value === "boolean"
          ? ["true", "false"]
          : null;
    if (options) {
      input = document.createElement("select");
      options.forEach((v) => {
        const o = document.createElement("option");
        o.value = v;
        o.textContent =
          v === "" ? "World/preset default" : v === "true" ? "Yes" : v === "false" ? "No" : v;
        input.append(o);
      });
      input.value = String(value);
    } else {
      input = document.createElement("input");
      input.type =
        key === "server_password" ? "password" : typeof value === "number" ? "number" : "text";
      input.value = value;
      if (key === "server_password") input.autocomplete = "new-password";
    }
    input.id = "setting-" + key;
    input.disabled = key === "server_port";
    label.append(input);
    row.append(label);
    const source = document.createElement("small");
    source.textContent = "Source: " + currentConfig.sources[key];
    row.append(source);
    if (currentConfig.sources[key] === "panel" && key !== "server_port") {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "reset";
      b.textContent = "Use Compose";
      b.onclick = () =>
        guard(async () => {
          const world = key === "world_name";
          if (world && !confirm("Use the Compose world? The current save will be preserved."))
            return;
          await api("/config", "PATCH", { reset: [key], confirm_world: world });
          await loadConfig(configGroup(key));
          notice("Override removed. Restart safely to apply game changes.");
        });
      row.append(b);
    }
    container.append(row);
    if (key in drafts) input.value = drafts[key];
  }
}
function draftPlayers() {
  return [
    ...new Set(
      $("user-entries")
        .value.split("\n")
        .map((id) => id.trim())
        .filter((id) => id && !id.startsWith("//")),
    ),
  ];
}
let knownPlayerRecords = [];
function renderPlayers() {
  const entries = draftPlayers();
  $("user-list-count").textContent = entries.length + " player(s) in list";
  $("user-list").replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No players in this list.";
    $("user-list").append(empty);
  }
  entries.forEach((id) => {
    const row = document.createElement("div");
    row.className = "player-entry";
    const label = document.createElement("code");
    const player = knownPlayerRecords.find((item) => item.id === id);
    label.textContent = player?.name
      ? [player.name, player.platform, id].filter(Boolean).join(" · ")
      : id;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", "Remove " + id);
    remove.onclick = () => {
      $("user-entries").value = draftPlayers()
        .filter((value) => value !== id)
        .join("\n");
      renderPlayers();
      notice("Player removed from selection. Save the list to apply.");
    };
    row.append(label, remove);
    $("user-list").append(row);
  });
}
function knownPlayers(players) {
  knownPlayerRecords = players;
  const selected = $("known-player").value;
  $("known-player").replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = players.length ? "Select a player" : "No IDs identified yet";
  $("known-player").append(placeholder);
  players.forEach((player) => {
    const option = document.createElement("option");
    option.value = player.id;
    option.textContent = [
      player.name,
      player.platform,
      player.id,
      player.confirmed === false ? "Observed ID: confirm in F2" : "",
    ]
      .filter(Boolean)
      .join(" · ");
    option.dataset.confirmed = String(player.confirmed !== false);
    option.dataset.playerName = player.name || "";
    option.dataset.playerPlatform = player.platform || "";
    $("known-player").append(option);
  });
  $("known-player").value = selected;
}
async function loadUsers() {
  const kind = $("user-kind").value;
  const d = await api("/users/" + kind);
  if (kind !== $("user-kind").value) return;
  $("user-entries").value = d.entries.join("\n");
  $("player-id").value = "";
  knownPlayers(d.known_players || []);
  $("known-player").value = "";
  renderPlayers();
}
async function loadBackups() {
  const list = await api("/backups");
  $("backup-list").replaceChildren();
  if (!list.length) {
    $("backup-list").textContent = "No backups created yet.";
    return;
  }
  list.forEach((b) => {
    const row = document.createElement("div");
    row.className = "backup";
    const a = document.createElement("a");
    a.href = "/api/v1/backups/" + encodeURIComponent(b.name);
    a.textContent = b.name + " · " + bytes(b.bytes);
    row.append(a);
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Restore";
    button.onclick = () =>
      guard(async () => {
        if (
          !confirm(
            "Restore " +
              b.name +
              "? Stop the game first. A backup of the current state will be created.",
          )
        )
          return;
        await api("/actions", "POST", { action: "restore", backup: b.name, confirm: true });
        notice("Restore requested. Follow progress in Activity.");
      });
    row.append(button);
    $("backup-list").append(row);
  });
}
async function signedIn() {
  $("login").hidden = true;
  $("logout").hidden = false;
  $("identity").textContent =
    panelUser.username + " · " + (isAdmin() ? "Administrator" : "Operator");
  $("own-password").hidden = false;
  if (panelUser.must_change_password) {
    $("dashboard").hidden = true;
    $("password-change").hidden = false;
    $("close-password-change").hidden = true;
    $("password-change-message").textContent =
      "Change your temporary password to unlock the panel.";
    return;
  }
  $("password-change").hidden = true;
  $("dashboard").hidden = false;
  document.querySelectorAll("[data-admin-only]").forEach((element) => {
    if (element.getAttribute("role") !== "tabpanel") element.hidden = !isAdmin();
    else if (!isAdmin()) element.hidden = true;
  });
  refreshMobileNavigation();
  selectTab($("tab-overview"));
  $("history-title").textContent = isAdmin() ? "Maintenance history" : "Your operations";
  $("action-policy").textContent = isAdmin()
    ? "Administrator actions request shutdown immediately and may disconnect players. The game saves before exiting."
    : "Start is immediate. Stop and restart wait for the game to be ready and zero players to be confirmed for two minutes; Steam mode also requires a Steam query. Track requests in Activity.";
  if (isAdmin()) {
    await Promise.all([
      loadConfig(),
      loadImageUpdateStatus(),
      loadUsers(),
      loadBackups(),
      loadCloud(),
      loadAlerts().catch((error) => notice("Could not load alerts: " + error.message)),
      loadModConfigs().catch((error) => notice("Could not load plugin settings: " + error.message)),
      loadPlugins().catch((error) => notice("Could not load the plugin list: " + error.message)),
      loadAccounts(),
      loadAudit(),
    ]);
  }
  const state = await api("/state");
  if (!isAdmin()) currentConfig = { values: { timezone: state.timezone || "America/Sao_Paulo" } };
  samples = state.metrics;
  update(state);
  if (events) events.close();
  events = new EventSource("/api/v1/events");
  events.onmessage = (e) => update(JSON.parse(e.data));
  clearInterval(imageUpdateTimer);
  if (isAdmin()) imageUpdateTimer = setInterval(loadImageUpdateStatus, 3000);
  events.onerror = () => {
    api("/session").catch(() => {});
    $("panel-health").textContent = "Panel: connection interrupted; reconnecting…";
    $("status").textContent = "Game: status not updated";
    notice("Connection interrupted; reconnecting to the supervisor…");
  };
}
async function guard(fn) {
  try {
    await fn();
  } catch (e) {
    notice(e.message);
  }
}
$("login-form").onsubmit = (e) => {
  e.preventDefault();
  guard(async () => {
    const r = await api("/login", "POST", {
      username: $("username").value,
      password: $("password").value,
    });
    $("password").value = "";
    acceptSession(r);
    notice("");
    await signedIn();
  });
};
$("logout").onclick = () =>
  guard(async () => {
    await api("/logout", "POST", {});
    signedOut();
  });
const actionConfirmations = {
  "image-update": {
    title: "Update server image?",
    label: "Update image",
    description:
      "The host will download and check the new image, create a backup, and switch containers. The panel may disconnect and require signing in again. If verification fails after switching, it will attempt to restore the previous image.",
  },
  stop: {
    title: "Stop the server?",
    label: "Stop server",
    description: "The game will stop and remain stopped until a new start request.",
  },
  restart: {
    title: "Restart the server?",
    label: "Restart server",
    description: "The game will stop and start again, applying pending settings.",
  },
  update: {
    title: "Update the game?",
    label: "Update game",
    description:
      "The supervisor will check and install game updates. This may require stopping and starting the server.",
  },
  backup: {
    title: "Criar backup agora?",
    label: "Criar backup",
    description:
      "To create a consistent world copy, the game will stop. If it was running, it will restart after the backup.",
  },
};
function confirmServerAction(action) {
  const info = actionConfirmations[action];
  if (!info) return Promise.resolve(true);
  const dialog = $("action-confirmation");
  $("action-confirm-title").textContent = info.title;
  $("action-confirm-description").textContent =
    info.description +
    (!isAdmin() && ["stop", "restart"].includes(action)
      ? " Your request will wait for zero players to be confirmed before stopping the game."
      : "");
  $("action-confirm-submit").textContent = info.label;
  dialog.returnValue = "cancel";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {
      once: true,
    });
    dialog.showModal();
  });
}
let actionPending = false;
$("actions").onclick = (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button || actionPending) return;
  const action = button.dataset.action;
  actionPending = true;
  guard(async () => {
    try {
      if (!(await confirmServerAction(action))) return;
      document.querySelectorAll("#actions button").forEach((item) => (item.disabled = true));
      await api("/actions", "POST", { action });
      notice("Operation requested. Track the result in Activity.");
    } finally {
      actionPending = false;
      document.querySelectorAll("#actions button").forEach((item) => (item.disabled = false));
    }
  });
};
for (const [formId, group] of [
  ["config-form", "game"],
  ["server-config-form", "server"],
  ["mods-config-form", "mods"],
]) {
  $(formId).onsubmit = (e) => {
    e.preventDefault();
    guard(async () => {
      const changes = {};
      for (const [key, old] of Object.entries(currentConfig.values)) {
        if (key === "server_port" || configGroup(key) !== group) continue;
        const input = $("setting-" + key);
        if (!input) continue;
        const value = input.value;
        if (key === "server_password" && !value) continue;
        const parsed =
          typeof old === "boolean"
            ? value === "true"
            : typeof old === "number"
              ? Number(value)
              : value;
        if (parsed !== old) changes[key] = parsed;
      }
      const world = "world_name" in changes;
      if (
        world &&
        !confirm(
          "Select/create world “" + changes.world_name + "”? The previous world will be preserved.",
        )
      )
        return;
      await api("/config", "PATCH", { changes, confirm_world: world });
      await loadConfig(group);
      notice("Settings saved. Game changes apply at the next startup.");
    });
  };
}
$("user-kind").onchange = () => guard(loadUsers);
$("users-form").onsubmit = (e) => {
  e.preventDefault();
  guard(async () => {
    const kind = $("user-kind").value;
    if (
      kind === "permitted" &&
      $("user-entries").value.trim() &&
      !confirm("Restrict access to permitted IDs only?")
    )
      return;
    const saved = await api("/users/" + kind, "PUT", { entries: draftPlayers() });
    if (kind === $("user-kind").value) {
      $("user-entries").value = saved.entries.join("\n");
      renderPlayers();
    }
    notice("List saved. The game reloads these lists; restart safely if needed.");
  });
};
$("refresh-backups").onclick = () => guard(loadBackups);
guard(async () => {
  try {
    acceptSession(await api("/session"));
    await signedIn();
  } catch (e) {
    signedOut();
  }
});

$("cloud-form").onsubmit = (e) => {
  e.preventDefault();
  guard(async () => {
    await api("/cloud", "PUT", {
      enabled: $("cloud-enabled").value === "true",
      folder: $("cloud-folder").value,
      config: $("cloud-config").value,
    });
    await loadCloud();
    notice("Cloud upload settings saved. New backups will be uploaded automatically when enabled.");
  });
};

$("cloud-file").onchange = () =>
  guard(async () => {
    const f = $("cloud-file").files[0];
    if (!f) return;
    if (f.size > 65536) throw new Error("Connection file too large (maximum 64 KiB)");
    $("cloud-config").value = await f.text();
  });

const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(tab, focus = false) {
  if (tab.hidden) return;
  $("dashboard").dataset.section = tab.id;
  if (tab.id === "tab-administration" && isAdmin()) guard(loadAudit);
  if (tab.id === "tab-server" && isAdmin())
    guard(async () => renderImageVersion(await api("/image-version")));
  tabs.forEach((item) => {
    const selected = item === tab && !item.hidden;
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
    $(item.getAttribute("aria-controls")).hidden = !selected;
  });
  $("mobile-section").value = tab.id;
  if (focus) tab.focus();
  if (tab.id === "tab-overview" && lastPanelState)
    requestAnimationFrame(() => lastPanelState && update(lastPanelState));
}
function refreshMobileNavigation() {
  $("mobile-section").replaceChildren();
  for (const tab of tabs.filter((item) => !item.hidden)) {
    const option = document.createElement("option");
    option.value = tab.id;
    option.textContent = tab.textContent.trim();
    $("mobile-section").append(option);
  }
}
$("mobile-section").onchange = () => {
  const tab = $($("mobile-section").value);
  if (!tab || tab.hidden) return;
  selectTab(tab);
  const target =
    tab.id === "tab-overview"
      ? document.querySelector(".overview")
      : $(tab.getAttribute("aria-controls"));
  target.scrollIntoView({ block: "start" });
};
let phoneLayout = null;
function syncResponsiveLayout() {
  const mobile = window.matchMedia(
    "(max-width: 760px), (max-width: 960px) and (max-height: 500px)",
  ).matches;
  if (mobile !== phoneLayout) $("server-details").open = !mobile;
  phoneLayout = mobile;
}
syncResponsiveLayout();
let resizeFrame = null;
window.addEventListener("resize", () => {
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => {
    syncResponsiveLayout();
    if (lastPanelState) update(lastPanelState);
  });
});
tabs.forEach((tab) => {
  tab.onclick = () => selectTab(tab);
  tab.onkeydown = (event) => {
    const visibleTabs = tabs.filter((item) => !item.hidden);
    const index = visibleTabs.indexOf(tab);
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % visibleTabs.length;
    else if (event.key === "ArrowLeft")
      next = (index + visibleTabs.length - 1) % visibleTabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = visibleTabs.length - 1;
    else return;
    event.preventDefault();
    selectTab(visibleTabs[next], true);
  };
});

$("known-player").onchange = () => {
  const option = $("known-player").selectedOptions[0];
  if (!option?.value) return;
  $("directory-id").value = option.value;
  $("directory-name").value = option.dataset.playerName || "";
  $("directory-platform").value = option.dataset.playerPlatform || "";
  $("directory-confirm").checked = false;
  if (option.dataset.confirmed === "false") {
    $("player-id").value = "";
    $("player-identity-details").open = true;
    notice("ID observed in crossplay transport. Check and correct the F2 ID before confirming.");
  } else $("player-id").value = option.value;
};
$("player-identity-form").onsubmit = (event) => {
  event.preventDefault();
  guard(async () => {
    const result = await api("/player-directory", "PUT", {
      id: $("directory-id").value.trim(),
      name: $("directory-name").value.trim(),
      platform: $("directory-platform").value.trim(),
      confirm: $("directory-confirm").checked,
    });
    const data = await api("/users/" + $("user-kind").value);
    knownPlayers(data.known_players || []);
    $("known-player").value = result.id;
    $("player-id").value = result.id;
    $("directory-confirm").checked = false;
    $("player-identity-details").open = false;
    renderPlayers();
    notice("Identity saved. Add the player to the desired list and save to apply permissions.");
  });
};
$("refresh-players").onclick = () =>
  guard(async () => {
    const data = await api("/users/" + $("user-kind").value);
    knownPlayers(data.known_players || []);
  });
$("user-entries").oninput = renderPlayers;
$("player-add-form").onsubmit = (event) => {
  event.preventDefault();
  guard(() => {
    let id = $("player-id").value.trim();
    if (/^\d{17}$/.test(id)) id = "Steam_" + id;
    if (!/^(?:[A-Za-z]+_)[A-Za-z0-9:_-]{1,128}$/.test(id))
      throw new Error(
        "Paste a complete platform ID, such as Steam_ followed by SteamID64, or the 17-digit SteamID64.",
      );
    const identity = (value) => (/^\d{17}$/.test(value) ? "Steam_" + value : value);
    const entries = draftPlayers();
    if (entries.some((value) => identity(value) === id)) {
      notice("This player is already in the selected list.");
      return;
    }
    if (entries.length >= 1000) throw new Error("The list supports up to 1000 players.");
    $("user-entries").value = [...entries, id].join("\n");
    $("player-id").value = "";
    $("known-player").value = "";
    renderPlayers();
    notice("Player added to selection. Save the list to apply.");
  });
};

let modConfig = null;
async function loadModConfigs() {
  const data = await api("/mod-configs");
  $("mod-config-file").replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = data.files.length ? "Select a file" : "No configuration files found";
  $("mod-config-file").append(empty);
  data.files.forEach((path) => {
    const option = document.createElement("option");
    option.value = path;
    option.textContent = path;
    $("mod-config-file").append(option);
  });
  modConfig = null;
  $("mod-config-content").value = "";
  $("mod-config-content").disabled = true;
  $("save-mod-config").disabled = true;
  $("previous-mod-config").disabled = true;
}
function showModConfig(data) {
  modConfig = data;
  $("mod-config-content").value = data.content;
  $("mod-config-content").disabled = false;
  $("save-mod-config").disabled = false;
  $("previous-mod-config").disabled = !data.previous_available;
}
function discardModChanges() {
  return (
    !modConfig ||
    (!modConfig.previousLoaded && $("mod-config-content").value === modConfig.content) ||
    confirm("Discard unsaved changes to this file?")
  );
}
$("refresh-mod-configs").onclick = () => {
  if (discardModChanges()) guard(loadModConfigs);
};
$("mod-config-file").onchange = () =>
  guard(async () => {
    if (!discardModChanges()) {
      $("mod-config-file").value = modConfig.path;
      return;
    }
    const path = $("mod-config-file").value;
    if (!path) {
      modConfig = null;
      $("mod-config-content").value = "";
      $("mod-config-content").disabled = true;
      $("save-mod-config").disabled = true;
      $("previous-mod-config").disabled = true;
      return;
    }
    $("mod-config-content").disabled = true;
    $("save-mod-config").disabled = true;
    $("previous-mod-config").disabled = true;
    const data = await api("/mod-config?path=" + encodeURIComponent(path));
    if ($("mod-config-file").value === path) showModConfig(data);
  });
$("mod-config-form").onsubmit = (event) => {
  event.preventDefault();
  guard(async () => {
    if (!modConfig || $("mod-config-file").value !== modConfig.path) return;
    const saved = await api("/mod-config", "PUT", {
      path: modConfig.path,
      content: $("mod-config-content").value,
      revision: modConfig.revision,
    });
    if ($("mod-config-file").value === saved.path) showModConfig(saved);
    notice("File saved. Start the game to apply the plugin settings.");
  });
};
$("previous-mod-config").onclick = () =>
  guard(async () => {
    if (!modConfig || $("mod-config-file").value !== modConfig.path) return;
    const path = modConfig.path;
    const data = await api("/mod-config?previous=true&path=" + encodeURIComponent(path));
    if ($("mod-config-file").value === path) {
      showModConfig(data);
      modConfig.previousLoaded = true;
      notice("Previous version loaded in the editor. Click Save file to restore it.");
    }
  });

async function loadPlugins() {
  const data = await api("/plugins");
  if (data.diagnostics) modDiagnostics(data.diagnostics);
  $("plugin-list").replaceChildren();
  if (!data.files.length) {
    $("plugin-list").textContent = "No DLLs installed yet.";
  }
  data.files.forEach((file) => {
    const row = document.createElement("div");
    row.className = "plugin-entry";
    const label = document.createElement("span");
    const sessionStates = {
      awaiting_start: "Waiting for game startup",
      pending_restart: "Changed since last startup; restart required",
      present_at_start: "Present at startup; check loading status above",
      last_session: "Present in the last session",
      unknown: "Session comparison unavailable",
    };
    label.textContent =
      file.name +
      " · " +
      bytes(file.bytes) +
      " · " +
      (sessionStates[file.session_state] || "Loading not confirmed");
    row.append(label);
    if (file.previous_available && file.revision) {
      const restore = document.createElement("button");
      restore.type = "button";
      restore.textContent = "Restore previous";
      restore.setAttribute("aria-label", "Restore previous version of " + file.name);
      restore.onclick = () =>
        guard(async () => {
          if (!confirm("Restore the previous DLL for " + file.name + "? The game must be stopped."))
            return;
          await api("/plugins/restore", "POST", { name: file.name, revision: file.revision });
          await loadPlugins();
          notice("Previous DLL restored. Start the game to load it.");
        });
      row.append(restore);
    }
    if (file.revision) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.pluginWrite = "true";
      remove.disabled = !pluginsEditable();
      remove.textContent = "Delete";
      remove.setAttribute("aria-label", "Delete plugin " + file.name);
      remove.onclick = () =>
        guard(async () => {
          if (!(await confirmPluginChange("delete", file.name))) return;
          pluginMutationPending = true;
          syncDeletedPluginControls();
          try {
            await api("/plugins/delete", "POST", {
              name: file.name,
              revision: file.revision,
              confirm: true,
            });
            await loadPlugins();
            notice(
              "Plugin deleted. Settings preserved; the recovery copy is listed under Deleted plugins.",
            );
          } finally {
            pluginMutationPending = false;
            syncDeletedPluginControls();
          }
        });
      row.append(remove);
    }
    $("plugin-list").append(row);
  });
  $("deleted-plugin-list").replaceChildren();
  for (const file of data.deleted || []) {
    const row = document.createElement("div");
    row.className = "plugin-entry";
    const label = document.createElement("span");
    label.textContent =
      file.name +
      " · Deleted at " +
      new Date(file.deleted_at * 1000).toLocaleString("en-GB", {
        timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
      });
    const restore = document.createElement("button");
    restore.type = "button";
    restore.textContent = "Restore deleted";
    restore.dataset.pluginWrite = "true";
    restore.dataset.restoreConflict = String(
      data.files.some(
        (active) => active.name.toLocaleLowerCase() === file.name.toLocaleLowerCase(),
      ),
    );
    restore.disabled = !pluginsEditable() || restore.dataset.restoreConflict === "true";
    restore.setAttribute("aria-label", "Restore deleted plugin " + file.name);
    restore.onclick = () =>
      guard(async () => {
        if (!(await confirmPluginChange("restore", file.name))) return;
        pluginMutationPending = true;
        syncDeletedPluginControls();
        try {
          await api("/plugins/restore-deleted", "POST", {
            id: file.id,
            revision: file.revision,
            confirm: true,
          });
          await loadPlugins();
          notice("Plugin restored. Start the game to load it.");
        } finally {
          pluginMutationPending = false;
          syncDeletedPluginControls();
        }
      });
    row.append(label, restore);
    $("deleted-plugin-list").append(row);
  }
  if (!(data.deleted || []).length)
    $("deleted-plugin-list").textContent = "No deleted plugins available to restore.";
  syncDeletedPluginControls();
  return data.files;
}
$("refresh-plugins").onclick = () => guard(loadPlugins);
$("plugin-upload-form").onsubmit = (event) => {
  event.preventDefault();
  guard(async () => {
    const file = $("plugin-file").files[0];
    if (!file) return;
    if (
      !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.dll$/i.test(file.name) ||
      file.size === 0 ||
      file.size > 16 * 1024 * 1024
    )
      throw new Error("Select a DLL up to 16 MiB with a simple filename and no directories.");
    $("upload-plugin").disabled = true;
    try {
      const files = await loadPlugins();
      const existing = files.find((item) => item.name === file.name);
      if (existing && !existing.revision)
        throw new Error("The existing DLL exceeds 16 MiB; manage it through SSH.");
      if (
        existing &&
        !confirm("Replace " + file.name + "? The previous version will be preserved.")
      )
        return;
      $("plugin-upload-status").textContent = "Enviando " + file.name + "…";
      const query = new URLSearchParams({ name: file.name });
      if (existing) query.set("revision", existing.revision);
      const response = await fetch("/api/v1/plugins?" + query, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-CSRF-Token": csrf },
        body: file,
      });
      const result = await response.json();
      if (!response.ok) {
        if (response.status === 401) signedOut();
        throw new Error(
          typeof result.detail === "string" ? result.detail : "Could not upload the DLL.",
        );
      }
      $("plugin-file").value = "";
      $("plugin-upload-status").textContent =
        "Plugin uploaded: " + result.name + ". Start the game to load it.";
      await loadPlugins();
    } catch (error) {
      $("plugin-upload-status").textContent = "Upload did not complete.";
      throw error;
    } finally {
      $("upload-plugin").disabled = false;
    }
  });
};

$("alerts-form").onsubmit = (event) => {
  event.preventDefault();
  guard(async () => {
    const a = await api("/alerts", "PUT", {
      enabled: $("alerts-enabled").value === "true",
      url: $("alerts-url").value,
      backup_max_age_hours: Number($("backup-max-age-hours").value),
      cloud_max_age_hours: Number($("cloud-max-age-hours").value),
    });
    $("alerts-url").value = "";
    alertStatus(a);
    notice("Alerts saved.");
  });
};
$("alerts-test").onclick = () =>
  guard(async () => {
    await api("/alerts/test", "POST", {});
    notice("Test notification delivered to Discord.");
  });

function renderImageUpdate(status) {
  $("update-image").disabled =
    imageUpdateRequesting || !status.available || status.state === "running";
  $("image-update-status").textContent = status.message || "Update status unavailable.";
}
async function loadImageUpdateStatus() {
  try {
    renderImageUpdate(await api("/image-update"));
  } catch (error) {
    $("update-image").disabled = true;
    $("image-update-status").textContent =
      "Panel temporarily unavailable; reconnecting to check the update. You may need to sign in again.";
  }
}
$("update-image").onclick = () =>
  guard(async () => {
    if (imageUpdateRequesting) return;
    imageUpdateRequesting = true;
    try {
      if (!(await confirmServerAction("image-update"))) return;
      $("update-image").disabled = true;
      const request_id = crypto.randomUUID().replaceAll("-", "");
      renderImageUpdate(await api("/image-update", "POST", { confirm: true, request_id }));
      notice(
        "Update requested. Track progress in Server settings; sign in again if prompted after the switch.",
      );
    } finally {
      imageUpdateRequesting = false;
      await loadImageUpdateStatus();
    }
  });

let maintenanceJobs = [];
function renderMaintenanceHistory() {
  const zone = currentConfig?.values.timezone || "America/Sao_Paulo";
  const format = (timestamp) =>
    typeof timestamp === "number" && Number.isFinite(timestamp)
      ? new Date(timestamp * 1000).toLocaleString("en-GB", { timeZone: zone, hourCycle: "h23" })
      : "—";
  const day = (timestamp) =>
    new Intl.DateTimeFormat("en-CA", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date(timestamp * 1000));
  const origins = {
    administrator: "Administrator",
    operator: "Operator",
    schedule: "Agendamento",
    recovery: "Recovery",
  };
  const actions = {
    start: "Start",
    stop: "Stop",
    restart: "Restart",
    backup: "Backup",
    update: "Game update",
    restore: "Restore",
    "check-update": "Update check",
    "recover-startup": "Startup recovery",
  };
  $("jobs").replaceChildren();
  const jobs = [...maintenanceJobs].reverse().filter((job) => {
    const origin = job.origin || (job.scheduled ? "schedule" : "administrator");
    const date = typeof job.created === "number" ? day(job.created) : "";
    return (
      (!$("history-origin").value || origin === $("history-origin").value) &&
      (!$("history-state").value || job.state === $("history-state").value) &&
      (!$("history-from").value || date >= $("history-from").value) &&
      (!$("history-to").value || date <= $("history-to").value)
    );
  });
  for (const job of jobs) {
    const row = document.createElement("div");
    row.className = "job";
    const when = document.createElement("time");
    when.textContent = "Requested at " + format(job.created);
    if (typeof job.created === "number" && Number.isFinite(job.created))
      when.dateTime = new Date(job.created * 1000).toISOString();
    when.title = "Times in " + zone;
    const heading = document.createElement("strong");
    heading.textContent =
      (actions[job.action] || job.action) +
      " · " +
      job.state +
      (isAdmin() && job.actor ? " · " + job.actor.username : "");
    const details = document.createElement("p");
    details.className = "muted";
    const duration = typeof job.duration === "number" ? Math.round(job.duration) + "s" : "—";
    details.textContent =
      origins[job.origin || (job.scheduled ? "schedule" : "administrator")] +
      " · Started: " +
      format(job.started) +
      " · Finished: " +
      format(job.finished) +
      " · Running duration: " +
      duration +
      " · Attempts: " +
      (job.attempts || 0);
    row.append(when, heading, details);
    for (const text of [
      job.error,
      job.result,
      job.backup_created ? "Backup created: " + job.backup_created : null,
      job.backup ? "Restored backup: " + job.backup : null,
    ]) {
      if (text) {
        const line = document.createElement("p");
        line.textContent = text;
        row.append(line);
      }
    }
    $("jobs").append(row);
  }
  if (!jobs.length)
    $("jobs").textContent = "No operations in this period match the selected filters.";
}
for (const id of ["history-origin", "history-state", "history-from", "history-to"]) {
  $(id).addEventListener("change", renderMaintenanceHistory);
}

$("own-password").onclick = () => {
  $("password-change").hidden = false;
  $("close-password-change").hidden = panelUser.must_change_password;
  $("password-change-message").textContent = panelUser.must_change_password
    ? "Change your temporary password to unlock the panel."
    : "Changing your password ends your sessions. Sign in again after saving.";
};
$("close-password-change").onclick = () => {
  $("password-change").hidden = true;
  $("password-change-form").reset();
};
$("password-change-form").onsubmit = (event) => {
  event.preventDefault();
  guard(async () => {
    if ($("new-password").value !== $("repeat-password").value)
      throw new Error("The new passwords do not match");
    await api("/password", "POST", {
      current_password: $("current-password").value,
      password: $("new-password").value,
    });
    signedOut();
    notice("Password changed. Sign in again with your new password.");
  });
};
async function loadAccounts() {
  const result = await api("/accounts");
  $("account-list").replaceChildren();
  $("account-reset-user").replaceChildren();
  for (const user of result.users) {
    const row = document.createElement("div");
    row.className = "account-row";
    const name = document.createElement("strong");
    name.textContent = user.username;
    const role = document.createElement("select");
    role.setAttribute("aria-label", "Role for " + user.username);
    for (const [value, label] of [
      ["operator", "Operator"],
      ["admin", "Administrator"],
    ]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      role.append(option);
    }
    role.value = user.role;
    const label = document.createElement("label");
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.setAttribute("aria-label", "Active account for " + user.username);
    enabled.checked = user.enabled;
    label.append(enabled, document.createTextNode(" Active account"));
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save " + user.username;
    role.disabled = enabled.disabled = save.disabled = user.id === "admin";
    save.onclick = () =>
      guard(async () => {
        await api("/accounts/" + encodeURIComponent(user.id), "PATCH", {
          role: role.value,
          enabled: enabled.checked,
        });
        await loadAccounts();
        await loadAudit();
        notice("Account updated; sessions and pending requests invalidated.");
      });
    const info = document.createElement("small");
    info.textContent = user.must_change_password
      ? "Password change required"
      : "Personal password set";
    row.append(name, role, label, save, info);
    $("account-list").append(row);
    const option = document.createElement("option");
    option.value = user.id;
    option.textContent = user.username;
    $("account-reset-user").append(option);
  }
}
$("account-create-form").onsubmit = (event) => {
  event.preventDefault();
  guard(async () => {
    try {
      await api("/accounts", "POST", {
        username: $("account-username").value,
        role: $("account-role").value,
        password: $("account-password").value,
      });
      $("account-create-form").reset();
      await loadAccounts();
      await loadAudit();
      notice("User created. They must change the temporary password at first sign-in.");
    } finally {
      $("account-password").value = "";
    }
  });
};
$("account-reset-form").onsubmit = (event) => {
  event.preventDefault();
  guard(async () => {
    try {
      await api(
        "/accounts/" + encodeURIComponent($("account-reset-user").value) + "/password",
        "POST",
        { password: $("account-reset-password").value },
      );
      await loadAccounts();
      await loadAudit();
      notice("Password reset. Sessions ended; password change required at next sign-in.");
    } finally {
      $("account-reset-password").value = "";
    }
  });
};
async function loadAudit() {
  const query = new URLSearchParams();
  for (const [field, id] of [
    ["user", "audit-user"],
    ["operation", "audit-operation"],
    ["since", "audit-from"],
    ["until", "audit-to"],
  ])
    if ($(id).value) query.set(field, $(id).value);
  const data = await api("/audit?" + query);
  $("audit-warning").textContent = data.error || "";
  $("audit-list").replaceChildren();
  const labels = {
    configuration: "Configuration",
    "server-action": "Operation request",
    start: "Start",
    stop: "Stop",
    restart: "Restart",
    backup: "Backup",
    restore: "Restore",
    update: "Update game",
    "image-update": "Update image",
    "password-change": "Change password",
    accounts: "Accounts",
    plugins: "Plugins",
    "plugin-config": "Plugin configuration",
    "player-lists": "Player lists",
    alerts: "Alerts",
    cloud: "Cloud",
    "backup-download": "Backup download",
    login: "Sign in",
    logout: "Sign out",
  };
  for (const entry of data.events) {
    const row = document.createElement("div");
    row.className = "job";
    const title = document.createElement("strong");
    title.textContent = (labels[entry.operation] || entry.operation) + " · " + entry.outcome;
    const detail = document.createElement("p");
    detail.textContent =
      new Date(entry.time * 1000).toLocaleString("en-GB", {
        timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
      }) +
      " · " +
      entry.actor.username +
      (entry.job_id ? " · Operation " + entry.job_id.slice(0, 8) : "");
    row.append(title, detail);
    if (entry.changes) {
      const changes = document.createElement("pre");
      changes.textContent = JSON.stringify(entry.changes, null, 2);
      row.append(changes);
    }
    $("audit-list").append(row);
  }
  if (!data.events.length) $("audit-list").textContent = "No records match the selected filters.";
}
$("audit-filter-form").onsubmit = (event) => {
  event.preventDefault();
  guard(loadAudit);
};

function renderImageVersion(version) {
  const text =
    "Installed: " +
    (version.installed || "not identified") +
    " · Available on Docker Hub: " +
    (version.available || "not checked yet") +
    (version.update_available ? " · New version available" : "") +
    (version.checked
      ? " · Last check: " +
        new Date(version.checked * 1000).toLocaleString("en-GB", {
          timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
        })
      : "") +
    (version.error ? " · " + version.error : "") +
    (version.stale && !version.error ? " · Information awaiting refresh" : "");
  $("image-version").textContent = text + (version.checking ? " · Checking" : "");
  $("image-version-status").textContent = $("image-version").textContent;
}
$("check-image-version").onclick = () =>
  guard(async () => {
    $("check-image-version").disabled = true;
    try {
      renderImageVersion(await api("/image-version"));
    } finally {
      $("check-image-version").disabled = false;
    }
  });

function confirmPluginChange(action, name) {
  const dialog = $("plugin-confirmation");
  if (dialog.open) return Promise.resolve(false);
  $("plugin-confirm-title").textContent =
    action === "delete" ? "Delete plugin?" : "Restore deleted plugin?";
  $("plugin-confirm-description").textContent =
    action === "delete"
      ? "Delete “" +
        name +
        "”? The game must be stopped. A recovery copy and its settings will be preserved. Dependent plugins may stop working."
      : "Restore “" +
        name +
        "”? The game must be stopped. An installed DLL at this path will not be overwritten.";
  $("plugin-confirm-submit").textContent = action === "delete" ? "Delete plugin" : "Restore plugin";
  dialog.returnValue = "cancel";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {
      once: true,
    });
    dialog.showModal();
  });
}

let pluginMutationPending = false;
function pluginsEditable() {
  return (
    isAdmin() &&
    lastPanelState &&
    !lastPanelState.running &&
    !pluginMutationPending &&
    !(lastPanelState.jobs || []).some((job) =>
      ["queued", "running", "deferred"].includes(job.state),
    )
  );
}
function syncDeletedPluginControls() {
  document.querySelectorAll("[data-plugin-write]").forEach((button) => {
    const conflict = button.dataset.restoreConflict === "true";
    button.disabled = !pluginsEditable() || conflict;
    if (conflict) {
      button.title = "A DLL is already installed at this path; it will not be overwritten";
      return;
    }
    button.title = button.disabled ? "Stop the game and wait for pending operations to finish" : "";
  });
}
