const DESCRIPTIONS = {
  battle: "Ataca quando aparece inimigo na batalha.",
  capture: "Procura os Pokémon selecionados e lança a Pokébola.",
  cavebot: "Anda pela rota do minimapa, luta e captura.",
  heal: "Aperta a tecla de cura quando sua vida cai.",
  switch: "Alterna continuamente entre as posições na ordem configurada.",
  combat: "Executa o modo selecionado: batalha, batalha com troca, ou somente troca.",
  macro: "Reproduz a rota gravada; pode repetir em ciclo e aguardar encontros prioritários.",
  pairing: "Aguarda o minigame, pausa os outros módulos, resolve, confirma e verifica se a tela fechou.",
  loot: "Verifica os tiles selecionados e coleta os itens dos Pokémon derrotados.",
};
const PICK_TARGETS = {
  "cavebot.map_region": "Área do minimapa",
  "capture.region": "Área de captura",
  "pairing.region": "Área de referência do minigame",
  "cavebot.hp_pixel": "Pixel da vida do inimigo",
  "heal.pixel": "Pixel da sua vida",
  "heal.order_point": "Ponto da Order",
  "loot.center": "Centro do tile do personagem",
  "loot.north": "Centro do tile vizinho ao norte",
  "loot.east": "Centro do tile vizinho ao leste",
  "loot.window_sample_region": "Janela de loot para calibração",
  "loot.anchor_region": "Âncora dos controles direitos da janela de loot",
  "loot.first_slot_offset": "Primeiro slot relativo à janela",
  new_pokemon: "Recortar Pokémon da tela",
  new_battle_target: "Recortar Pokémon da lista de combate",
  "battle.pokemon_list_region": "Área da lista de combate",
  new_battle_empty: "Definir a referência da lista de batalha vazia",
  new_waypoint: "Recortar ponto do minimapa",
  switch_slot: "Posição de troca",
};
const FKEYS = Array.from({ length: 12 }, (_, i) => `F${i + 1}`);

let api = null;
let config = null;
let images = null;
let lastSeq = 0;
let pickKey = "F8";
let pickHandled = null;
let pendingPokemonName = "";
let pendingBattleTargetName = "";
let macroWasRecording = false;
let timerEditing = false;
const moduleEls = {};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const stem = (name) => name.replace(/\.png$/i, "");
const pretty = (name) => stem(name).replace(/_/g, " ");

function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
}
function setPath(obj, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  keys.reduce((o, k) => (o[k] ??= {}), obj)[last] = value;
}

function toast(msg, isError = false) {
  let t = $(".toast");
  if (!t) document.body.append((t = el("div", "toast")));
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.classList.add("show");
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove("show"), isError ? 4000 : 1800);
}

// Chama a API do Python mostrando o erro em vez de travar a interface.
async function call(name, ...args) {
  try {
    return await api[name](...args);
  } catch (e) {
    toast(e?.message || String(e), true);
    throw e;
  }
}

function fmtDuration(sec) {
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h) return `${h}h ${String(m).padStart(2, "0")}min`;
  if (m) return `${m}min`;
  return `${sec}s`;
}

// ---------- modal ----------
function openModal(title, body) {
  $("#modal-title").textContent = title;
  $("#modal-body").replaceChildren(body);
  $("#modal").hidden = false;
}
function closeModal() {
  $("#modal").hidden = true;
}

// ---------- módulos ----------
function buildModules(modules) {
  const tpl = $("#module-tpl");
  const names = Object.keys(modules);
  const savedOrder = Array.isArray(config.module_order) ? config.module_order : [];
  const orderedNames = [...savedOrder.filter((name) => names.includes(name)), ...names.filter((name) => !savedOrder.includes(name))];
  for (const name of orderedNames) {
    const label = modules[name];
    const node = tpl.content.firstElementChild.cloneNode(true);
    $(".m-name", node).textContent = label;
    $(".m-desc", node).textContent = DESCRIPTIONS[name] || "";
    const input = $("input", node);
    input.setAttribute("aria-label", `Ligar ${label}`);
    input.addEventListener("change", async () => {
      const args = name === "macro"
        ? [name, input.checked, $("#macro-select").value, $("#macro-loop").checked]
        : [name, input.checked];
      const ok = await call("toggle_module", ...args).catch((error) => {
        toast(error.message || "N\u00e3o foi poss\u00edvel alternar o m\u00f3dulo", true);
        return false;
      });
      if (input.checked && !ok) input.checked = false;
    if (name === "macro" && ok) {
      config.macro.name = $("#macro-select").value;
      config.macro.loop = $("#macro-loop").checked;
    }
    });
    $(".m-order-up", node).addEventListener("click", () => moveModule(name, -1));
    $(".m-order-down", node).addEventListener("click", () => moveModule(name, 1));
    $("#modules").append(node);
    moduleEls[name] = { node, input, label, state: $(".m-state", node), text: $(".m-text", node), key: $(".m-key", node), up: $(".m-order-up", node), down: $(".m-order-down", node) };
  }
  updateModuleOrderControls();

  // filtros do console e campos de atalho seguem a lista de módulos
  const chips = $("#log-filter");
  const chip = (f, label) => Object.assign(el("button", "chip" + (f === "all" ? " on" : ""), label), { type: "button" });
  chips.append(chip("all", "Tudo"), ...Object.entries(modules).map(([n, l]) => chip(n, l)), chip("system", "Sistema"));
  $$(".chip", chips).forEach((c, i) => (c.dataset.f = ["all", ...Object.keys(modules), "system"][i]));

  const fields = $("#hotkey-fields");
  for (const [name, label] of Object.entries(modules)) {
    const lab = el("label", null, label);
    const sel = el("select", "hk");
    sel.dataset.path = `hotkeys.${name}`;
    lab.append(sel);
    fields.append(lab);
  }
  for (const sel of $$("select.hk")) {
    const isStop = sel.dataset.path === "stop_hotkey";
    if (!isStop) sel.append(Object.assign(el("option", null, "Nenhum"), { value: "" }));
    for (const k of FKEYS) if (k !== pickKey) sel.append(Object.assign(el("option", null, k), { value: k }));
  }
}

function updateModuleOrderControls() {
  const nodes = $$("#modules .module");
  nodes.forEach((node, index) => {
    const name = Object.keys(moduleEls).find((key) => moduleEls[key].node === node);
    if (!name) return;
    moduleEls[name].up.disabled = index === 0;
    moduleEls[name].down.disabled = index === nodes.length - 1;
  });
}

async function moveModule(name, offset) {
  const node = moduleEls[name]?.node;
  if (!node) return;
  const nodes = $$("#modules .module");
  const index = nodes.indexOf(node);
  const destination = index + offset;
  if (destination < 0 || destination >= nodes.length) return;
  const neighbor = nodes[destination];
  if (offset < 0) neighbor.before(node);
  else neighbor.after(node);
  updateModuleOrderControls();
  config.module_order = $$("#modules .module").map((item) =>
    Object.keys(moduleEls).find((key) => moduleEls[key].node === item)
  ).filter(Boolean);
  try {
    config = await call("save_config", config);
    toast("Ordem dos módulos salva");
  } catch (error) {
    toast(error.message || "Não foi possível salvar a ordem", true);
    config = await api.get_config();
    const saved = Array.isArray(config.module_order) ? config.module_order : [];
    const validNames = Object.keys(moduleEls);
    const order = [...saved.filter((key) => validNames.includes(key)), ...validNames.filter((key) => !saved.includes(key))];
    order.forEach((key) => $("#modules").append(moduleEls[key].node));
    updateModuleOrderControls();
  }
}

// ---------- console ----------
function appendLogs(logs) {
  if (!logs.length) return;
  const box = $("#log");
  const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  for (const l of logs) {
    const row = el("div", l.level);
    row.dataset.m = l.module || "system";
    row.append(el("span", "t", l.time));
    if (l.module) row.append(el("span", "tag", `[${moduleEls[l.module]?.label || l.module}]`));
    row.append(l.msg);
    box.append(row);
  }
  while (box.childElementCount > 800) box.firstElementChild.remove();
  applyLogFilter();
  if (stick) box.scrollTop = box.scrollHeight;
}

