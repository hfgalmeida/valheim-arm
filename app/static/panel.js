"use strict";
const $ = (id) => document.getElementById(id);
let csrf = "",
  events = null,
  samples = [],
  currentConfig = null;
const labels = {
  server_name: "Nome do servidor",
  world_name: "Nome do mundo",
  server_password: "Senha do jogo (vazio mantém)",
  server_port: "Porta (Compose)",
  public: "Listagem pública",
  crossplay: "Crossplay",
  preset: "Preset",
  modifier_combat: "Combate",
  modifier_deathpenalty: "Penalidade de morte",
  modifier_resources: "Recursos",
  modifier_raids: "Incursões",
  modifier_portals: "Portais",
  key_nobuildcost: "Construção sem custo",
  key_playerevents: "Eventos por jogador",
  key_passivemobs: "Inimigos passivos",
  key_nomap: "Sem mapa",
  save_interval: "Salvar a cada (segundos)",
  native_backups: "Backups nativos",
  timezone: "Fuso horário",
  restart_at: "Reinício diário (HH:MM ou off)",
  backup_at: "Backup diário (HH:MM ou off)",
  auto_update: "Atualizar jogo automaticamente",
  retain_daily: "Retenção diária",
  retain_weekly: "Retenção semanal",
  bepinex: "Habilitar BepInEx",
  bepinex_url: "URL HTTPS do BepInExPack Linux",
  bepinex_sha256: "SHA256 do pacote BepInEx",
};
function notice(text) {
  $("notice").textContent = text;
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
function signedOut() {
  if (events) events.close();
  events = null;
  csrf = "";
  $("dashboard").hidden = true;
  $("logout").hidden = true;
  $("login").hidden = false;
  $("mod-config-content").value = "";
  modConfig = null;
  $("plugin-file").value = "";
  $("plugin-upload-status").textContent = "";
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
    w = c.width,
    h = c.height;
  x.clearRect(0, 0, w, h);
  x.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--line").trim();
  x.beginPath();
  x.moveTo(0, h - 1);
  x.lineTo(w, h - 1);
  x.stroke();
  if (values.length < 2) return;
  const max = ceiling || Math.max(...values, 1);
  x.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  x.lineWidth = 3;
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
    ? " · Último envio: " +
      new Date(c.last_success * 1000).toLocaleString() +
      " (" +
      c.last_file +
      ")"
    : "";
  $("cloud-status").textContent =
    (c.enabled
      ? (c.uploading ? "Enviando" : "Ativado") +
        (c.provider ? " · " + c.provider : "") +
        " · Pendentes: " +
        c.pending
      : "Envio desativado") +
    last +
    (c.error ? " · " + c.error + ". Nova tentativa automática em cinco minutos." : "");
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
    cloud: "envio à nuvem",
    game: "recuperação do jogo",
    disk: "espaço em disco",
  };
  $("alerts-status").textContent =
    (a.enabled ? "Notificações ativadas." : "Notificações desativadas.") +
    (a.configured ? " Webhook salvo." : " Webhook não configurado.") +
    (a.active.length
      ? " Atenção: " + a.active.map((key) => names[key] || key).join(", ") + "."
      : " Nenhum problema ativo.") +
    (a.error ? " " + a.error : "");
}
async function loadAlerts() {
  const a = await api("/alerts");
  $("alerts-enabled").value = String(a.enabled);
  $("alerts-url").value = "";
  alertStatus(a);
}
function update(s) {
  if (s.alerts) alertStatus(s.alerts);
  if (s.cloud) cloudStatus(s.cloud);
  $("crossplay-info").hidden = !s.crossplay;
  $("join-code").textContent =
    s.join_code ||
    (s.running ? "Aguardando código do servidor…" : "Disponível ao iniciar o servidor");
  $("status").textContent =
    "Estado: " + s.state + (s.recovery_error ? " — " + s.recovery_error : "");
  $("panel-health").textContent = "Painel: conectado ao supervisor";
  const diagnostic = s.diagnostics;
  const phases = {
    ready: "Pronto para conexão",
    starting: "Iniciando",
    stopping: "Parando",
    stopped: "Parado",
    recovering: "Em recuperação",
    failed: "Falha",
  };
  if (diagnostic) {
    $("status").textContent = "Jogo: " + (phases[diagnostic.phase] || s.state);
    $("game-detail").textContent =
      diagnostic.detail +
      (diagnostic.retry_in_seconds === null
        ? ""
        : " Nova tentativa em " + diagnostic.retry_in_seconds + " s.");
    const failure = diagnostic.last_failure;
    $("last-failure").hidden = !failure;
    $("last-failure").textContent = failure
      ? "Última falha (" +
        failure.source +
        ") · " +
        new Date(failure.time * 1000).toLocaleString("pt-BR", {
          timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
        }) +
        ": " +
        failure.message
      : "";
  }
  $("players").textContent = s.players.count === null ? "?" : s.players.count;
  $("player-reason").textContent = s.players.reason;
  $("versions").textContent =
    "Build instalado: " +
    (s.installed_build || "—") +
    " · Disponível: " +
    (s.available_build || "—");
  $("pending").textContent = s.pending_config ? "Alterações aguardam reinício" : "";
  $("logs").textContent = s.logs
    .map((l) => new Date(l.time * 1000).toLocaleTimeString() + "  " + l.message)
    .join("\n");
  $("jobs").replaceChildren();
  [...s.jobs]
    .reverse()
    .slice(0, 15)
    .forEach((j) => {
      const r = document.createElement("div");
      r.className = "job";
      const when = document.createElement("time");
      const zone = currentConfig?.values.timezone || "America/Sao_Paulo";
      if (typeof j.created === "number" && Number.isFinite(j.created)) {
        const date = new Date(j.created * 1000);
        when.dateTime = date.toISOString();
        when.textContent =
          "Solicitado em " +
          date.toLocaleString("pt-BR", {
            timeZone: zone,
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hourCycle: "h23",
          });
      } else {
        when.textContent = "Horário indisponível";
      }
      when.title = "Horário da solicitação · " + zone;
      r.append(
        when,
        document.createTextNode(
          j.action +
            " · " +
            j.state +
            (j.error ? " — " + j.error : "") +
            (j.result ? " — " + j.result : ""),
        ),
      );
      $("jobs").append(r);
    });
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
    "Medição: " +
    m.scope +
    ". Disco: sistema de arquivos do volume. Histórico: até uma hora; atualização a cada 5 segundos.";
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
    const container = $(settingsContainers[configGroup(key)]);
    const heading = {
      server_name: "Mundo e acesso",
      preset: "Regras de jogo",
      save_interval: "Salvamento",
      timezone: "Rotina automática",
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
          v === "" ? "Padrão do mundo/preset" : v === "true" ? "Sim" : v === "false" ? "Não" : v;
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
    source.textContent = "Origem: " + currentConfig.sources[key];
    row.append(source);
    if (currentConfig.sources[key] === "painel" && key !== "server_port") {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "reset";
      b.textContent = "Usar Compose";
      b.onclick = () =>
        guard(async () => {
          const world = key === "world_name";
          if (world && !confirm("Voltar ao mundo do Compose? O save atual será preservado."))
            return;
          await api("/config", "PATCH", { reset: [key], confirm_world: world });
          await loadConfig(configGroup(key));
          notice("Ajuste removido. Reinicie com segurança para aplicar ao jogo.");
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
function renderPlayers() {
  const entries = draftPlayers();
  $("user-list-count").textContent = entries.length + " jogador(es) na lista";
  $("user-list").replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Nenhum jogador nesta lista.";
    $("user-list").append(empty);
  }
  entries.forEach((id) => {
    const row = document.createElement("div");
    row.className = "player-entry";
    const label = document.createElement("code");
    label.textContent = id;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remover";
    remove.setAttribute("aria-label", "Remover " + id);
    remove.onclick = () => {
      $("user-entries").value = draftPlayers()
        .filter((value) => value !== id)
        .join("\n");
      renderPlayers();
      notice("Jogador removido da seleção. Salve a lista para aplicar.");
    };
    row.append(label, remove);
    $("user-list").append(row);
  });
}
function knownPlayers(players) {
  const selected = $("known-player").value;
  $("known-player").replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = players.length
    ? "Selecione um jogador"
    : "Nenhum ID identificado ainda";
  $("known-player").append(placeholder);
  players.forEach((player) => {
    const option = document.createElement("option");
    option.value = player.id;
    option.textContent = player.id;
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
    $("backup-list").textContent = "Nenhum backup criado ainda.";
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
    button.textContent = "Restaurar";
    button.onclick = () =>
      guard(async () => {
        if (
          !confirm(
            "Restaurar " +
              b.name +
              "? Pare o jogo primeiro. Será criado um backup do estado atual.",
          )
        )
          return;
        await api("/actions", "POST", { action: "restore", backup: b.name, confirm: true });
        notice("Restauração enviada. Acompanhe na aba Atividade.");
      });
    row.append(button);
    $("backup-list").append(row);
  });
}
async function signedIn() {
  $("login").hidden = true;
  $("dashboard").hidden = false;
  $("logout").hidden = false;
  await Promise.all([
    loadConfig(),
    loadUsers(),
    loadBackups(),
    loadCloud(),
    loadAlerts().catch((error) => notice("Não foi possível carregar os alertas: " + error.message)),
    loadModConfigs().catch((error) =>
      notice("Não foi possível carregar as configurações dos plugins: " + error.message),
    ),
    loadPlugins().catch((error) =>
      notice("Não foi possível carregar a lista de plugins: " + error.message),
    ),
  ]);
  const state = await api("/state");
  samples = state.metrics;
  update(state);
  if (events) events.close();
  events = new EventSource("/api/v1/events");
  events.onmessage = (e) => update(JSON.parse(e.data));
  events.onerror = () => {
    $("panel-health").textContent = "Painel: conexão interrompida; reconectando…";
    $("status").textContent = "Jogo: estado sem atualização";
    notice("Conexão interrompida; tentando reconectar ao supervisor…");
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
    const r = await api("/login", "POST", { password: $("password").value });
    $("password").value = "";
    csrf = r.csrf;
    notice("");
    await signedIn();
  });
};
$("logout").onclick = () =>
  guard(async () => {
    await api("/logout", "POST", {});
    signedOut();
  });
$("actions").onclick = (e) => {
  const action = e.target.dataset.action;
  if (!action) return;
  guard(async () => {
    if (
      action === "stop" &&
      !confirm(
        "Parar o servidor agora? Jogadores conectados serão desconectados. O jogo terá tempo para salvar.",
      )
    )
      return;
    await api("/actions", "POST", { action });
    notice("Operação enviada. Acompanhe o resultado na aba Atividade.");
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
        const value = $("setting-" + key).value;
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
          "Selecionar/criar o mundo “" +
            changes.world_name +
            "”? O mundo anterior será preservado.",
        )
      )
        return;
      await api("/config", "PATCH", { changes, confirm_world: world });
      await loadConfig(group);
      notice("Configuração salva. Alterações do jogo serão aplicadas na próxima inicialização.");
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
      !confirm("Restringir o acesso somente aos IDs permitidos?")
    )
      return;
    const saved = await api("/users/" + kind, "PUT", { entries: draftPlayers() });
    if (kind === $("user-kind").value) {
      $("user-entries").value = saved.entries.join("\n");
      renderPlayers();
    }
    notice("Lista salva. O jogo recarrega as listas; se necessário, reinicie com segurança.");
  });
};
$("refresh-backups").onclick = () => guard(loadBackups);
guard(async () => {
  try {
    csrf = (await api("/session")).csrf;
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
    notice("Envio na nuvem salvo. Novos backups serão enviados automaticamente quando ativado.");
  });
};

