"use strict";
const $ = (id) => document.getElementById(id);
let csrf = "",
  events = null,
  samples = [],
  currentConfig = null;
let panelUser = null;
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
let imageUpdateTimer = null;
let imageUpdateRequesting = false;
function signedOut() {
  clearInterval(imageUpdateTimer);
  imageUpdateTimer = null;
  if (events) events.close();
  events = null;
  csrf = "";
  panelUser = null;
  currentConfig = null;
  samples = [];
  maintenanceJobs = [];
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
    "mod-config-file",
    "mod-session-plugins",
    "mod-session-issues",
    "known-player",
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
    "last-failure",
    "mod-session-status",
    "mod-session-version",
    "mod-session-pending",
    "versions",
    "image-version",
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
function modDiagnostics(d) {
  const states = {
    disabled: "BepInEx desativado nesta sessão.",
    awaiting_start: "Aguardando início do jogo com BepInEx.",
    stopped: "Jogo parado. Exibindo a última sessão deste supervisor.",
    complete: "Carregador BepInEx concluído; confira os avisos e erros abaixo.",
    loading: "BepInEx detectado; carregamento em andamento.",
    unconfirmed: "BepInEx habilitado, mas ainda sem confirmação nos logs.",
  };
  $("mod-session-status").textContent = states[d.state] || "Estado desconhecido.";
  $("mod-session-version").textContent =
    "Versão detectada: " +
    (d.version || "não identificada nos logs") +
    (d.started
      ? " · Sessão iniciada em " +
        new Date(d.started * 1000).toLocaleString("pt-BR", {
          timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
        })
      : "");
  $("mod-session-pending").textContent =
    (d.pending_config ? "Alteração de ativação do BepInEx aguarda novo início do jogo. " : "") +
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
        ? "Falha ou carregamento ignorado"
        : d.complete
          ? "Tentativa registrada; carregador concluído"
          : "Carregamento iniciado");
    plugins.append(item);
  }
  if (!d.plugins.length) {
    const item = document.createElement("li");
    item.textContent = "Nenhum plugin identificado nos logs desta sessão.";
    plugins.append(item);
  }
  const issues = $("mod-session-issues");
  issues.replaceChildren();
  for (const issue of d.issues) {
    const item = document.createElement("li");
    item.textContent =
      (issue.level === "warning" ? "Aviso" : "Erro") + " · " + issue.source + ": " + issue.message;
    issues.append(item);
  }
  if (!d.issues.length) {
    const item = document.createElement("li");
    item.textContent = "Nenhum aviso ou erro capturado no diagnóstico.";
    issues.append(item);
  }
}
function update(s) {
  if (s.server_name) $("server-name").textContent = s.server_name;
  if (isAdmin() && s.audit_error) $("audit-warning").textContent = s.audit_error;
  if (s.run_intent) {
    $("run-intent").textContent =
      (s.run_intent.running
        ? "Após reiniciar o contêiner: iniciar o jogo automaticamente."
        : "Após reiniciar o contêiner: manter o jogo parado.") +
      (s.run_intent.error ? " " + s.run_intent.error : "");
  }
  if (s.mods) modDiagnostics(s.mods);
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
  const presence = s.players;
  const presenceDetails = [presence.reason];
  if (presence.source === "game_log") {
    presenceDetails.push("Fonte: log do jogo (Connections)");
    if (typeof presence.updated_at === "number") {
      presenceDetails.push(
        "Observado em " +
          new Date(presence.updated_at * 1000).toLocaleString("pt-BR", {
            timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
            hourCycle: "h23",
          }),
      );
    }
    if (typeof presence.age_seconds === "number")
      presenceDetails.push("Há " + presence.age_seconds + "s");
    if (presence.count === null && typeof presence.last_count === "number")
      presenceDetails.push("Última contagem: " + presence.last_count + " (sem confirmação atual)");
    if (presence.count === 0 && typeof presence.idle_wait_seconds === "number")
      presenceDetails.push(
        presence.idle_wait_seconds > 0
          ? "Zero no log: aguardando " + presence.idle_wait_seconds + "s para manutenção automática"
          : "Zero no log há pelo menos 2 min; demais verificações ainda se aplicam",
      );
  }
  if (!s.crossplay && presence.steam?.updated_at) {
    presenceDetails.push(
      "Última consulta Steam da manutenção: " +
        (presence.steam.count === null ? "indisponível" : presence.steam.count + " jogador(es)") +
        " · há " +
        presence.steam.age_seconds +
        "s",
    );
  }
  $("player-reason").textContent = presenceDetails.join(" · ");
  $("versions").textContent =
    "Build instalado: " +
    (s.installed_build || "—") +
    " · Disponível: " +
    (s.available_build || "—");
  if (s.image_version) {
    const version = s.image_version;
    $("image-version").textContent =
      "Imagem: " +
      (version.installed || "não identificada") +
      (version.update_available
        ? " · Nova versão pública: " +
          version.available +
          ". Atualize pelo SSH: sudo python3 update-server.py"
        : "") +
      (version.error ? " · " + version.error : "");
  }
  $("pending").textContent = s.pending_config ? "Alterações aguardam reinício" : "";
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
    if (["bepinex_url", "bepinex_sha256"].includes(key)) continue;
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
  $("logout").hidden = false;
  $("identity").textContent =
    panelUser.username + " · " + (isAdmin() ? "Administrador" : "Operador");
  $("own-password").hidden = false;
  if (panelUser.must_change_password) {
    $("dashboard").hidden = true;
    $("password-change").hidden = false;
    $("close-password-change").hidden = true;
    $("password-change-message").textContent =
      "Troque a senha temporária para liberar o acesso ao painel.";
    return;
  }
  $("password-change").hidden = true;
  $("dashboard").hidden = false;
  document.querySelectorAll("[data-admin-only]").forEach((element) => {
    if (element.getAttribute("role") !== "tabpanel") element.hidden = !isAdmin();
    else if (!isAdmin()) element.hidden = true;
  });
  selectTab($("tab-overview"));
  $("history-title").textContent = isAdmin() ? "Histórico de manutenção" : "Suas operações";
  $("action-policy").textContent = isAdmin()
    ? "Ações administrativas solicitam a parada imediatamente e podem desconectar jogadores. O jogo salva antes de encerrar."
    : "Iniciar é imediato. Parar e reiniciar aguardam o jogo pronto e zero jogadores confirmado por dois minutos; no modo Steam também exigem consulta Steam. Acompanhe suas solicitações na aba Atividade.";
  if (isAdmin()) {
    await Promise.all([
      loadConfig(),
      loadImageUpdateStatus(),
      loadUsers(),
      loadBackups(),
      loadCloud(),
      loadAlerts().catch((error) =>
        notice("Não foi possível carregar os alertas: " + error.message),
      ),
      loadModConfigs().catch((error) =>
        notice("Não foi possível carregar as configurações dos plugins: " + error.message),
      ),
      loadPlugins().catch((error) =>
        notice("Não foi possível carregar a lista de plugins: " + error.message),
      ),
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
    title: "Atualizar a imagem do servidor?",
    label: "Atualizar imagem",
    description:
      "O host baixará e validará a nova imagem, criará um backup e trocará os contêineres. O painel pode desconectar e solicitar novo login. Em caso de falha após a troca, tentará retornar à imagem anterior.",
  },
  stop: {
    title: "Parar o servidor?",
    label: "Parar servidor",
    description: "O jogo será encerrado e permanecerá parado até uma nova solicitação de início.",
  },
  restart: {
    title: "Reiniciar o servidor?",
    label: "Reiniciar servidor",
    description:
      "O jogo será encerrado e iniciado novamente, aplicando as configurações pendentes.",
  },
  update: {
    title: "Atualizar o jogo?",
    label: "Atualizar jogo",
    description:
      "O supervisor verificará e instalará a atualização do jogo. A operação pode exigir a parada e a inicialização do servidor.",
  },
  backup: {
    title: "Criar backup agora?",
    label: "Criar backup",
    description:
      "Para criar uma cópia consistente do mundo, o jogo será parado. Se estava em execução, será iniciado novamente após o backup.",
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
      ? " Sua solicitação aguardará a confirmação de zero jogadores antes de parar o jogo."
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
      notice("Operação enviada. Acompanhe o resultado na aba Atividade.");
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
  if (tab.hidden) return;
  if (tab.id === "tab-administration" && isAdmin()) guard(loadAudit);
  tabs.forEach((item) => {
    const selected = item === tab && !item.hidden;
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
    $(item.getAttribute("aria-controls")).hidden = !selected;
  });
  if (focus) tab.focus();
}
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
  if (data.diagnostics) modDiagnostics(data.diagnostics);
  $("plugin-list").replaceChildren();
  if (!data.files.length) {
    $("plugin-list").textContent = "Nenhuma DLL instalada ainda.";
  }
  data.files.forEach((file) => {
    const row = document.createElement("div");
    row.className = "plugin-entry";
    const label = document.createElement("span");
    const sessionStates = {
      awaiting_start: "Aguarda início do jogo",
      pending_restart: "Alterado após o último início; aguarda reinício",
      present_at_start: "Presente no início; confira o carregamento acima",
      last_session: "Presente na última sessão",
      unknown: "Comparação com a sessão indisponível",
    };
    label.textContent =
      file.name +
      " · " +
      bytes(file.bytes) +
      " · " +
      (sessionStates[file.session_state] || "Carregamento não confirmado");
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

function renderImageUpdate(status) {
  $("update-image").disabled =
    imageUpdateRequesting || !status.available || status.state === "running";
  $("image-update-status").textContent = status.message || "Estado da atualização indisponível.";
}
async function loadImageUpdateStatus() {
  try {
    renderImageUpdate(await api("/image-update"));
  } catch (error) {
    $("update-image").disabled = true;
    $("image-update-status").textContent =
      "Painel temporariamente indisponível; reconectando para consultar a atualização. Pode ser necessário entrar novamente.";
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
        "Atualização solicitada. Acompanhe em Configurações do servidor; entre novamente se o painel pedir login após a troca.",
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
      ? new Date(timestamp * 1000).toLocaleString("pt-BR", { timeZone: zone, hourCycle: "h23" })
      : "—";
  const day = (timestamp) =>
    new Intl.DateTimeFormat("en-CA", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date(timestamp * 1000));
  const origins = {
    administrator: "Administrador",
    operator: "Operador",
    schedule: "Agendamento",
    recovery: "Recuperação",
  };
  const actions = {
    start: "Início",
    stop: "Parada",
    restart: "Reinício",
    backup: "Backup",
    update: "Atualização do jogo",
    restore: "Restauração",
    "check-update": "Verificação de atualização",
    "recover-startup": "Recuperação da inicialização",
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
    when.textContent = "Solicitado em " + format(job.created);
    if (typeof job.created === "number" && Number.isFinite(job.created))
      when.dateTime = new Date(job.created * 1000).toISOString();
    when.title = "Horários em " + zone;
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
      " · Início: " +
      format(job.started) +
      " · Término: " +
      format(job.finished) +
      " · Duração em execução: " +
      duration +
      " · Tentativas: " +
      (job.attempts || 0);
    row.append(when, heading, details);
    for (const text of [
      job.error,
      job.result,
      job.backup_created ? "Backup criado: " + job.backup_created : null,
      job.backup ? "Backup restaurado: " + job.backup : null,
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
    $("jobs").textContent = "Nenhuma operação neste período com os filtros selecionados.";
}
for (const id of ["history-origin", "history-state", "history-from", "history-to"]) {
  $(id).addEventListener("change", renderMaintenanceHistory);
}

$("own-password").onclick = () => {
  $("password-change").hidden = false;
  $("close-password-change").hidden = panelUser.must_change_password;
  $("password-change-message").textContent = panelUser.must_change_password
    ? "Troque a senha temporária para liberar o painel."
    : "Alterar a senha encerra suas sessões. Entre novamente depois de salvar.";
};
$("close-password-change").onclick = () => {
  $("password-change").hidden = true;
  $("password-change-form").reset();
};
$("password-change-form").onsubmit = (event) => {
  event.preventDefault();
  guard(async () => {
    if ($("new-password").value !== $("repeat-password").value)
      throw new Error("As novas senhas não conferem");
    await api("/password", "POST", {
      current_password: $("current-password").value,
      password: $("new-password").value,
    });
    signedOut();
    notice("Senha alterada. Entre novamente com sua nova senha.");
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
    role.setAttribute("aria-label", "Perfil de " + user.username);
    for (const [value, label] of [
      ["operator", "Operador"],
      ["admin", "Administrador"],
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
    enabled.setAttribute("aria-label", "Conta ativa de " + user.username);
    enabled.checked = user.enabled;
    label.append(enabled, document.createTextNode(" Conta ativa"));
    const save = document.createElement("button");
    save.textContent = "Salvar " + user.username;
    role.disabled = enabled.disabled = save.disabled = user.id === "admin";
    save.onclick = () =>
      guard(async () => {
        await api("/accounts/" + encodeURIComponent(user.id), "PATCH", {
          role: role.value,
          enabled: enabled.checked,
        });
        await loadAccounts();
        await loadAudit();
        notice("Conta atualizada; sessões e solicitações pendentes foram invalidadas.");
      });
    const info = document.createElement("small");
    info.textContent = user.must_change_password
      ? "Troca de senha pendente"
      : "Senha pessoal definida";
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
      notice("Usuário cadastrado. Ele deverá trocar a senha temporária no primeiro acesso.");
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
      notice("Senha redefinida. Sessões encerradas; troca obrigatória no próximo acesso.");
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
    configuration: "Configuração",
    "server-action": "Solicitação de operação",
    start: "Iniciar",
    stop: "Parar",
    restart: "Reiniciar",
    backup: "Backup",
    restore: "Restauração",
    update: "Atualizar jogo",
    "image-update": "Atualizar imagem",
    "password-change": "Trocar senha",
    accounts: "Contas",
    plugins: "Plugins",
    "plugin-config": "Configuração de plugin",
    "player-lists": "Listas de jogadores",
    alerts: "Alertas",
    cloud: "Nuvem",
    "backup-download": "Download de backup",
    login: "Entrada",
    logout: "Saída",
  };
  for (const entry of data.events) {
    const row = document.createElement("div");
    row.className = "job";
    const title = document.createElement("strong");
    title.textContent = (labels[entry.operation] || entry.operation) + " · " + entry.outcome;
    const detail = document.createElement("p");
    detail.textContent =
      new Date(entry.time * 1000).toLocaleString("pt-BR", {
        timeZone: currentConfig?.values.timezone || "America/Sao_Paulo",
      }) +
      " · " +
      entry.actor.username +
      (entry.job_id ? " · Operação " + entry.job_id.slice(0, 8) : "");
    row.append(title, detail);
    if (entry.changes) {
      const changes = document.createElement("pre");
      changes.textContent = JSON.stringify(entry.changes, null, 2);
      row.append(changes);
    }
    $("audit-list").append(row);
  }
  if (!data.events.length)
    $("audit-list").textContent = "Nenhum registro com os filtros selecionados.";
}
$("audit-filter-form").onsubmit = (event) => {
  event.preventDefault();
  guard(loadAudit);
};