function applyLogFilter() {
  const f = $("#log").dataset.filter;
  for (const row of $("#log").children) row.hidden = f !== "all" && row.dataset.m !== f;
}

// ---------- estado (polling) ----------
async function poll() {
  try {
    const s = await api.get_state(lastSeq);
    if (s.logs.length) lastSeq = s.logs[s.logs.length - 1].seq;
    appendLogs(s.logs);
    $("#route-record").disabled = Boolean(s.route_recording);
    $("#route-record-stop").disabled = !s.route_recording;

    let count = 0;
    for (const [name, m] of Object.entries(moduleEls)) {
      const running = s.running[name];
      const macroActive = name === "macro" && (s.macro?.playing || s.macro?.recording);
      const routeRecording = name === "cavebot" && s.route_recording;
      const active = running || macroActive || routeRecording;
      if (active) count++;
      m.node.classList.toggle("running", active);
      if (document.activeElement !== m.input) m.input.checked = running;
      if (name === "macro" && s.macro?.recording) {
        m.state.textContent = "Gravando";
        m.text.textContent = `${s.macro.name}: ${s.macro.actions} comandos`;
      } else if (name === "macro" && s.macro?.playing) {
        m.state.textContent = s.macro.paused ? "Pausado" : "Reproduzindo";
        m.text.textContent = s.macro.paused
          ? (s.macro.pause_reasons || []).join(", ") || `Retoma em ${Math.ceil(s.macro.resume_remaining || 0)} s`
          : `${s.macro.loop ? "Em ciclo" : "Executando"}: ${s.macro.name || config.macro.name || "rota gravada"}`;
      } else if (routeRecording) {
        m.state.textContent = "Gravando";
        m.text.textContent = "Registrando pontos do mapa";
      } else {
        m.state.textContent = running ? "Rodando" : "Parado";
        m.text.textContent = s.status[name] || "Aguardando ativação";
      }
      m.text.title = m.text.textContent;
    }
    const pill = $("#status-pill");
    pill.classList.toggle("on", count > 0);
    pill.textContent = count === 0 ? "Parado" : count === 1 ? "1 rodando" : `${count} rodando`;

    const game = $("#h-game");
    game.classList.toggle("ok", s.health.game && s.health.focused);
    game.classList.toggle("warn", s.health.game && !s.health.focused);
    game.textContent = s.health.game && !s.health.focused ? "Jogo (fora de foco)" : "Jogo";
    game.title = !s.health.game
      ? `Janela "${config.window_title}" não encontrada`
      : s.health.focused
        ? "Jogo encontrado e em primeiro plano"
        : "Jogo aberto, mas outra janela está na frente: os módulos ficam pausados";
    const drv = $("#h-driver");
    drv.classList.toggle("ok", s.health.driver);
    drv.title = s.health.driver ? "Driver Interception OK" : "Driver Interception não encontrado";

    renderTimer(s.timer);
    renderMacroState(s.macro);
    handlePick(s.pick);
  } catch (e) {
    console.error(e);
  } finally {
    setTimeout(poll, 300);
  }
}

// ---------- timer ----------
function renderTimer(left) {
  const out = $("#timer-left");
  out.textContent = left == null ? "" : fmtDuration(left);
  $("#timer-desc").textContent = left == null ? "Desliga tudo sozinho." : "Desliga tudo em";
  if (left == null && !timerEditing && $("#timer-select").value && $("#timer-select").value !== "at") {
    $("#timer-select").value = "";
  }
}

function wireTimer() {
  const sel = $("#timer-select");
  const at = $("#timer-at");
  sel.addEventListener("change", async () => {
    at.hidden = sel.value !== "at";
    if (sel.value === "at") {
      timerEditing = true;
      at.focus();
      return;
    }
    timerEditing = false;
    await call("set_timer", sel.value ? Number(sel.value) : null, null);
  });
  at.addEventListener("change", async () => {
    if (!at.value) return;
    timerEditing = false;
    await call("set_timer", null, at.value);
    toast(`Desliga tudo às ${at.value}`);
  });
}

// ---------- calibração com o mouse ----------
async function startPick(kind, target) {
  pickKey = await call("start_pick", kind, target);
  pickHandled = null;
}

async function handlePick(pick) {
  const banner = $("#pick-banner");
  if (!pick) {
    banner.hidden = true;
    return;
  }
  if (pick.cancelled) {
    banner.hidden = true;
    await api.clear_pick();
    toast("Calibração cancelada");
    return;
  }
  if (pick.result) {
    banner.hidden = true;
    if (pickHandled === pick.id) return;
    pickHandled = pick.id;
    await api.clear_pick();
    await applyPick(pick.target, pick.result);
    return;
  }

  banner.hidden = false;
  const targetName = pick.target.startsWith("switch_slot:") ? "switch_slot" : pick.target;
  $("#pick-title").textContent = PICK_TARGETS[targetName] || "Calibrando";
  const key = `<kbd class="k">${pickKey}</kbd>`;
  $("#pick-help").innerHTML =
    pick.kind === "point"
      ? `Vá até o jogo, coloque o mouse em cima do ponto e aperte ${key}.`
      : pick.points.length === 0
        ? `Vá até o jogo, coloque o mouse no <b>canto de cima à esquerda</b> e aperte ${key}.`
        : `Agora o <b>canto de baixo à direita</b> e aperte ${key}.`;
  if (pick.mouse) {
    const [r, g, b] = pick.mouse.rgb;
    $("#pick-xy").textContent = `x ${pick.mouse.x}  y ${pick.mouse.y}`;
    $("#pick-rgb").textContent = `RGB ${r}, ${g}, ${b}`;
    $("#pick-swatch").style.background = `rgb(${r},${g},${b})`;
  }
}

async function applyPick(target, result) {
  if (target.startsWith("switch_slot:")) {
    const slotId = target.slice("switch_slot:".length);
    await call("set_switch_slot_point", slotId, result.x, result.y);
    config = await api.get_config();
    renderSwitchSlots();
    toast("Posição de troca salva");
    return;
  }
  if (target === "new_pokemon") {
    const added = await call("add_pokemon_from_region", pendingPokemonName, result.region);
    await afterPokemonAdded(added);
    return;
  }
  if (target === "new_battle_target") {
    await call("add_battle_target_from_region", pendingBattleTargetName, result.region);
    config = await api.get_config();
    await refreshImages();
    toast(`Alvo "${pendingBattleTargetName}" adicionado`);
    return;
  }
  if (target === "new_battle_empty") {
    await call("set_battle_empty_from_region", result.region);
    config = await api.get_config();
    applyConfig();
    toast("Referência da batalha vazia definida");
    return;
  }
  if (target === "loot.anchor_region") {
    await call("capture_loot_anchor", result.region);
    config = await api.get_config();
    applyConfig();
    toast("Âncora visual do Loot salva");
    return;
  }
  if (target === "loot.first_slot_offset") {
    const windowRegion = config.loot?.window_sample_region || [];
    if (windowRegion.length !== 4) {
      toast("Marque primeiro a janela inteira de loot", true);
      return;
    }
    const input = $(`[data-path="${target}"]`);
    const point = [result.region[0] - windowRegion[0], result.region[1] - windowRegion[1], result.region[2], result.region[3]];
    input.value = point.join(", ");
    await save("Calibração relativa da janela salva");
    return;
  }
  if (target === "new_waypoint") {
    const added = await call("add_waypoint_from_region", result.region);
    config = await api.get_config();
    await refreshImages();
    const row = $(`#route .rt[data-name="${CSS.escape(added)}"]`);
    if (row) {
      $("input[type=checkbox]", row).checked = true;
      row.classList.remove("off");
      const firstOff = $$("#route .rt.off")[0];
      if (firstOff) firstOff.before(row);
      renumberRoute();
    }
    await save(`Ponto ${stem(added)} adicionado à rota`);
    return;
  }
  const input = $(`[data-path="${target}"]`);
  if (result.region) input.value = result.region.join(", ");
  else {
    input.value = `${result.x}, ${result.y}`;
    const colorPath = { "cavebot.hp_pixel": "cavebot.hp_color", "heal.pixel": "heal.color" }[target];
    if (colorPath) $(`[data-path="${colorPath}"]`).value = result.rgb.join(", ");
  }
  config = { ...config, window_ref: (await api.get_config()).window_ref };
  updateSwatches();
  await save(`Salvo: ${PICK_TARGETS[target].toLowerCase()}`);
}
function updateSwatches() {
  for (const sw of $$("[data-swatch]")) {
    const v = $(`[data-path="${sw.dataset.swatch}"]`).value.split(/[,\s]+/).filter(Boolean).map(Number);
    sw.style.background = v.length === 3 && !v.some(Number.isNaN) ? `rgb(${v})` : "transparent";
  }
}