$("cloud-file").onchange = () =>
  guard(async () => {
    const f = $("cloud-file").files[0];
    if (!f) return;
    if (f.size > 65536) throw new Error("Arquivo de conexão muito grande (máximo 64 KiB)");
    $("cloud-config").value = await f.text();
  });

const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(tab, focus = false) {
  tabs.forEach((item) => {
    const selected = item === tab;
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
    $(item.getAttribute("aria-controls")).hidden = !selected;
  });
  if (focus) tab.focus();
}
tabs.forEach((tab, index) => {
  tab.onclick = () => selectTab(tab);
  tab.onkeydown = (event) => {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    selectTab(tabs[next], true);
  };
});

$("known-player").onchange = () => {
  if ($("known-player").value) $("player-id").value = $("known-player").value;
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
        "Cole um ID de plataforma completo, como Steam_ seguido do SteamID64, ou apenas os 17 dígitos do SteamID64.",
      );
    const identity = (value) => (/^\d{17}$/.test(value) ? "Steam_" + value : value);
    const entries = draftPlayers();
    if (entries.some((value) => identity(value) === id)) {
      notice("Esse jogador já está na lista selecionada.");
      return;
    }
    if (entries.length >= 1000) throw new Error("A lista aceita no máximo 1000 jogadores.");
    $("user-entries").value = [...entries, id].join("\n");
    $("player-id").value = "";
    $("known-player").value = "";
    renderPlayers();
    notice("Jogador adicionado à seleção. Salve a lista para aplicar.");
  });
};