function renderWindowRef() {
  const ref = config.window_ref;
  $("#ref-text").textContent = ref
    ? `As coordenadas acompanham a janela do jogo (referência: canto em ${ref[0]}, ${ref[1]}). Pode mover a janela à vontade.`
    : "Abra o jogo: as coordenadas vão passar a acompanhar a posição da janela.";
}

// ---------- configurações ----------
function fillForm() {
  for (const input of $$("[data-path]")) {
    const v = getPath(config, input.dataset.path);
    if (input.type === "checkbox") input.checked = Boolean(v);
    else input.value = Array.isArray(v) ? v.join(", ") : v ?? "";
  }
  $("#hotkey-label").textContent = config.stop_hotkey;
  for (const [name, m] of Object.entries(moduleEls)) {
    const k = config.hotkeys?.[name];
    m.key.hidden = !k;
    m.key.textContent = k || "";
    m.key.title = k ? `Atalho: ${k}` : "";
  }
  updateSwatches();
  renderWindowRef();
  renderLootGrid();
}

function renderLootGrid() {
  const selected = new Set(config?.loot?.targets || []);
  $$("[data-loot-cell]").forEach((cell) => {
    cell.classList.toggle("selected", selected.has(cell.dataset.lootCell));
    cell.setAttribute("aria-pressed", selected.has(cell.dataset.lootCell) ? "true" : "false");
  });
}

function applyConfig() {
  fillForm();
  for (const t of $$("#capture-grid .thumb")) {
    t.classList.toggle("on", config.capture.targets.includes(t.dataset.name));
    $(".bell", t).classList.toggle("on", config.capture.alert_on.includes(t.dataset.name));
    const keyInput = $(".poke-key", t);
    if (keyInput) keyInput.value = config.capture.keys?.[t.dataset.name] || "";
  }
  for (const target of $$("#battle-target-grid .thumb")) {
    target.classList.toggle("on", (config.battle.pause_targets || []).includes(target.dataset.name));
  }
  buildRoute();
  renderSwitchSlots();
  renderProfiles();
}

function readForm() {
  const next = structuredClone(config);
  for (const input of $$("[data-path]")) {
    let v = input.value.trim();
    const type = input.dataset.type || input.type;
    if (type === "checkbox") v = input.checked;
    else if (type === "number") v = Number(v);
    else if (type === "list") v = v.split(",").map((s) => s.trim()).filter(Boolean);
    else if (type === "numlist") v = v.split(/[,\s]+/).filter(Boolean).map(Number);
    setPath(next, input.dataset.path, v);
  }
  next.capture.targets = $$("#capture-grid .thumb.on").map((t) => t.dataset.name);
  next.capture.alert_on = $$("#capture-grid .bell.on").map((b) => b.closest(".thumb").dataset.name);
  next.capture.keys = Object.fromEntries($$("#capture-grid .poke-key").map((input) => [input.dataset.pokemon, input.value.trim()]).filter(([, key]) => key));
  next.battle.pause_targets = $$("#battle-target-grid .thumb.on").map((target) => target.dataset.name);
  next.cavebot.route = readRoute();
  next.loot.targets = $$("[data-loot-cell].selected").map((cell) => cell.dataset.lootCell);
  return next;
}

function validate() {
  const engageDelay = Number($('[data-path="cavebot.engage_delay"]').value);
  if (!Number.isFinite(engageDelay) || engageDelay < 0 || engageDelay > 3600) {
    toast("O atraso após clicar no mapa deve ficar entre 0 e 3600 segundos", true);
    $('[data-path="cavebot.engage_delay"]').focus();
    return false;
  }
  const cavebotBattleSkipAfter = Number($('[data-path="cavebot.battle_skip_after"]').value);
  if (!Number.isFinite(cavebotBattleSkipAfter) || cavebotBattleSkipAfter < 0 || cavebotBattleSkipAfter > 3600) {
    toast("O limite para ignorar a batalha no Cavebot deve ficar entre 0 e 3600 segundos", true);
    $('[data-path="cavebot.battle_skip_after"]').focus();
    return false;
  }
  const longBattleAfter = Number($('[data-path="battle.long_battle_after"]').value);
  const longBattleEnabled = $('[data-path="battle.long_battle_enabled"]').checked;
  if (longBattleEnabled && (!Number.isFinite(longBattleAfter) || longBattleAfter < 1 || longBattleAfter > 3600)) {
    toast("O tempo preso em combate deve ficar entre 1 e 3600 segundos", true);
    $('[data-path="battle.long_battle_after"]').focus();
    return false;
  }
  const autoTargetEnabled = $('[data-path="battle.auto_target_enabled"]').checked;
  const autoTargetKey = $('[data-path="battle.auto_target_key"]').value.trim().toLowerCase();
  const validActionKeys = new Set([
    ..."abcdefghijklmnopqrstuvwxyz0123456789",
    ...FKEYS.filter((key) => key !== "F8").map((key) => key.toLowerCase()),
    "backspace", "tab", "enter", "esc", "space", "left", "up", "right", "down",
  ]);
  if (autoTargetEnabled && !validActionKeys.has(autoTargetKey)) {
    toast("Configure uma tecla válida para o auto target", true);
    $('[data-path="battle.auto_target_key"]').focus();
    return false;
  }
  const followEnabled = $('[data-path="heal.follow_enabled"]').checked;
  const followKey = $('[data-path="heal.follow_key"]').value.trim().toLowerCase();
  const followInterval = Number($('[data-path="heal.follow_interval"]').value);
  if (followEnabled && (!validActionKeys.has(followKey) || !Number.isFinite(followInterval) || followInterval < 0.1 || followInterval > 3600)) {
    toast("Configure uma tecla válida e um intervalo entre 0,1 e 3600 segundos para o acompanhamento", true);
    $('[data-path="heal.follow_key"]').focus();
    return false;
  }
  const orderEnabled = $('[data-path="heal.order_enabled"]').checked;
  const orderKey = $('[data-path="heal.order_key"]').value.trim().toLowerCase();
  const orderInterval = Number($('[data-path="heal.order_interval"]').value);
  const orderPoint = $('[data-path="heal.order_point"]').value.split(/[,\s]+/).filter(Boolean).map(Number);
  if (orderEnabled && (!validActionKeys.has(orderKey) || orderPoint.length !== 2 || orderPoint.some((value) => !Number.isFinite(value)) || !Number.isFinite(orderInterval) || orderInterval < 0.1 || orderInterval > 3600)) {
    toast("Configure tecla, ponto e intervalo entre 0,1 e 3600 segundos para a Order", true);
    $('[data-path="heal.order_key"]').focus();
    return false;
  }
  for (const input of $$("[data-type=numlist]")) {
    const nums = input.value.split(/[,\s]+/).filter(Boolean).map(Number);
    const want = Number(input.dataset.len);
    if (!nums.length && "optional" in input.dataset) continue;
    if (nums.length !== want || nums.some(Number.isNaN)) {
      toast(`Preencha ${want} números separados por vírgula`, true);
      $$(".tab").find((t) => t.dataset.tab === "settings").click();
      input.focus();
      return false;
    }
  }
  const keys = $$("select.hk").map((s) => s.value).filter(Boolean);
  if (new Set(keys).size !== keys.length) {
    toast("Dois atalhos estão com a mesma tecla", true);
    return false;
  }
  return true;
}

async function save(message = "Salvo") {
  if (!validate()) return false;
  config = await call("save_config", readForm());
  fillForm();
  toast(message);
  return true;
}

// ---------- captura ----------
function buildCaptureGrid() {
  const grid = $("#capture-grid");
  const had = grid.childElementCount > 0;
  const selected = new Set(had ? $$(".thumb.on", grid).map((t) => t.dataset.name) : config.capture.targets);
  const belled = new Set(had ? $$(".bell.on", grid).map((b) => b.closest(".thumb").dataset.name) : config.capture.alert_on);
  grid.replaceChildren();
  for (const img of images.capture) {
    const name = pretty(img.name);
    const t = el("div", "thumb");
    t.tabIndex = 0;
    t.setAttribute("role", "button");
    t.setAttribute("aria-label", `Capturar ${name}`);
    t.dataset.name = img.name;
    t.classList.toggle("on", selected.has(img.name));
    const pic = el("img");
    pic.src = img.src;
    pic.alt = "";

    const rm = el("button", "rm", "✕");
    rm.title = `Remover ${name}`;
    rm.setAttribute("aria-label", rm.title);
    rm.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (!confirm(`Remover "${name}"? A imagem vai para imags/captura/removidos.`)) return;
      await call("remove_pokemon", img.name);
      config = await api.get_config();
      await refreshImages();
    });

    const bell = el("button", "bell" + (belled.has(img.name) ? " on" : ""), "🔔");
    bell.title = `Avisar com som quando ${name} aparecer`;
    bell.setAttribute("aria-label", bell.title);
    bell.addEventListener("click", (ev) => {
      ev.stopPropagation();
      bell.classList.toggle("on");
    });

    const keyLabel = el("label", "thumb-key-label", "Tecla de captura");
    const keyInput = el("input", "thumb-key poke-key");
    keyInput.type = "text";
    keyInput.maxLength = 3;
    keyInput.placeholder = config.capture.key || "1";
    keyInput.value = config.capture.keys?.[img.name] || "";
    keyInput.dataset.pokemon = img.name;
    keyInput.title = `Tecla para capturar ${name}; vazio usa ${config.capture.key || "1"}`;
    keyInput.addEventListener("click", (ev) => ev.stopPropagation());
    keyInput.addEventListener("keydown", (ev) => ev.stopPropagation());
    keyLabel.addEventListener("click", (ev) => ev.stopPropagation());
    keyLabel.append(keyInput);

    t.append(rm, pic, el("span", "n", name), bell, keyLabel);
    const toggle = () => t.classList.toggle("on");
    t.addEventListener("click", toggle);
    t.addEventListener("keydown", (ev) => (ev.key === "Enter" || ev.key === " ") && ev.target === t && (ev.preventDefault(), toggle()));
    grid.append(t);
  }
  if (!images.capture.length) grid.append(el("p", "hint", "Nenhuma imagem em imags/captura."));
}

function buildBattleTargetGrid() {
  const grid = $("#battle-target-grid");
  grid.replaceChildren();
  const selected = new Set(config.battle.pause_targets || []);
  for (const target of images.battle_targets || []) {
    const card = el("div", "thumb");
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `Usar ${pretty(target.name)} como alvo de pausa`);
    card.dataset.name = target.name;
    card.classList.toggle("on", selected.has(target.name));
    const image = el("img");
    image.src = target.src;
    image.alt = "";
    const remove = el("button", "rm", "×");
    remove.type = "button";
    remove.title = `Remover ${pretty(target.name)}`;
    remove.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!confirm(`Remover o alvo "${pretty(target.name)}"?`)) return;
      await call("remove_battle_target", target.name);
      config = await api.get_config();
      await refreshImages();
    });
    card.append(remove, image, el("span", "n", pretty(target.name)));
    card.addEventListener("click", () => card.classList.toggle("on"));
    card.addEventListener("keydown", (event) => {
      if ((event.key === "Enter" || event.key === " ") && event.target === card) {
        event.preventDefault();
        card.classList.toggle("on");
      }
    });
    grid.append(card);
  }
  if (!images.battle_targets?.length) grid.append(el("p", "hint", "Nenhum Pokémon cadastrado para a lista de combate."));
}

function openAddBattleTarget() {
  const form = el("div", "add-form");
  const fields = el("div", "fields");
  const label = el("label", null, "Nome do Pokémon");
  const name = el("input");
  name.type = "text";
  name.maxLength = 40;
  name.placeholder = "ex.: pikachu";
  label.append(name);
  fields.append(label);
  const help = el("p", "hint", `Deixe o Pokémon visível na lista de combate, clique em recortar e marque somente o ícone ou nome com ${pickKey}.`);
  const start = el("button", "btn primary", "Recortar da lista");
  start.addEventListener("click", async () => {
    if (!name.value.trim()) {
      toast("Digite um nome para o Pokémon", true);
      name.focus();
      return;
    }
    pendingBattleTargetName = name.value.trim();
    closeModal();
    await startPick("region", "new_battle_target");
  });
  form.append(fields, help, start);
  openModal("Adicionar Pokémon da lista de combate", form);
  setTimeout(() => name.focus(), 50);
}

async function refreshImages() {
  images = await call("get_images");
  buildCaptureGrid();
  buildBattleTargetGrid();
  buildRoute();
}

function openAddPokemon() {
  const form = el("div", "add-form");
  const wrap = el("div", "fields");
  wrap.style.marginBottom = "0";
  const lab = el("label", null, "Nome do Pokémon");
  const name = el("input");
  name.type = "text";
  name.placeholder = "ex.: croa";
  lab.append(name);
  wrap.append(lab);

  const fromFile = el("button", "btn", "Escolher arquivo…");
  const fromScreen = el("button", "btn primary", "Recortar da tela");
  const row = el("div", "row-gap");
  row.append(fromScreen, fromFile);

  const help = el("p", "hint");
  help.innerHTML = `<b>Recortar da tela</b>: deixe o Pokémon visível no jogo, clique no botão e marque o canto superior esquerdo e depois o canto inferior direito com <kbd class="k">${pickKey}</kbd>. Faça um recorte justo, contendo apenas o Pokémon.`;

  fromFile.addEventListener("click", async () => {
    const added = await call("add_pokemon_from_file", name.value.trim());
    if (added) {
      closeModal();
      await afterPokemonAdded(added);
    }
  });
  fromScreen.addEventListener("click", async () => {
    if (!name.value.trim()) {
      toast("Dê um nome antes de recortar", true);
      name.focus();
      return;
    }
    pendingPokemonName = name.value.trim();
    closeModal();
    await startPick("region", "new_pokemon");
  });

  form.append(wrap, row, help);
  openModal("Adicionar Pokémon", form);
  setTimeout(() => name.focus(), 50);
}

async function afterPokemonAdded(fileName) {
  await refreshImages();
  const thumb = $(`#capture-grid .thumb[data-name="${CSS.escape(fileName)}"]`);
  thumb?.classList.add("on");
  thumb?.scrollIntoView({ block: "nearest" });
  await save(`"${pretty(fileName)}" adicionado e marcado`);
}

function openAddWaypoint() {
  const body = el("div", "add-form");
  const help = el("p", "hint");
  help.innerHTML = `Deixe o minimapa visível no jogo, clique em <b>Recortar</b> e marque com <kbd class="k">${pickKey}</kbd> o canto de cima à esquerda e o de baixo à direita do marcador. Recorte só o ícone, bem justo. Ele entra no fim da rota com o próximo número.`;
  const go = el("button", "btn primary", "Recortar do minimapa");
  go.addEventListener("click", async () => {
    closeModal();
    await startPick("region", "new_waypoint");
  });
  body.append(help, go);
  openModal("Adicionar ponto da rota", body);
}