let modConfig = null;
async function loadModConfigs() {
  const data = await api("/mod-configs");
  $("mod-config-file").replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = data.files.length
    ? "Selecione um arquivo"
    : "Nenhuma configuração encontrada";
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
    confirm("Descartar as alterações não salvas deste arquivo?")
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
    notice("Arquivo salvo. Inicie o jogo para aplicar a configuração do plugin.");
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
      notice("Versão anterior carregada no editor. Clique em Salvar arquivo para restaurar.");
    }
  });

async function loadPlugins() {
  const data = await api("/plugins");
  $("plugin-list").replaceChildren();
  if (!data.files.length) {
    $("plugin-list").textContent = "Nenhuma DLL instalada ainda.";
  }
  data.files.forEach((file) => {
    const row = document.createElement("div");
    row.className = "plugin-entry";
    const label = document.createElement("span");
    label.textContent = file.name + " · " + bytes(file.bytes);
    row.append(label);
    if (file.previous_available && file.revision) {
      const restore = document.createElement("button");
      restore.type = "button";
      restore.textContent = "Restaurar anterior";
      restore.setAttribute("aria-label", "Restaurar versão anterior de " + file.name);
      restore.onclick = () =>
        guard(async () => {
          if (
            !confirm("Restaurar a DLL anterior de " + file.name + "? O jogo precisa estar parado.")
          )
            return;
          await api("/plugins/restore", "POST", { name: file.name, revision: file.revision });
          await loadPlugins();
          notice("DLL anterior restaurada. Inicie o jogo para carregá-la.");
        });
      row.append(restore);
    }
    $("plugin-list").append(row);
  });
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
      throw new Error("Selecione uma DLL de até 16 MiB, com nome simples e sem pastas.");
    $("upload-plugin").disabled = true;
    try {
      const files = await loadPlugins();
      const existing = files.find((item) => item.name === file.name);
      if (existing && !existing.revision)
        throw new Error("A DLL existente ultrapassa 16 MiB; gerencie-a pelo SSH.");
      if (existing && !confirm("Substituir " + file.name + "? A versão anterior será preservada."))
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
          typeof result.detail === "string" ? result.detail : "Não foi possível enviar a DLL.",
        );
      }
      $("plugin-file").value = "";
      $("plugin-upload-status").textContent =
        "Plugin enviado: " + result.name + ". Inicie o jogo para carregá-lo.";
      await loadPlugins();
    } catch (error) {
      $("plugin-upload-status").textContent = "Envio não concluído.";
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
    });
    $("alerts-url").value = "";
    alertStatus(a);
    notice("Alertas salvos.");
  });
};
$("alerts-test").onclick = () =>
  guard(async () => {
    await api("/alerts/test", "POST", {});
    notice("Notificação de teste entregue ao Discord.");
  });