// ---------- rota ----------
function buildRoute() {
  const list = $("#route");
  const route = config.cavebot.route;
  const def = config.cavebot.walk_time;
  const all = images.waypoints;
  const byName = Object.fromEntries(all.map((w) => [w.name, w]));
  const enabled = route.length ? route.filter((r) => byName[r.name]) : all.map((w) => ({ name: w.name, time: def }));
  const enabledNames = new Set(enabled.map((r) => r.name));
  const rows = [
    ...enabled.map((r) => ({ ...r, on: true })),
    ...all.filter((w) => !enabledNames.has(w.name)).map((w) => ({ name: w.name, time: def, on: false })),
  ];

  list.replaceChildren();
  for (const r of rows) {
    const li = el("li", "rt");
    li.draggable = true;
    li.dataset.name = r.name;
    li.classList.toggle("off", !r.on);

    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = r.on;
    cb.setAttribute("aria-label", `Usar ponto ${stem(r.name)}`);
    cb.addEventListener("change", () => {
      li.classList.toggle("off", !cb.checked);
      renumberRoute();
    });

    const pic = el("img");
    pic.src = byName[r.name].src;
    pic.alt = "";

    const nm = el("span", "nm", `Ponto ${stem(r.name)}`);
    nm.append(el("span", "order"));

    const tm = el("label", "tm");
    const time = el("input");
    time.type = "number";
    time.min = "1";
    time.step = "0.5";
    time.value = r.time;
    time.setAttribute("aria-label", `Tempo de caminhada até o ponto ${stem(r.name)}`);
    tm.append(time, "s");

    const mv = el("span", "mv");
    const up = el("button", null, "↑");
    const down = el("button", null, "↓");
    up.title = "Subir";
    down.title = "Descer";
    up.addEventListener("click", () => li.previousElementSibling && (li.previousElementSibling.before(li), renumberRoute()));
    down.addEventListener("click", () => li.nextElementSibling && (li.nextElementSibling.after(li), renumberRoute()));
    mv.append(up, down);

    const rm = el("button", "rm-wp", "✕");
    rm.title = `Remover ponto ${stem(r.name)}`;
    rm.setAttribute("aria-label", rm.title);
    rm.addEventListener("click", async () => {
      if (!confirm(`Remover o ponto ${stem(r.name)}? A imagem vai para imags/map/removidos.`)) return;
      await call("remove_waypoint", r.name);
      config = await api.get_config();
      await refreshImages();
    });

    li.append(el("span", "grip", "⋮⋮"), cb, pic, nm, tm, mv, rm);
    list.append(li);
  }
  if (!rows.length) list.append(el("p", "hint", "Nenhum ponto em imags/map. Use + Adicionar ponto."));
  renumberRoute();
}

function renumberRoute() {
  let i = 0;
  for (const li of $$("#route .rt")) {
    const on = $("input[type=checkbox]", li).checked;
    $(".order", li).textContent = on ? `#${++i}` : "desligado";
  }
}

function readRoute() {
  return $$("#route .rt")
    .filter((li) => $("input[type=checkbox]", li).checked)
    .map((li) => ({ name: li.dataset.name, time: Number($("input[type=number]", li).value) || config.cavebot.walk_time }));
}

function wireRouteDnD() {
  const list = $("#route");
  let dragging = null;
  const clearMarks = () => $$(".drop-before, .drop-after", list).forEach((n) => n.classList.remove("drop-before", "drop-after"));
  list.addEventListener("dragstart", (e) => {
    dragging = e.target.closest(".rt");
    dragging?.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
  });
  list.addEventListener("dragover", (e) => {
    const over = e.target.closest(".rt");
    if (!dragging || !over || over === dragging) return;
    e.preventDefault();
    clearMarks();
    const r = over.getBoundingClientRect();
    over.classList.add(e.clientY < r.top + r.height / 2 ? "drop-before" : "drop-after");
  });
  list.addEventListener("drop", (e) => {
    e.preventDefault();
    const over = e.target.closest(".rt");
    if (dragging && over && over !== dragging) {
      over.classList.contains("drop-before") ? over.before(dragging) : over.after(dragging);
      renumberRoute();
    }
    clearMarks();
  });
  list.addEventListener("dragend", () => {
    dragging?.classList.remove("dragging");
    dragging = null;
    clearMarks();
  });
}

// ---------- testes de detecção ----------
const TEST_TITLES = { capture: "Teste de captura", map: "Teste do minimapa", battle: "Teste da batalha", pairing: "Teste do minigame" };

async function runTest(kind) {
  if (!(await save("Configurações salvas, testando…"))) return;
  openModal(TEST_TITLES[kind], el("p", "hint", "Tirando um print e procurando as imagens…"));
  let r;
  try {
    r = await call("test_detection", kind);
  } catch {
    closeModal();
    return;
  }
  const body = el("div");

  if (r.offset && (r.offset[0] || r.offset[1])) {
    body.append(el("div", "callout", `A janela do jogo está deslocada ${r.offset[0]}, ${r.offset[1]} px da referência; as áreas foram ajustadas.`));
  }
  if (kind === "battle") {
    const empty = r.results[0]?.found;
    body.append(
      el(
        "div",
        "callout",
        empty
          ? "A janela de batalha está vazia (sem inimigos). Se tiver inimigo agora, a precisão está baixa demais."
          : "Não achei a batalha vazia, então o bot acha que tem inimigo. Se não tiver, abaixe a precisão ou atualize imags/battle/batalha_vazia.png.",
      ),
    );
    body.append(el(
      "div",
      "callout",
      r.target_detected
        ? "Alvo cadastrado encontrado na lista. Essa detecção é independente do estado de lista vazia."
        : "Nenhum alvo cadastrado foi encontrado na região da lista de combate.",
    ));
    if (r.extra) {
      const hp = el("div", "callout");
      const sw = el("span", "swatch");
      sw.style.background = `rgb(${r.extra.rgb})`;
      hp.append(sw, `Pixel da vida (${r.extra.hp_pixel.join(", ")}) está com RGB ${r.extra.rgb.join(", ")}: ${r.extra.matches ? "igual à cor configurada (inimigo vivo)." : "diferente da cor configurada."}`);
      body.append(hp);
    }
  }

  if (kind === "pairing") {
    const message = r.active
      ? `Minigame detectado. Prévia sem cliques: ${r.pairs.map(([top, bottom]) => `superior ${top} → inferior ${bottom}`).join("; ")}. Confira as linhas antes de ativar o módulo.`
      : `Minigame não detectado nesta captura (nota ${r.presence_score.toFixed(2)}). Confira se imags/map/areaminigame.png mostra o cabeçalho e o rodapé do painel.`;
    body.append(el("div", "callout", message));
  }

  const img = el("img", "shot");
  img.src = r.image;
  img.alt = "Print da tela com as detecções marcadas";
  body.append(img);

  const list = el("div", "results");
  for (const x of r.results) {
    if (kind === "pairing") {
      const row = el("div", "res ok");
      row.append(el("span", null, x.name), el("span", "sc", x.score.toFixed(2)), el("span", "lb", "similaridade cosseno"));
      list.append(row);
      continue;
    }
    const row = el("div", "res" + (x.found ? " ok" : ""));
    const track = el("div", "track");
    const fill = el("div", "fill");
    fill.style.width = `${Math.max(0, x.score) * 100}%`;
    const th = el("div", "th");
    th.style.left = `${r.confidence * 100}%`;
    track.append(fill, th);
    const label = x.missing ? "arquivo faltando" : x.found ? `achou em ${x.x}, ${x.y}` : "não achou";
    row.append(el("span", null, kind === "map" ? `Ponto ${x.name}` : pretty(x.name)), track, el("span", "sc", x.score.toFixed(2)), el("span", "lb", label));
    list.append(row);
  }
  body.append(list);
  if (kind !== "pairing") {
    const legend = el("p", "legend");
    legend.innerHTML = `Barra = semelhança com a imagem salva. <i></i> = precisão configurada (${r.confidence}). Se o certo ficar logo abaixo da linha, diminua um pouco a precisão; se aparecer coisa errada acima dela, aumente.`;
    body.append(legend);
  }
  openModal(TEST_TITLES[kind], body);
}

// ---------- estatísticas ----------
const SVG = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};

function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v);
}

// Barras de uma série só, eixo único começando no zero, dica ao passar o mouse.
function barChart(box, data, { value, format, label }) {
  const W = box.clientWidth || 360;
  const H = box.clientHeight || 150;
  const pad = { l: 34, r: 4, t: 8, b: 20 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const max = niceMax(Math.max(...data.map(value)));
  const step = iw / data.length;
  const bw = Math.max(4, Math.min(22, step - 4));

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });
  const grid = svgEl("g", { class: "grid" });
  for (const f of [0, 0.5, 1]) {
    const y = pad.t + ih - f * ih;
    grid.append(svgEl("line", { x1: pad.l, x2: W - pad.r, y1: y, y2: y }));
    const t = svgEl("text", { x: pad.l - 6, y: y + 3, "text-anchor": "end" });
    t.textContent = format(max * f, true);
    grid.append(t);
  }
  svg.append(grid);

  const axis = svgEl("g", { class: "axis" });
  const tip = $("#chart-tip");
  data.forEach((d, i) => {
    const v = value(d);
    const h = (v / max) * ih;
    const x = pad.l + i * step + (step - bw) / 2;
    const y = pad.t + ih - h;
    const hit = svgEl("rect", { class: "hit", x: pad.l + i * step, y: pad.t, width: step, height: ih });
    // topo arredondado, base reta apoiada no eixo
    const r = Math.min(4, bw / 2, h);
    const shape = h > 0 ? `M${x},${pad.t + ih} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${pad.t + ih} Z` : "";
    const bar = svgEl("path", { class: "bar" + (i === data.length - 1 ? " today" : ""), d: shape });
    hit.addEventListener("mousemove", (e) => {
      tip.hidden = false;
      tip.innerHTML = "";
      tip.append(Object.assign(el("b"), { textContent: format(v) }), label(d));
      tip.style.left = `${Math.min(e.clientX + 12, window.innerWidth - tip.offsetWidth - 8)}px`;
      tip.style.top = `${e.clientY - tip.offsetHeight - 10}px`;
    });
    hit.addEventListener("mouseleave", () => (tip.hidden = true));
    svg.append(hit, bar);
    if (i % 2 === (data.length - 1) % 2) {
      const t = svgEl("text", { x: x + bw / 2, y: H - 6, "text-anchor": "middle" });
      t.textContent = d.day.slice(8, 10);
      axis.append(t);
    }
  });
  svg.append(axis);
  box.replaceChildren(svg);
}

const dayLabel = (d) => {
  const [y, m, day] = d.day.split("-");
  const dt = new Date(+y, +m - 1, +day);
  return dt.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
};

let statsTimer = null;
async function refreshStats() {
  const s = await api.get_stats();
  const run = s.session.run_seconds;
  const totalRun = Object.values(run).reduce((a, b) => a + b, 0);
  const sessionBalls = Object.values(s.session.balls).reduce((a, b) => a + b, 0);
  const totalBalls = Object.values(s.total.balls).reduce((a, b) => a + b, 0);
  $("#k-time").textContent = fmtDuration(totalRun);
  $("#k-kills").textContent = s.session.kills;
  $("#k-balls").textContent = sessionBalls;
  $("#k-heals").textContent = s.session.heals;
  $("#k-total").textContent = totalBalls;

  const ballsBox = $("#chart-balls");
  barChart(ballsBox, s.daily, { value: (d) => d.balls, format: (v) => String(Math.round(v)), label: dayLabel });
  ballsBox.setAttribute("aria-label", "Pokébolas por dia: " + s.daily.map((d) => `${dayLabel(d)} ${d.balls}`).join(", "));
  const timeBox = $("#chart-time");
  barChart(timeBox, s.daily, {
    value: (d) => d.seconds / 3600,
    format: (v, axis) => (axis ? `${+v.toFixed(1)}h` : fmtDuration(v * 3600)),
    label: dayLabel,
  });
  timeBox.setAttribute("aria-label", "Tempo ligado por dia: " + s.daily.map((d) => `${dayLabel(d)} ${fmtDuration(d.seconds)}`).join(", "));

  const bars = $("#s-modules");
  bars.replaceChildren();
  const max = Math.max(1, ...Object.values(run));
  for (const [name, sec] of Object.entries(run)) {
    const row = el("div", "bar-row");
    const track = el("div", "track");
    const fill = el("div", "fill");
    fill.style.width = `${(sec / max) * 100}%`;
    track.append(fill);
    row.append(el("span", null, moduleEls[name]?.label || name), track, el("span", "v", fmtDuration(sec)));
    bars.append(row);
  }

  const body = $("#s-pokes");
  body.replaceChildren();
  const names = [...new Set([...Object.keys(s.total.balls), ...Object.keys(s.session.balls)])].sort(
    (a, b) => (s.total.balls[b] || 0) - (s.total.balls[a] || 0),
  );
  for (const n of names) {
    const tr = el("tr");
    tr.append(el("td", null, pretty(n)), el("td", "num", s.session.balls[n] || 0), el("td", "num", s.total.balls[n] || 0));
    body.append(tr);
  }
  if (!names.length) {
    const tr = el("tr");
    const td = el("td", "empty", "Nenhuma pokébola jogada ainda.");
    td.colSpan = 3;
    tr.append(td);
    body.append(tr);
  }
}

// ---------- perfis ----------
async function renderProfiles() {
  const names = await api.list_profiles();
  const sel = $("#profile-select");
  sel.replaceChildren();
  const none = el("option", null, names.length ? "Sem perfil" : "Nenhum perfil salvo");
  none.value = "";
  sel.append(none, ...names.map((n) => Object.assign(el("option", null, n), { value: n })));
  sel.value = names.includes(config.profile) ? config.profile : "";
  $("#profile-name").value = config.profile || "";
  $("#profile-delete").disabled = !config.profile;
}

// ---------- versão ----------
async function checkVersion() {
  const v = await api.get_version();
  $("#version").textContent = `Versão ${v.current}`;
  if (v.update) {
    $("#update-version").textContent = v.update.version;
    $("#update-banner").hidden = false;
  }
}

// ---------- abas e controles ----------
function wireUi() {
  for (const tab of $$(".tab")) {
    tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      $$(".tab-body").forEach((b) => b.classList.toggle("active", b.id === `tab-${tab.dataset.tab}`));
      clearInterval(statsTimer);
      if (tab.dataset.tab === "stats") {
        refreshStats();
        statsTimer = setInterval(refreshStats, 3000);
      }
    });
  }

  $$(".save").forEach((b) => b.addEventListener("click", () => save()));
  $("#stop-all").addEventListener("click", () => api.stop_all());
  $("#loot-window-test").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const found = await call("locate_loot_window");
      if (!found) toast("Janela de loot não encontrada na tela", true);
      else {
        const first = found.first_slot_region
          ? ` | 1o slot: ${found.first_slot_region[0]}, ${found.first_slot_region[1]}`
          : " | configure o primeiro slot";
        toast(`Janela encontrada em ${found.region[0]}, ${found.region[1]}${first} (confiança ${found.anchor_score.toFixed(2)})`);
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
  $("#loot-empty-slot-capture").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await call("capture_loot_empty_slot");
      toast("Referencia do slot vazio capturada");
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
  $$('[data-loot-cell]').forEach((cell) => cell.addEventListener("click", async () => {
    cell.classList.toggle("selected");
    if (!await save("Tiles de loot salvos")) renderLootGrid();
  }));
  $("#clear-logs").addEventListener("click", () => $("#log").replaceChildren());
  $("#open-logs").addEventListener("click", () => call("open_logs_folder"));

  $("#log-filter").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    $$("#log-filter .chip").forEach((c) => c.classList.toggle("on", c === chip));
    $("#log").dataset.filter = chip.dataset.f;
    applyLogFilter();
    $("#log").scrollTop = $("#log").scrollHeight;
  });

  $$("[data-test]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await runTest(b.dataset.test);
      } finally {
        b.disabled = false;
      }
    }),
  );
  $$("[data-pick]").forEach((b) => b.addEventListener("click", () => startPick(b.dataset.pick, b.dataset.target)));
  $("#pick-cancel").addEventListener("click", () => api.cancel_pick());
  $$("[data-swatch]").forEach((sw) => $(`[data-path="${sw.dataset.swatch}"]`).addEventListener("input", updateSwatches));
  $("#add-poke").addEventListener("click", openAddPokemon);
  $("#battle-target-add").addEventListener("click", openAddBattleTarget);
  $("#battle-empty-pick").addEventListener("click", () => startPick("region", "new_battle_empty"));
  $("#add-waypoint").addEventListener("click", openAddWaypoint);
  $('[data-path="battle.long_battle_enabled"]').addEventListener("change", async (event) => {
    if (event.currentTarget.checked && Number($('[data-path="battle.long_battle_after"]').value) < 1) {
      $('[data-path="battle.long_battle_after"]').value = "30";
    }
    if (!await save("Ação extra atualizada")) {
      event.currentTarget.checked = Boolean(config.battle.long_battle_enabled);
    }
  });
  $('[data-path="battle.auto_target_enabled"]').addEventListener("change", async (event) => {
    if (!await save("Auto target atualizado")) {
      event.currentTarget.checked = Boolean(config.battle.auto_target_enabled);
    }
  });
  $('[data-path="heal.order_enabled"]').addEventListener("change", async (event) => {
    if (!await save("Ordem de pesca atualizada")) event.currentTarget.checked = Boolean(config.heal.order_enabled);
  });
  $('[data-path="heal.follow_enabled"]').addEventListener("change", async (event) => {
    if (!await save("Acompanhamento atualizado")) {
      event.currentTarget.checked = Boolean(config.heal.follow_enabled);
    }
  });
  $$('[data-path="battle.long_battle_after"], [data-path="battle.long_battle_key"], [data-path="battle.long_battle_repeats"], [data-path="battle.auto_target_key"]').forEach((input) => {
    input.addEventListener("change", () => save("Configuração de batalha salva"));
  });
  $$('[data-path="heal.follow_key"], [data-path="heal.follow_interval"]').forEach((input) => {
    input.addEventListener("change", () => save("Configuração de acompanhamento salva"));
  });
  $$('[data-path="heal.order_key"], [data-path="heal.order_interval"], [data-path="heal.order_point"]').forEach((input) => {
    input.addEventListener("change", () => save("Configura??o da Order salva"));
  });
  $("#route-record").addEventListener("click", async () => {
    if (!confirm("A gravação substituirá a sequência atual da rota. Clique nos marcadores do minimapa em ordem; depois pressione Parar gravação.")) return;
    try {
      await call("start_route_recording");
      toast("Gravação de rota iniciada. Clique nos pontos do minimapa.");
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#route-record-stop").addEventListener("click", async () => {
    await call("stop_route_recording");
    config = await api.get_config();
    await refreshImages();
    applyConfig();
    toast("Gravação de rota encerrada.");
  });
  $("#macro-select").addEventListener("change", async () => {
    if ($("#macro-select").value) $("#macro-name").value = $("#macro-select").value;
    await call("configure_macro_module", $("#macro-select").value, $("#macro-loop").checked).catch((error) => toast(error.message, true));
    config.macro.name = $("#macro-select").value;
    await loadMacroOptions();
  });
  $("#macro-loop").addEventListener("change", async () => {
    await call("configure_macro_module", $("#macro-select").value, $("#macro-loop").checked).catch((error) => toast(error.message, true));
    config.macro.loop = $("#macro-loop").checked;
  });
  $$('[data-macro-pause]').forEach((button) => button.addEventListener("click", async () => {
    button.dataset.enabled = button.dataset.enabled !== "true" ? "true" : "false";
    updateMacroPauseButtons();
    await saveMacroOptions();
  }));
  $$('[data-macro-delay]').forEach((input) => input.addEventListener("change", saveMacroOptions));
  $('[data-macro-command-interval]').addEventListener("change", saveMacroOptions);
  $('[data-macro-skip-battle-after]').addEventListener("change", saveMacroOptions);
  $('[data-path="cavebot.battle_skip_after"]').addEventListener("change", () => save("Tempo limite do Cavebot salvo"));
  $("#macro-options-save").addEventListener("click", () => saveMacroOptions());
  $("#macro-record").addEventListener("click", async () => {
    const name = $("#macro-name").value.trim();
    const duration = Number($("#macro-duration").value);
    if (!name) return toast("Digite um nome para a macro", true);
    const normalized = name.toLowerCase().replace(/[^\p{L}\p{N}_ -]/gu, "").trim().replace(/\s+/g, "_") || "pokemon";
    const existing = await call("list_macros").catch(() => []);
    if (existing.includes(normalized) && !confirm(`A macro "${normalized}" já existe. Substituir?`)) return;
    await call("start_macro_recording", name, duration, true, 0, collectMacroOptions(), $("#macro-arrows-only").checked);
    toast("Gravação iniciada; execute as ações no jogo");
  });
  $("#macro-stop-record").addEventListener("click", async () => {
    const state = await call("stop_macro_recording");
    await renderMacroList(state.name);
  });
  $("#macro-play").addEventListener("click", async () => {
    const name = $("#macro-select").value;
    if (!name) return toast("Selecione uma macro", true);
    if (!await saveMacroOptions(false)) return;
    await call("play_macro", name, $("#macro-loop").checked);
    toast(`Reproduzindo ${name}`);
  });
  $("#macro-stop-play").addEventListener("click", () => call("stop_macro_playback"));
  $("#macro-delete").addEventListener("click", async () => {
    const name = $("#macro-select").value;
    if (!name) return toast("Selecione uma macro", true);
    if (!confirm(`Excluir a macro "${name}" permanentemente?`)) return;
    await call("delete_macro", name);
    await renderMacroList();
    if ($("#macro-name").value === name) $("#macro-name").value = "";
    toast(`Macro "${name}" excluída`);
  });
  $("#switch-add").addEventListener("click", async () => {
    const name = $("#switch-name").value.trim();
    if (!name) return toast("Digite um nome para a posição", true);
    const slot = await call("add_switch_slot", name);
    config = await api.get_config();
    $("#switch-name").value = "";
    renderSwitchSlots();
    await startPick("point", `switch_slot:${slot.id}`);
  });
  $("#ref-set").addEventListener("click", async () => {
    config = await call("set_window_ref");
    renderWindowRef();
    toast("Referência atualizada");
  });

  $("#modal-close").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => e.target.id === "modal" && closeModal());
  document.addEventListener("keydown", (e) => e.key === "Escape" && !$("#modal").hidden && closeModal());

  $("#reset-stats").addEventListener("click", async () => {
    if (!confirm("Apagar todo o histórico de estatísticas?")) return;
    await call("reset_stats");
    refreshStats();
  });

  $("#update-open").addEventListener("click", () => api.open_update());
  $("#update-close").addEventListener("click", () => ($("#update-banner").hidden = true));

  // perfis
  $("#profile-select").addEventListener("change", async (e) => {
    const name = e.target.value;
    if (!name) return;
    config = await call("load_profile", name);
    applyConfig();
    toast(`Perfil "${name}" carregado`);
  });
  $("#profile-save").addEventListener("click", async () => {
    const name = $("#profile-name").value.trim();
    if (!name) {
      toast("Digite um nome para o perfil", true);
      $("#profile-name").focus();
      return;
    }
    if (!validate()) return;
    config = await call("save_profile", name, readForm());
    await renderProfiles();
    toast(`Perfil "${name}" salvo`);
  });
  $("#profile-delete").addEventListener("click", async () => {
    if (!config.profile || !confirm(`Excluir o perfil "${config.profile}"?`)) return;
    config = await call("delete_profile", config.profile);
    await renderProfiles();
    toast("Perfil excluído");
  });

  // modo compacto
  $("#compact-btn").addEventListener("click", async () => {
    const compact = !document.body.classList.contains("compact");
    document.body.classList.toggle("compact", compact);
    $("#compact-btn").title = compact ? "Voltar ao painel completo" : "Modo compacto (fica por cima do jogo)";
    $("#compact-btn").setAttribute("aria-label", $("#compact-btn").title);
    $("#compact-btn path").setAttribute("d", compact ? "M9 9H3V7h4V3h2zM11 11h6v2h-4v4h-2z" : "M3 3h6v2H5v4H3zM17 17h-6v-2h4v-4h2z");
    await call("set_compact", compact).catch(() => {});
  });

  // opacidade
  const range = $("#opacity");
  const out = $("#opacity-value");
  let timer;
  range.addEventListener("input", () => {
    out.textContent = `${range.value}%`;
    clearTimeout(timer);
    timer = setTimeout(() => api.set_opacity(Math.round((range.value / 100) * 255)), 250);
  });

  wireTimer();
}

async function renderMacroList(selected) {
  const select = $("#macro-select");
  const current = selected ?? select.value;
  const names = await call("list_macros").catch(() => []);
  select.replaceChildren();
  if (!names.length) {
    const option = el("option", null, "Nenhuma macro salva");
    option.value = "";
    select.append(option);
  } else {
    for (const name of names) {
      const option = el("option", null, name);
      option.value = name;
      select.append(option);
    }
  }
  select.value = names.includes(current) ? current : (names[0] || "");
  select.disabled = !names.length;
  $("#macro-play").disabled = !select.value;
  $("#macro-delete").disabled = !select.value;
  await loadMacroOptions();
}

async function loadMacroOptions() {
  const select = $("#macro-select");
  const buttons = $$('[data-macro-pause]');
  const delays = $$('[data-macro-delay]');
  const commandInterval = $('[data-macro-command-interval]');
  const skipBattleAfter = $('[data-macro-skip-battle-after]');
  if (!select.value) {
    buttons.forEach((button) => { button.dataset.enabled = "true"; });
    delays.forEach((input) => { input.value = "0"; });
    commandInterval.value = "0";
    skipBattleAfter.value = "0";
    updateMacroPauseButtons();
    buttons.concat(delays, [commandInterval, skipBattleAfter]).forEach((control) => { control.disabled = Boolean(macroCurrentState?.recording || macroCurrentState?.playing); });
    return;
  }
  const options = await call("get_macro_options", select.value).catch(() => ({}));
  commandInterval.value = String(options.command_interval ?? 0);
  skipBattleAfter.value = String(options.skip_battle_after ?? 0);
  for (const category of ["battle", "capture", "target"]) {
    const button = $(`[data-macro-pause="${category}"]`);
    const delay = $(`[data-macro-delay="${category}"]`);
    button.dataset.enabled = options[`pause_${category}`] !== false ? "true" : "false";
    delay.value = String(options[`delay_${category}`] ?? 0);
  }
  updateMacroPauseButtons();
  buttons.concat(delays, [commandInterval, skipBattleAfter]).forEach((control) => { control.disabled = Boolean(macroCurrentState?.recording || macroCurrentState?.playing); });
}

function updateMacroPauseButtons() {
  const labels = { battle: "batalha", capture: "captura", target: "Pok\u00e9mon-alvo" };
  $$('[data-macro-pause]').forEach((button) => {
    const enabled = button.dataset.enabled === "true";
    button.textContent = `Pausa em ${labels[button.dataset.macroPause]}: ${enabled ? "ativada" : "desativada"}`;
    button.setAttribute("aria-pressed", String(enabled));
    button.classList.toggle("primary", enabled);
  });
}

function collectMacroOptions() {
  const options = {};
  for (const category of ["battle", "capture", "target"]) {
    options[`pause_${category}`] = $(`[data-macro-pause="${category}"]`).dataset.enabled === "true";
    options[`delay_${category}`] = Number($(`[data-macro-delay="${category}"]`).value);
  }
  options.command_interval = Number($('[data-macro-command-interval]').value);
  options.skip_battle_after = Number($('[data-macro-skip-battle-after]').value);
  return options;
}

async function saveMacroOptions(showToast = true) {
  const options = collectMacroOptions();
  if (Object.values(options).some((value) => typeof value === "number" && (!Number.isFinite(value) || value < 0 || value > 3600))) {
    toast("Defina cada tempo entre 0 e 3600 segundos", true);
    await loadMacroOptions();
    return false;
  }
  const name = $("#macro-select").value;
  if (!name) return true;
  try {
    await call("set_macro_options", name, options);
    if (showToast) toast("Opções da macro salvas");
    return true;
  } catch (error) {
    toast(error.message || "Não foi possível salvar as opções", true);
    await loadMacroOptions();
    return false;
  }
}

let macroCurrentState = null;

function renderMacroState(state) {
  if (!state) return;
  macroCurrentState = state;
  $("#macro-record").disabled = state.recording || state.playing;
  $("#macro-arrows-only").disabled = state.recording || state.playing;
  $("#macro-stop-record").disabled = !state.recording;
  $("#macro-play").disabled = state.recording || state.playing || !$("#macro-select").value;
  $("#macro-loop").disabled = state.recording || state.playing || !$("#macro-select").value;
  $("#macro-stop-play").disabled = !state.playing;
  $("#macro-delete").disabled = state.recording || state.playing || !$("#macro-select").value;
  $("#macro-select").disabled = state.recording || state.playing || !$("#macro-select").options.length;
  $$('[data-macro-pause], [data-macro-delay], [data-macro-command-interval], [data-macro-skip-battle-after]').forEach((control) => { control.disabled = state.recording || state.playing; });
  $("#macro-options-save").disabled = state.recording || state.playing || !$("#macro-select").value;
  const status = $("#macro-status");
  if (state.recording) {
    status.textContent = `Gravando ${state.name}: ${Math.ceil(state.duration - state.elapsed)} s restantes.`;
  } else if (state.playing) {
    status.textContent = state.paused
      ? state.pause_reasons.length
        ? `Rota gravada pausada durante ${state.pause_reasons.join(", ")}; retoma automaticamente.`
        : `Aguardando ${Math.ceil(state.resume_remaining)} s para retomar a rota gravada.`
      : `Reproduzindo ${$("#macro-select").value || "rota gravada"}${state.loop ? " em ciclo" : ""} no jogo.`;
  } else {
    status.textContent = "Nenhuma gravação ou reprodução em andamento.";
  }
  if (macroWasRecording && !state.recording) renderMacroList(state.name);
  macroWasRecording = Boolean(state.recording);
}

function renderSwitchSlots() {
  const list = $("#switch-list");
  list.replaceChildren();
  const slots = config.switch?.slots || [];
  if (!slots.length) {
    list.append(el("p", "hint", "Nenhuma posição configurada."));
    return;
  }
  slots.forEach((slot, index) => {
    const row = el("div", "switch-row");
    const name = el("strong", "switch-name", slot.name);
    const point = el("span", "switch-point mono", slot.point ? slot.point.join(", ") : "posição não marcada");
    const mark = el("button", "btn ghost small", "Marcar");
    mark.type = "button";
    mark.addEventListener("click", () => startPick("point", `switch_slot:${slot.id}`));
    const click = el("button", "btn small", "Trocar");
    click.type = "button";
    click.disabled = !slot.point;
    click.addEventListener("click", async () => {
      await call("click_switch_slot", slot.id);
      toast(`Clique enviado: ${slot.name}`);
    });
    const remove = el("button", "btn ghost small", "Remover");
    remove.type = "button";
    remove.addEventListener("click", async () => {
      if (!confirm(`Remover a posição "${slot.name}"?`)) return;
      await call("remove_switch_slot", slot.id);
      config = await api.get_config();
      renderSwitchSlots();
    });
    const order = el("div", "slot-order");
    const up = el("button", "btn ghost small", "↑");
    up.type = "button";
    up.title = "Mover para cima na ordem do ciclo";
    up.disabled = index === 0;
    up.addEventListener("click", async () => {
      config.switch.slots = await call("move_switch_slot", slot.id, -1);
      renderSwitchSlots();
    });
    const down = el("button", "btn ghost small", "↓");
    down.type = "button";
    down.title = "Mover para baixo na ordem do ciclo";
    down.disabled = index === slots.length - 1;
    down.addEventListener("click", async () => {
      config.switch.slots = await call("move_switch_slot", slot.id, 1);
      renderSwitchSlots();
    });
    order.append(up, down);
    row.append(name, point, mark, click, remove, order);
    list.append(row);
  });
}

async function init() {
  api = window.pywebview.api;
  config = await api.get_config();
  images = await api.get_images();
  pickKey = images.pick_hotkey;
  $$("[data-pickkey]").forEach((k) => (k.textContent = pickKey));
  buildModules(images.modules);
  buildCaptureGrid();
  buildBattleTargetGrid();
  wireRouteDnD();
  await renderMacroList(config.macro?.name);
  applyConfig();
  wireUi();
  poll();
  setTimeout(checkVersion, 1500);
  setTimeout(checkVersion, 9000);
}

window.addEventListener("pywebviewready", init);
