"use strict";

// The setup page. Every address here is relative, so it keeps the page's one-time code.
const PHASES = ["welcome", "connect", "working", "check", "first-brief", "finish"];
const page = {
  text: null, language: "en", chosen: null, languages: [], system: "macos", ais: [],
  progress: null, welcome: null, connect: null, gmail: null, whatsapp: null, myclub: null,
  check: null, brief: null, welcomeShown: false, connectShown: false, workingShown: false,
  checkShown: false, briefShown: false, finishShown: false, finished: false,
};

// A text in the page's language. On Windows, a text that names the Mac's own things has a twin
// that names Windows' instead (the PC, Credential Manager, PowerShell), used in its place.
function t(key) {
  const table = page.text[page.language];
  return (page.system === "windows" && table[`${key}.windows`]) || table[key];
}

// A text with its {placeholders} filled from `values`.
function fill(text, values) {
  return text.replace(/\{(\w+)\}/g, (all, name) => values && name in values ? String(values[name]) : all);
}

// Shows `key`'s text in `el`, filled from `values`, and keeps it for when the language changes.
function setText(el, key, values) {
  el.dataset.text = key;
  if (values) el.dataset.values = JSON.stringify(values);
  else delete el.dataset.values;
  el.textContent = fill(t(key), values);
}

async function getJSON(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

async function saveLanguage(language) {
  const r = await fetch("api/language", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ language }),
  });
  const out = await r.json();
  if (!r.ok) throw new Error(out.result);
  page.chosen = out.language;
  page.progress = out.progress;
}

function showError(key) {
  const error = document.getElementById("error");
  error.dataset.text = key;
  error.textContent = t(key);
  error.hidden = false;
}

function clearError() {
  const error = document.getElementById("error");
  delete error.dataset.text;
  error.hidden = true;
}

function failed(e) {
  showError(e instanceof TypeError ? "error.closed" : "error.save");
}

function applyText() {
  document.documentElement.lang = page.language;
  document.title = t("title");
  for (const el of document.querySelectorAll("[data-text]")) {
    el.textContent = fill(t(el.dataset.text), el.dataset.values && JSON.parse(el.dataset.values));
  }
  for (const el of document.querySelectorAll("[data-label]")) {
    el.setAttribute("aria-label",
      fill(t(el.dataset.label), el.dataset.values && JSON.parse(el.dataset.values)));
  }
  document.getElementById("language-switch").value = page.language;
  document.getElementById("phases").setAttribute("aria-label", t("phases.label"));
  if (page.progress) renderPhase();
  // Only in Connect: on Finish, its AI sign-in is the one moved into Change the AI.
  if (page.connectShown && page.progress.phase === "connect") renderSources();
}

function renderSwitch() {
  const select = document.getElementById("language-switch");
  for (const { code, name } of page.languages) {
    const option = document.createElement("option");
    option.value = code;
    option.lang = code;
    option.textContent = name;
    select.append(option);
  }
  select.addEventListener("change", async () => {
    clearError();
    const before = page.language;
    choose(select.value);
    // On the first page, Continue saves it; once setup is done, the server has stopped.
    if (!page.chosen || page.finished) return;
    try {
      await saveLanguage(page.language);
    } catch (e) {
      choose(before);
      failed(e);
    }
  });
}

// Shows the page in `language`, with the first page's choice and the switch on it.
function choose(language) {
  page.language = language;
  const choice = document.querySelector(`input[name="language"][value="${language}"]`);
  if (choice) choice.checked = true;
  applyText();
}

function renderChoices() {
  const fieldset = document.getElementById("language-choices");
  for (const { code, name } of page.languages) {
    const label = document.createElement("label");
    label.lang = code;
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "language";
    input.value = code;
    input.setAttribute("aria-label", name);
    input.checked = code === page.language;
    input.addEventListener("change", () => choose(code));
    label.append(input, name);
    fieldset.append(label);
  }
  document.getElementById("language-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();
    const button = event.submitter;
    button.disabled = true;
    try {
      await saveLanguage(page.language);
      show();
    } catch (e) {
      failed(e);
    } finally {
      button.disabled = false;
    }
  });
}

function renderPhase() {
  const current = page.progress.phase;
  const list = document.getElementById("phases");
  list.replaceChildren();
  PHASES.forEach((phase, i) => {
    const item = document.createElement("li");
    const name = document.createElement("b");
    name.textContent = t("phase." + phase);
    item.append(name);
    if (phase === current) item.setAttribute("aria-current", "step");
    else if (i < PHASES.indexOf(current)) item.className = "done";
    list.append(item);
  });
}

// ── Welcome: the AI, the partner and pilot feedback, each with its default.

const RECHECK_MS = 5000;
const ai = { result: null, checking: 0, timer: null };

function picked(name) {
  return document.querySelector(`input[name="${name}"]:checked`).value;
}

function pick(name, value) {
  document.querySelector(`input[name="${name}"][value="${value}"]`).checked = true;
}

// Only the AIs the server offers on this system.
function offerAIs(name) {
  for (const input of document.querySelectorAll(`input[name="${name}"]`)) {
    input.closest("label").hidden = !page.ais.includes(input.value);
  }
}

function renderWelcome() {
  const { ai: chosen, partner, feedback } = page.welcome;
  offerAIs("ai");
  pick("ai", chosen);
  pick("partner", partner.add ? "add" : "only-me");
  // Asked only when this version ships the pilot Form.
  document.getElementById("welcome-feedback").hidden = feedback === null;
  pick("feedback", feedback ? "yes" : "no");
  document.getElementById("partner-address").value = partner.address;
  const select = document.getElementById("partner-language");
  for (const { code, name } of page.languages) {
    const option = document.createElement("option");
    option.value = code;
    option.lang = code;
    option.textContent = name;
    select.append(option);
  }
  select.value = partner.address ? partner.language : page.language;

  for (const input of document.querySelectorAll('input[name="ai"]')) {
    input.addEventListener("change", checkAI);
  }
  for (const input of document.querySelectorAll('input[name="partner"]')) {
    input.addEventListener("change", showPartner);
  }
  document.getElementById("ai-again").addEventListener("click", checkAI);
  document.getElementById("ai-copy").addEventListener("click", () => {
    navigator.clipboard.writeText(document.getElementById("ai-install-line").textContent);
  });
  document.getElementById("welcome-form").addEventListener("submit", saveWelcome);
  showPartner();
  checkAI();
}

function showPartner() {
  const add = picked("partner") === "add";
  document.getElementById("partner-details").hidden = !add;
  document.getElementById("partner-address").required = add;
}

// Checks the AI picked, and again every few seconds until it's ready, so the page ticks
// itself once the parent has installed it or signed in.
async function checkAI() {
  clearTimeout(ai.timer);
  showChat();
  const name = picked("ai");
  const check = ++ai.checking;
  showAI(name, "checking");
  let out;
  try {
    const r = await fetch("api/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ai: name }),
    });
    out = await r.json();
    if (!r.ok) throw new Error(out.result);
  } catch (e) {
    if (check === ai.checking) failed(e);
    return;
  }
  if (check !== ai.checking) return; // the parent picked the other one meanwhile
  showAI(name, out.result, out.install);
  if (out.result !== "ready" && !document.getElementById("welcome").hidden) {
    ai.timer = setTimeout(checkAI, RECHECK_MS);
  }
}

// Welcome goes on once the AI picked is ready. On Windows, ChatGPT's and Claude's own steps after
// it install Codex or Claude Code and sign in, so Welcome needn't wait for those.
function welcomeGoesOn(name, result) {
  return result === "ready" || (page.system === "windows" && ["codex", "claude"].includes(name)
    && ["not-installed", "signed-out"].includes(result));
}

function showAI(name, result, install) {
  ai.result = result;
  const status = document.getElementById("ai-status");
  status.dataset.result = result;
  const message = document.getElementById("ai-message");
  message.dataset.text = result === "checking" ? "welcome.ai.checking" : `ai.${name}.${result}`;
  message.textContent = t(message.dataset.text);
  document.getElementById("ai-install").hidden = !install;
  document.getElementById("ai-install-line").textContent = install || "";
  // A recheck can't change a result Welcome already goes on with.
  const goesOn = welcomeGoesOn(name, result);
  document.getElementById("ai-again").hidden = goesOn || result === "checking";
  document.getElementById("welcome-continue").disabled = !goesOn;
}

async function saveWelcome(event) {
  event.preventDefault();
  clearError();
  if (!welcomeGoesOn(picked("ai"), ai.result)) return;
  const button = event.submitter;
  button.disabled = true;
  const add = picked("partner") === "add";
  const answers = {
    ai: picked("ai"),
    partner: add ? {
      address: document.getElementById("partner-address").value.trim(),
      language: document.getElementById("partner-language").value,
    } : null,
    feedback: page.welcome.feedback !== null && picked("feedback") === "yes",
  };
  try {
    const r = await fetch("api/welcome", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(answers),
    });
    const out = await r.json();
    if (r.status === 400) return showError("welcome.partner.check");
    if (!r.ok) throw new Error(out.result);
    clearTimeout(ai.timer);
    page.progress = out.progress;
    page.welcome.ai = answers.ai;
    show();
  } catch (e) {
    failed(e);
  } finally {
    button.disabled = false;
  }
}

// ── Connect: the Source list, worked through in order, and Gmail's own step.

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const out = await r.json();
  if (!r.ok) throw new Error(out.result);
  return out;
}

// Connect's steps, which the list shows in the order the server sends (page.connect.sources). A
// step's panel is the element `source-<name>`, shown while it's the current step; the rest of the
// step is its entry here, each part optional:
//   setUp   — once, as Connect is first shown: its buttons and the texts that don't change
//   first   — as it becomes the current step, the first time since a step was opened or skipped
//   again   — each time the list is drawn again while it stays the current step
//   leave   — as any step is opened or skipped: what was typed into it and what it waits on
//   ownSkip — its panel asks whether to skip it, in place of the skip button
//   working — Working's line for it once it's done: a text key and its values
//   label   — what the first Brief's progress calls it while it's read
const STEPS = {
  wilma: {
    setUp: renderWilma, first: prepareWilma, again: prepareWilma, ownSkip: true,
    leave: () => {
      document.getElementById("wilma-password").value = "";
      clearTimeout(wilmaStep.timer);
    },
    working: () => ["working.wilma",
      { kids: page.check.kids.map((k) => k.everyday_name).join(", ") }],
    label: "Wilma",
  },
  gmail: {
    setUp: setUpGmail,
    leave: () => { document.getElementById("gmail-password").value = ""; },
    label: "Gmail",
  },
  ai: {
    setUp: renderAI, first: showAISignIn,
    leave: () => {
      document.getElementById("claude-token").value = "";
      document.getElementById("gateway-key").value = "";
      clearTimeout(aiStep.timer);
    },
  },
  whatsapp: {
    setUp: renderWhatsApp,
    first: () => {
      showSource("whatsapp.checking");
      checkWhatsApp();
    },
    leave: leaveWhatsApp,
    working: () => ["working.whatsapp", { count: (page.progress.whatsapp_chats || []).length }],
    label: "WhatsApp",
  },
  myclub: {
    setUp: setUpMyClub, first: refreshMyClub, again: renderMyClub,
    leave: () => { document.getElementById("myclub-link").value = ""; },
    working: () => ["working.myclub", null],
    label: "MyClub",
  },
};

// The steps the server offers on this system, in its order, each with its name and whether the
// family can skip it.
function connectSteps() {
  return page.connect.sources.filter(({ name }) => name in STEPS)
    .map(({ name, skippable }) => ({ name, skippable, ...STEPS[name] }));
}

function renderConnect() {
  document.getElementById("source-skip").addEventListener("click", () => {
    chooseSource(currentSource(), "skip");
  });
  document.getElementById("source-copy").addEventListener("click", () => {
    navigator.clipboard.writeText(document.getElementById("source-install-line").textContent);
  });
  for (const step of connectSteps()) step.setUp?.();
  document.getElementById("connect-continue").addEventListener("click", startReading);
}

function setUpGmail() {
  document.getElementById("gmail-open").addEventListener("click", () => openSite("app-passwords"));
  document.getElementById("gmail-two-step").addEventListener("click", () => openSite("two-step"));
  document.getElementById("gmail-unavailable").addEventListener("click", () => {
    showSource("gmail.app-passwords-unavailable");
  });
  document.getElementById("gmail-form").addEventListener("submit", connectGmail);
  document.getElementById("gmail-address").value = page.gmail.address || "";
}

// The Source the parent is on: the one saved, else the first still to do (null once none is).
function currentSource() {
  const statuses = page.progress.sources;
  return page.progress.source ??
    page.connect.sources.map((s) => s.name).find((name) => statuses[name] === "to-do") ?? null;
}

// The Source the list last showed as current, the hand-off once one was connected (the Source
// just done and the next one, null after the last), and the steps whose `first` has run since a
// step was last opened or skipped.
const connectStep = { current: undefined, handoff: null, shown: new Set() };

function renderSources() {
  const current = currentSource();
  const statuses = page.progress.sources;
  const moved = connectStep.current !== undefined && current !== connectStep.current;
  if (moved) {
    const left = connectStep.current;
    connectStep.handoff = left && statuses[left] === "done" ? { done: left, next: current } : null;
  }
  connectStep.current = current;
  const list = document.getElementById("sources");
  list.replaceChildren();
  for (const { name } of page.connect.sources) {
    const item = document.createElement("li");
    item.dataset.status = statuses[name];
    if (name === current) item.setAttribute("aria-current", "step");
    const button = document.createElement("button");
    button.type = "button";
    const label = document.createElement("span");
    label.textContent = t("source." + name);
    const state = document.createElement("span");
    state.className = "state";
    state.textContent = t("status." + statuses[name]);
    button.append(label, state);
    button.setAttribute("aria-label", t("sources.button")
      .replace("{source}", label.textContent).replace("{status}", state.textContent));
    button.addEventListener("click", () => {
      if (name !== current || statuses[name] === "skipped") chooseSource(name, "open");
    });
    item.append(button);
    list.append(item);
  }
  const step = connectSteps().find((s) => s.name === current);
  document.getElementById("source-skip").hidden =
    !step?.skippable || statuses[current] !== "to-do" || Boolean(step.ownSkip);
  for (const name of Object.keys(STEPS)) {
    document.getElementById("source-" + name).hidden = name !== current;
  }
  document.getElementById("connect-done").hidden = Boolean(current);
  renderHandoff(moved);
  if (!step) return;
  if (connectStep.shown.has(current)) return step.again?.();
  connectStep.shown.add(current);
  step.first?.();
}

// Once a Source is connected, says so and which Source is next, and scrolls to it in the list,
// since the step below changes by itself.
function renderHandoff(moved) {
  const note = document.getElementById("source-handoff");
  const handoff = connectStep.handoff;
  note.hidden = !handoff;
  if (!handoff) return;
  const done = t("source." + handoff.done);
  note.textContent = handoff.next
    ? fill(t("connect.handoff"), { done, next: t("source." + handoff.next) })
    : fill(t("connect.handoff.last"), { done });
  if (!moved) return;
  const target = document.querySelector('#sources li[aria-current="step"]') ||
    document.getElementById("connect-done");
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" });
}

// Skips the Source, or opens it from the list, which brings a skipped one back.
async function chooseSource(name, action) {
  clearError();
  try {
    const out = await post("api/source", { source: name, action });
    page.progress = out.progress;
    // What was typed isn't kept once the parent moves on, and each step starts again.
    for (const step of Object.values(STEPS)) step.leave?.();
    connectStep.shown.clear();
    showSource(null);
    renderSources();
  } catch (e) {
    failed(e);
  }
}

// The results after which the page offers Wilma's Terminal sign-in window, and the ones that
// can be tried again once the parent has fixed what they say.
const OFFER_TERMINAL = [
  "wilma.sign-in-failed", "wilma.no-list", "wilma.window.not-signed-in",
  "wilma.window.sign-in-failed", "wilma.window.timeout", "wilma.window.no-terminal",
];
const TRY_AGAIN = [
  "wilma.ready.install-failed", "wilma.not-installed", "wilma.window.not-installed",
];

// What the last step said, under the Source: a text key, or null for nothing, with an address
// to open, the Kids found or a line to install something.
function showSource(key, { url, kids, install, values } = {}) {
  const status = document.getElementById("source-status");
  const message = document.getElementById("source-message");
  status.hidden = !key;
  status.dataset.result = key || "";
  if (key) {
    setText(message, key, values);
  } else {
    delete message.dataset.text;
  }
  const where = document.getElementById("source-url");
  where.hidden = !url;
  where.textContent = url || "";
  const list = document.getElementById("source-kids");
  list.replaceChildren(...(kids || []).map((name) => {
    const item = document.createElement("li");
    item.textContent = name;
    return item;
  }));
  list.hidden = !kids || !kids.length;
  document.getElementById("source-install").hidden = !install;
  document.getElementById("source-install-line").textContent = install || "";
  document.getElementById("gmail-two-step").hidden =
    !["gmail.app-passwords-unavailable", "gmail.rejected"].includes(key);
  document.getElementById("wilma-terminal").hidden = !OFFER_TERMINAL.includes(key);
  document.getElementById("wilma-again").hidden = !TRY_AGAIN.includes(key);
  document.getElementById("claude-terminal").hidden = !CLAUDE_TERMINAL.includes(key);
  document.getElementById("claude-again").hidden = !CLAUDE_AGAIN.includes(key);
}

// The server opens the site, so the page itself names none.
async function openSite(site) {
  clearError();
  try {
    const out = await post("api/open", { site });
    if (out.result !== "opened") {
      showSource(out.url ? "open.not-opened" : "open.app-not-opened", { url: out.url });
    }
  } catch (e) {
    failed(e);
  }
}

async function connectGmail(event) {
  event.preventDefault();
  clearError();
  showSource(null);
  const button = event.submitter;
  button.disabled = true;
  const password = document.getElementById("gmail-password");
  try {
    const out = await post("api/gmail", {
      address: document.getElementById("gmail-address").value,
      password: password.value,
    });
    if (out.result !== "no-connection" && out.result !== "keychain-failed") password.value = "";
    if (out.result === "saved") {
      page.progress = out.progress;
      page.gmail.address = out.address;
      document.getElementById("gmail-address").value = out.address;
      renderSources();
    }
    showSource("gmail." + out.result);
  } catch (e) {
    failed(e);
  } finally {
    button.disabled = false;
  }
}

// ── Connect: Wilma, signed in to in the page (ADR 0008), or only the town without it.

const SEARCH_MS = 250;
const WINDOW_MS = 3000;
const wilmaStep = {
  ready: false, preparing: false, found: [], searched: false, searching: 0, typing: null,
  timer: null,
};

function renderWilma() {
  for (const input of document.querySelectorAll('input[name="wilma-uses"]')) {
    input.addEventListener("change", showWilmaUses);
  }
  const search = document.getElementById("wilma-search");
  search.addEventListener("input", () => {
    clearTimeout(wilmaStep.typing);
    wilmaStep.typing = setTimeout(searchTowns, SEARCH_MS);
  });
  search.addEventListener("keydown", (event) => {
    if (event.key === "Enter") event.preventDefault();
  });
  document.getElementById("wilma-form").addEventListener("submit", signInWilma);
  document.getElementById("town-form").addEventListener("submit", saveTown);
  document.getElementById("wilma-passwords").addEventListener("click", () => openSite("passwords"));
  document.getElementById("wilma-passwords").hidden = page.system !== "macos"; // macOS's own app
  document.getElementById("wilma-terminal").addEventListener("click", openWilmaWindow);
  document.getElementById("wilma-again").addEventListener("click", getWilmaReady);
  showWilmaUses();
}

// With Wilma, the login fields; without, only the town.
function showWilmaUses() {
  const uses = picked("wilma-uses") === "yes";
  document.getElementById("wilma-form").hidden = !uses;
  document.getElementById("town-form").hidden = uses;
  // A status the parent caused meanwhile, such as "pick your town", stays.
  if (wilmaStep.preparing) {
    const status = document.getElementById("source-status");
    if (INSTALLING.includes(status.dataset.result)) showSource(wilmaInstalling());
    showWilmaWait(wilmaInstalling());
  }
  renderTowns();
}

// What the install gets ready: a family without Wilma, who just said so, waits only for the
// town list, so the page doesn't say it's getting Wilma ready.
const INSTALLING = ["wilma.ready.installing", "wilma.ready.installing.town"];
function wilmaInstalling() {
  return picked("wilma-uses") === "no" ? "wilma.ready.installing.town" : "wilma.ready.installing";
}

// Wilma's step, each time it's drawn: the CLI made ready, unless it is or is being made so.
function prepareWilma() {
  if (!wilmaStep.ready && !wilmaStep.preparing) getWilmaReady();
}

// Gets the pinned wilma CLI ready, installing it if it isn't: the town list comes with it.
async function getWilmaReady() {
  wilmaStep.preparing = true;
  showSource(wilmaInstalling());
  showWilmaWait(wilmaInstalling());
  try {
    const out = await post("api/wilma/install", {});
    if (out.result !== "installed") {
      showSource("wilma.ready." + out.result);
      showWilmaWait("wilma.ready." + out.result);
      return;
    }
    wilmaStep.ready = true;
    showSource(null);
    showWilmaWait(null);
    const search = document.getElementById("wilma-search");
    search.disabled = false;
    search.focus();
    if (search.value.trim()) searchTowns();
  } catch (e) {
    showSource(null);
    showWilmaWait(null);
    failed(e);
  } finally {
    wilmaStep.preparing = false;
  }
}

// Under the town box, which stays greyed out until the wilma CLI is ready, why it waits: the
// town list comes with the CLI, so nothing the parent fills in opens it sooner.
function showWilmaWait(key) {
  const wait = document.getElementById("wilma-search-wait");
  wait.hidden = !key;
  wait.classList.toggle("busy", INSTALLING.includes(key));
  if (key) setText(wait, key);
  else delete wait.dataset.text;
}

async function searchTowns() {
  const query = document.getElementById("wilma-search").value.trim();
  const search = ++wilmaStep.searching;
  if (query.length < 2) {
    wilmaStep.found = [];
    wilmaStep.searched = false;
    renderTowns();
    return;
  }
  try {
    const out = await post("api/towns", { query });
    if (search !== wilmaStep.searching) return; // the parent typed on meanwhile
    if (out.result !== "found") {
      showSource("wilma." + out.result);
      return;
    }
    wilmaStep.found = out.towns;
    wilmaStep.searched = true;
    renderTowns();
  } catch (e) {
    failed(e);
  }
}

// The entries found, to pick one from, each with its town; without Wilma, each town once.
function renderTowns() {
  const uses = picked("wilma-uses") === "yes";
  const before = chosenTown();
  const shown = [];
  for (const entry of wilmaStep.found) {
    if (!uses && (!entry.town || shown.some((e) => e.town === entry.town))) continue;
    shown.push(entry);
  }
  const list = document.getElementById("wilma-towns");
  list.replaceChildren(...shown.map((entry) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "wilma-town";
    input.value = String(wilmaStep.found.indexOf(entry));
    input.checked = shown.length === 1 || Boolean(before) &&
      (uses ? before.url === entry.url : before.town === entry.town);
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = uses ? entry.name : entry.town;
    label.append(input, name);
    if (uses && entry.town) {
      const town = document.createElement("span");
      town.className = "town";
      town.textContent = entry.town;
      label.append(town);
    }
    return label;
  }));
  document.getElementById("wilma-none").hidden = !wilmaStep.searched || shown.length > 0;
}

function chosenTown() {
  const input = document.querySelector('input[name="wilma-town"]:checked');
  return input ? wilmaStep.found[Number(input.value)] : null;
}

async function signInWilma(event) {
  event.preventDefault();
  clearError();
  const entry = chosenTown();
  if (!entry) return showSource("wilma.pick-town");
  const button = event.submitter;
  button.disabled = true;
  showSource("wilma.signing-in");
  const password = document.getElementById("wilma-password");
  try {
    const out = await post("api/wilma", {
      url: entry.url,
      town: entry.town,
      username: document.getElementById("wilma-username").value,
      password: password.value,
    });
    password.value = "";
    wilmaResult(out, "wilma.");
  } catch (e) {
    showSource(null);
    failed(e);
  } finally {
    button.disabled = false;
  }
}

// How signing in went. Once signed in, Wilma is done and the Kids it found are listed.
function wilmaResult(out, prefix) {
  if (out.result !== "signed-in") return showSource(prefix + out.result);
  page.progress = out.progress;
  renderSources();
  showSource("wilma.signed-in", { kids: out.kids });
}

async function saveTown(event) {
  event.preventDefault();
  clearError();
  const entry = chosenTown();
  if (!entry) return showSource("wilma.pick-town");
  const button = event.submitter;
  button.disabled = true;
  try {
    const out = await post("api/town", { town: entry.town });
    page.progress = out.progress;
    renderSources();
    showSource("wilma.town-saved");
  } catch (e) {
    failed(e);
  } finally {
    button.disabled = false;
  }
}

// The Terminal sign-in window, for when the page's own sign-in fails. The page asks how it's
// going every few seconds, and ticks Wilma itself once the parent has signed in there.
async function openWilmaWindow() {
  clearError();
  try {
    windowResult(await post("api/wilma/terminal", { town: chosenTown()?.town ?? null }));
  } catch (e) {
    failed(e);
  }
}

async function checkWilmaWindow() {
  try {
    windowResult(await post("api/wilma/check", {}));
  } catch (e) {
    failed(e);
  }
}

function windowResult(out) {
  clearTimeout(wilmaStep.timer);
  if (out.result === "no-window") return;
  if (out.result === "waiting") {
    showSource("wilma.window.waiting");
    wilmaStep.timer = setTimeout(checkWilmaWindow, WINDOW_MS);
    return;
  }
  wilmaResult(out, "wilma.window.");
}

// ── Connect: the AI sign-in for the evening Brief: Claude's own token, read from its sign-in
// without anything copied, Codex's ChatGPT login, or the Gateway Key pasted into the page. Each
// ticks itself once it's done.

const AI_MS = 3000;
const aiStep = { timer: null };
// After these the page offers Claude's Terminal window, or to sign in again.
const CLAUDE_TERMINAL = ["claude.sign-in-failed", "claude.timeout", "claude.window.no-terminal"];
const CLAUDE_AGAIN = [
  "claude.not-installed", "claude.test-call-failed", "claude.keychain-failed",
  "claude.window.not-installed", "claude.install-failed",
];
// What checking Claude's install on Windows can say: still installing, or how it ended.
const CLAUDE_INSTALL = ["installing", "install-failed", "opened", "no-terminal"];
// Codex's results after which the page checks again by itself.
const CODEX_AGAIN = [
  "codex.waiting", "codex.not-installed", "codex.check-failed", "codex.installing",
];
// Codex's results after which the page offers to install it: on Windows, where it installs the
// Codex CLI itself. A Mac gets Codex with the ChatGPT app, which the page offers to download.
const CODEX_INSTALL = ["codex.not-installed", "codex.install-failed"];

function renderAI() {
  document.getElementById("claude-start").addEventListener("click", startClaude);
  document.getElementById("claude-again").addEventListener("click", startClaude);
  document.getElementById("claude-terminal").addEventListener("click", openClaudeWindow);
  document.getElementById("claude-form").addEventListener("submit", saveClaudeToken);
  document.getElementById("codex-login").addEventListener("click", signInCodex);
  document.getElementById("codex-download").addEventListener("click", () => openSite("codex-app"));
  document.getElementById("codex-install").addEventListener("click", installCodex);
  document.getElementById("gateway-form").addEventListener("submit", saveGatewayKey);
}

// The sign-in of the Household's AI, as its step opens: Codex's checks by itself, and Claude's
// picks up a sign-in still running.
function showAISignIn() {
  document.getElementById("ai-claude").hidden = page.welcome.ai !== "claude";
  document.getElementById("ai-codex").hidden = page.welcome.ai !== "codex";
  document.getElementById("ai-gateway").hidden = page.welcome.ai !== "gateway";
  if (page.welcome.ai === "codex" && page.progress.sources.ai === "to-do") {
    showSource("codex.checking");
    checkCodex();
  }
  if (page.welcome.ai === "claude") resumeClaude();
}

// Once the AI sign-in is done, setup moves on to the next Source. On Finish, the AI picked there
// is the Household's now.
function aiDone(out, key) {
  document.getElementById("claude-form").hidden = true;
  if (page.progress.phase === "finish") return switchedAI();
  page.progress = out.progress;
  renderSources();
  showSource(key);
}

// Windows has no pseudo-terminal to read Claude's token from: there the page installs Claude
// Code if it isn't there, and the family copies the token from its PowerShell window.
function startClaude() {
  return page.system === "windows" ? installClaude() : signInClaude();
}

async function signInClaude() {
  clearError();
  clearTimeout(aiStep.timer);
  try {
    claudeResult(await post("api/claude", {}));
  } catch (e) {
    failed(e);
  }
}

async function checkClaude() {
  try {
    claudeResult(await post("api/claude/check", {}));
  } catch (e) {
    failed(e);
  }
}

// Picks up a sign-in still running from before the page was reloaded or left, and only that:
// how an earlier one ended is old news. On Windows, an install of Claude Code, or the window it
// opened once it was done, whose token the field takes.
async function resumeClaude() {
  try {
    const out = await post("api/claude/check", {});
    if (out.result === "waiting") claudeResult(out);
    if (CLAUDE_INSTALL.includes(out.result)) claudeInstallResult(out);
  } catch (e) {
    failed(e);
  }
}

function claudeResult(out) {
  clearTimeout(aiStep.timer);
  if (out.result === "waiting") aiStep.timer = setTimeout(checkClaude, AI_MS);
  if (out.result === "saved") return aiDone(out, "claude.saved");
  showSource("claude." + out.result, { install: out.install });
}

async function openClaudeWindow() {
  clearError();
  try {
    claudeWindowResult(await post("api/claude/terminal", {}));
  } catch (e) {
    failed(e);
  }
}

function claudeWindowResult(out) {
  if (out.result === "opened") document.getElementById("claude-form").hidden = false;
  showSource("claude.window." + out.result, { install: out.install });
}

// On Windows: installs Claude Code if it isn't there, then opens its sign-in window by itself.
async function installClaude() {
  clearError();
  clearTimeout(aiStep.timer);
  try {
    claudeInstallResult(await post("api/claude/install", {}));
  } catch (e) {
    failed(e);
  }
}

async function checkClaudeInstall() {
  try {
    claudeInstallResult(await post("api/claude/check", {}));
  } catch (e) {
    failed(e);
  }
}

function claudeInstallResult(out) {
  clearTimeout(aiStep.timer);
  if (out.result === "installing") aiStep.timer = setTimeout(checkClaudeInstall, AI_MS);
  if (["installing", "install-failed"].includes(out.result)) {
    return showSource("claude." + out.result);
  }
  if (out.result !== "no-sign-in") claudeWindowResult(out);
}

async function saveClaudeToken(event) {
  event.preventDefault();
  clearError();
  const button = event.submitter;
  button.disabled = true;
  const token = document.getElementById("claude-token");
  try {
    const out = await post("api/claude/token", { token: token.value });
    if (out.result !== "keychain-failed") token.value = "";
    if (out.result === "saved") return aiDone(out, "claude.saved");
    showSource("claude." + out.result, { install: out.install });
  } catch (e) {
    failed(e);
  } finally {
    button.disabled = false;
  }
}

async function checkCodex() {
  clearTimeout(aiStep.timer);
  try {
    codexResult(await post("api/codex", {}));
  } catch (e) {
    failed(e);
  }
}

async function signInCodex() {
  clearError();
  clearTimeout(aiStep.timer);
  try {
    codexResult(await post("api/codex/login", {}));
  } catch (e) {
    failed(e);
  }
}

// On Windows: installs the Codex CLI, then opens ChatGPT's sign-in by itself.
async function installCodex() {
  clearError();
  clearTimeout(aiStep.timer);
  try {
    codexResult(await post("api/codex/install", {}));
  } catch (e) {
    failed(e);
  }
}

function codexResult(out) {
  const key = "codex." + out.result;
  const windows = page.system === "windows";
  document.getElementById("codex-login").hidden =
    !["codex.signed-out", "codex.login-failed"].includes(key);
  document.getElementById("codex-download").hidden = windows || key !== "codex.not-installed";
  document.getElementById("codex-install").hidden = !windows || !CODEX_INSTALL.includes(key);
  if (out.result === "signed-in") return aiDone(out, key);
  showSource(key);
  if (CODEX_AGAIN.includes(key)) aiStep.timer = setTimeout(checkCodex, AI_MS);
}

// The Gateway Key the Parent Recap team sent an invited Household (ADR 0014), tested with one
// call to the gateway and kept in the Keychain. It stays in the field only when the same key can
// be tried again as it is.
async function saveGatewayKey(event) {
  event.preventDefault();
  clearError();
  const button = event.submitter;
  button.disabled = true;
  const key = document.getElementById("gateway-key");
  try {
    const out = await post("api/gateway", { key: key.value });
    if (!["no-connection", "test-call-failed", "keychain-failed"].includes(out.result)) key.value = "";
    if (out.result === "saved") return aiDone(out, "gateway.saved");
    showSource("gateway." + out.result);
  } catch (e) {
    failed(e);
  } finally {
    button.disabled = false;
  }
}

// ── Connect: WhatsApp, read with the evening job's own permission. The page reads again every
// few seconds, so the entry ticks itself once the parent has given the permission.

const WHATSAPP_MS = 5000;
const whatsappStep = { opened: false, checking: 0, timer: null };

// The Python file Finder selects, by the name it has on this Mac, such as python3.13: in the
// step's texts, its pictures and the texts that send the parent back to Full Disk Access.
function pythonFile() {
  return { python: page.whatsapp.python };
}

function renderWhatsApp() {
  document.getElementById("whatsapp-open").addEventListener("click", openFullDiskAccess);
  setText(document.getElementById("whatsapp-explain"), "whatsapp.explain", pythonFile());
  setText(document.getElementById("whatsapp-drag"), "whatsapp.picture.drag", pythonFile());
  for (const name of document.querySelectorAll("#source-whatsapp .python-file")) {
    name.textContent = page.whatsapp.python;
  }
}

function leaveWhatsApp() {
  clearTimeout(whatsappStep.timer);
  whatsappStep.checking++; // a read still on its way is old news
  whatsappStep.opened = false;
}

// Finder shows the Python to allow, and System Settings opens at Full Disk Access next to it.
async function openFullDiskAccess() {
  clearError();
  try {
    const out = await post("api/whatsapp/open", {});
    whatsappStep.opened = out.result === "opened";
    showSource("whatsapp.open." + out.result, { values: pythonFile() });
  } catch (e) {
    failed(e);
  }
}

async function checkWhatsApp() {
  clearTimeout(whatsappStep.timer);
  const check = ++whatsappStep.checking;
  let out;
  try {
    out = await post("api/whatsapp/check", {});
  } catch (e) {
    if (check === whatsappStep.checking) failed(e);
    return;
  }
  if (check !== whatsappStep.checking) return; // the parent moved on meanwhile
  if (out.result === "readable") {
    page.progress = out.progress;
    renderSources();
    showSource("whatsapp.readable");
    return;
  }
  // Once System Settings is open, the page keeps saying what to do there.
  if (out.result !== "no-permission" || !whatsappStep.opened) showSource("whatsapp." + out.result);
  whatsappStep.timer = setTimeout(checkWhatsApp, WHATSAPP_MS);
}

// ── Connect: MyClub. Each Kid's calendar link, pasted into the page's own field. A Household
// without Wilma adds its Kids here first, by the names it calls them.

const myclubStep = { kid: null };

function setUpMyClub() {
  document.getElementById("myclub-open").addEventListener("click", () => openSite("myclub"));
  document.getElementById("myclub-form").addEventListener("submit", saveMyClubLink);
  document.getElementById("myclub-kid-form").addEventListener("submit", addKid);
  document.getElementById("myclub-continue").addEventListener("click", leaveMyClub);
}

// The Kids as they are now: Wilma may have listed them since the page opened.
async function refreshMyClub() {
  try {
    page.myclub = (await getJSON("api/state")).myclub;
    renderMyClub();
  } catch (e) {
    failed(e);
  }
}

function renderMyClub() {
  const { kids, add } = page.myclub;
  if (!kids.some((k) => k.name === myclubStep.kid)) {
    myclubStep.kid = (kids.find((k) => !k.linked) ?? kids[0])?.name ?? null;
  }
  const list = document.getElementById("myclub-kids");
  list.setAttribute("aria-label", t("myclub.kid"));
  list.replaceChildren(...kids.map(({ name, linked }) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "myclub-kid";
    input.value = name;
    input.checked = name === myclubStep.kid;
    // Another Kid's link starts empty, so what was pasted for one Kid isn't saved for the next.
    input.addEventListener("change", () => {
      myclubStep.kid = name;
      document.getElementById("myclub-link").value = "";
      showSource(null);
    });
    const shown = document.createElement("span");
    shown.className = "name";
    shown.textContent = name;
    const state = document.createElement("span");
    state.className = "town";
    state.textContent = t(linked ? "myclub.kid.linked" : "myclub.kid.to-do");
    label.append(input, shown, state);
    return label;
  }));
  document.getElementById("myclub-kid-form").hidden = !add;
  document.getElementById("myclub-form").hidden = !kids.length;
  document.getElementById("myclub-more").hidden = page.progress.sources.myclub !== "done";
}

async function saveMyClubLink(event) {
  event.preventDefault();
  clearError();
  if (!myclubStep.kid) return showSource("myclub.pick-kid");
  const button = event.submitter;
  button.disabled = true;
  showSource("myclub.checking");
  const link = document.getElementById("myclub-link");
  try {
    const out = await post("api/myclub", { kid: myclubStep.kid, link: link.value });
    // Kept only when trying again may work as it is.
    if (out.result !== "link-failed" && out.result !== "save-failed") link.value = "";
    if (out.result === "saved") {
      page.progress = out.progress;
      page.myclub.kids = out.kids;
      myclubStep.kid = null; // the next Kid without a link
      renderSources();
    }
    showSource("myclub." + out.result);
    // The result is shown under the whole step: bring it into view next to the button.
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById("source-status").scrollIntoView({ behavior: still ? "auto" : "smooth",
                                                              block: "nearest" });
  } catch (e) {
    showSource(null);
    failed(e);
  } finally {
    button.disabled = false;
  }
}

async function addKid(event) {
  event.preventDefault();
  clearError();
  const button = event.submitter;
  button.disabled = true;
  const name = document.getElementById("myclub-kid-name");
  try {
    const out = await post("api/myclub/kid", { name: name.value });
    if (out.result === "added") {
      page.myclub.kids = out.kids;
      myclubStep.kid = name.value.trim();
      name.value = "";
      renderMyClub();
    }
    showSource("myclub." + out.result);
  } catch (e) {
    failed(e);
  } finally {
    button.disabled = false;
  }
}

// Moves on once a link is saved, leaving the Kids without a club as they are.
async function leaveMyClub() {
  clearError();
  try {
    const out = await post("api/myclub/done", {});
    page.progress = out.progress;
    document.getElementById("myclub-link").value = "";
    showSource(null);
    renderSources();
  } catch (e) {
    failed(e);
  }
}

// ── Working: Parent Recap reads the Gmail senders for the check page, and the page shows how
// far it is. Wilma's Kids and WhatsApp's groups were read in Connect.

const READING_MS = 1000;
const workingStep = { timer: null, reading: null };

// The Kids and the lists as they are now, for the progress shown and the check page.
async function refreshState() {
  const state = await getJSON("api/state");
  page.check = state.check;
  page.myclub = state.myclub;
}

async function startReading() {
  clearError();
  clearTimeout(workingStep.timer);
  try {
    const out = await post("api/working", {});
    page.progress = out.progress;
    workingStep.reading = null;
    await refreshState();
    const first = !page.workingShown;
    show(); // the first time, this checks how far it is
    if (!first) checkReading();
  } catch (e) {
    failed(e);
  }
}

function enterWorking() {
  document.getElementById("working-again").addEventListener("click", startReading);
  renderWorking();
  checkReading();
}

async function checkReading() {
  clearTimeout(workingStep.timer);
  let out;
  try {
    out = await post("api/working/check", {});
  } catch (e) {
    failed(e);
    return;
  }
  if (out.result === "no-read") return startReading(); // the page was restarted meanwhile
  workingStep.reading = out;
  if (out.result === "read") {
    page.progress = out.progress;
    try {
      await refreshState();
    } catch (e) {
      failed(e);
    }
    show();
    return;
  }
  renderWorking();
  if (out.result === "reading") workingStep.timer = setTimeout(checkReading, READING_MS);
}

// What's been read: the Sources Connect read, in its list's order, then Gmail's senders, as far
// as they've got.
function renderWorking() {
  const statuses = page.progress.sources;
  const reading = workingStep.reading || { result: "reading", read: 0, total: null };
  const items = connectSteps().filter((step) => step.working && statuses[step.name] === "done")
    .map((step) => step.working());
  items.push(reading.total ? ["working.gmail", { read: reading.read, total: reading.total }, true]
    : ["working.gmail.start", null, true]);
  document.getElementById("working-list").replaceChildren(...items.map(([key, values, now]) => {
    const item = document.createElement("li");
    item.dataset.status = now ? "to-do" : "done";
    if (now && reading.result === "reading") item.setAttribute("aria-current", "step");
    const line = document.createElement("span");
    setText(line, key, values);
    item.append(line);
    return item;
  }));
  const bar = document.getElementById("working-bar");
  bar.value = reading.total ? reading.read / reading.total : 0;
  bar.hidden = reading.result !== "reading";
  const failedRead = reading.result === "read-failed";
  document.getElementById("working-status").hidden = !failedRead;
  document.getElementById("working-again").hidden = !failedRead;
  if (failedRead) setText(document.getElementById("working-message"), "working.read-failed");
}

// ── Check: one page of everything found, each list ticked by best guess, confirmed at once.
// Confirming saves it all and runs the health check.

const HEALTH_MS = 2000;
const checkStep = { timer: null, checking: false };

function enterCheck() {
  document.getElementById("check-form").addEventListener("submit", confirmCheck);
  document.getElementById("check-kid-add").addEventListener("click", addCheckKid);
  document.getElementById("check-kid-name").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addCheckKid();
  });
  allOption("check-whatsapp-all", "check-groups");
  allOption("check-senders-all", "check-senders");
  showBackToConnect("check-back");
  renderCheck();
  resumeHealth();
}

function renderCheck() {
  const { kids, whatsapp, senders, recipients, evening, feedback } = page.check;
  document.getElementById("check-kids").replaceChildren(...kids.map(kidRow));
  document.getElementById("check-whatsapp").hidden = whatsapp === null;
  document.getElementById("check-groups").replaceChildren(...(whatsapp || []).map((group) =>
    tickRow(group.name, group.ticked, group.kids.length ? { text: group.kids.join(", ") } : null)));
  document.getElementById("check-senders").replaceChildren(...senders.map((sender) =>
    tickRow(sender.domain, sender.ticked, sender.count
      ? { key: "check.sender.mails", values: { count: sender.count, example: sender.example } }
      : { key: "check.sender.no-mail" })));
  // Without school mail among what came in, the Briefs come out empty, unless another Source
  // brings the news and Gmail only sends the Brief.
  const none = document.getElementById("check-senders-none");
  none.hidden = senders.some((s) => s.ticked && s.count > 0);
  setText(none, page.check.other_sources ? "check.senders.none.other" : "check.senders.none");
  document.getElementById("check-recipients").replaceChildren(...recipients.map(recipientRow));
  document.getElementById("check-evening").value = evening;
  // A pilot Household's feedback goes under this name in the team's sheet.
  document.getElementById("check-feedback").hidden = feedback === null;
  const label = document.getElementById("check-household-label");
  label.required = feedback !== null;
  label.value = feedback ? feedback.household_label : "";
  syncAll("check-whatsapp-all", "check-groups");
  syncAll("check-senders-all", "check-senders");
}

// A Kid, ticked, with the name the Brief calls them in a field of its own.
// A Kid added on the page is named by the name they're called.
function kidRow({ name, everyday_name: called }) {
  const row = document.createElement("div");
  row.className = "kid";
  const label = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.value = name;
  input.checked = true;
  const shown = document.createElement("span");
  shown.className = "name";
  shown.textContent = name;
  label.append(input, shown);
  const everyday = document.createElement("input");
  everyday.type = "text";
  everyday.className = "everyday";
  everyday.value = called;
  everyday.autocomplete = "off";
  everyday.dataset.label = "check.kid.everyday";
  everyday.dataset.values = JSON.stringify({ name });
  everyday.setAttribute("aria-label", fill(t("check.kid.everyday"), { name }));
  row.append(label, everyday);
  return row;
}

// One item of a list to tick, with what it is beside it: a text, or a text to show.
function tickRow(value, ticked, detail) {
  const label = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.value = value;
  input.checked = ticked;
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = value;
  label.append(input, name);
  if (detail) {
    const about = document.createElement("span");
    about.className = "town";
    if (detail.key) setText(about, detail.key, detail.values);
    else about.textContent = detail.text;
    label.append(about);
  }
  return label;
}

function recipientRow({ address, language }, i) {
  const field = document.createElement("div");
  field.className = "field";
  const label = document.createElement("label");
  label.htmlFor = `check-recipient-${i}`;
  label.textContent = address;
  const select = document.createElement("select");
  select.id = label.htmlFor;
  select.dataset.address = address;
  const offered = [...page.languages];
  if (!offered.some((l) => l.code === language)) offered.push({ code: language, name: language });
  for (const { code, name } of offered) {
    const option = document.createElement("option");
    option.value = code;
    option.lang = code;
    option.textContent = name;
    select.append(option);
  }
  select.value = language;
  field.append(label, select);
  return field;
}

// "All" ticks or unticks the whole list, and is ticked itself while every item is.
function allOption(allId, listId) {
  const all = document.getElementById(allId);
  const list = document.getElementById(listId);
  all.addEventListener("change", () => {
    for (const box of list.querySelectorAll('input[type="checkbox"]')) box.checked = all.checked;
  });
  list.addEventListener("change", () => syncAll(allId, listId));
}

function syncAll(allId, listId) {
  const boxes = [...document.getElementById(listId).querySelectorAll('input[type="checkbox"]')];
  document.getElementById(allId).checked = boxes.length > 0 && boxes.every((b) => b.checked);
}

// A Kid Wilma didn't list, by the name the family calls them.
function addCheckKid() {
  clearError();
  const input = document.getElementById("check-kid-name");
  const name = input.value.trim();
  if (!name) return;
  const rows = [...document.querySelectorAll("#check-kids .kid")];
  const taken = rows.some((row) => [row.querySelector('input[type="checkbox"]').value,
    row.querySelector("input.everyday").value].some((n) => n.trim().toLowerCase() === name.toLowerCase()));
  if (taken) return showError("check.kid-exists");
  document.getElementById("check-kids").append(kidRow({ name, everyday_name: name }));
  input.value = "";
}

function ticked(listId) {
  return [...document.querySelectorAll(`#${listId} input[type="checkbox"]:checked`)]
    .map((box) => box.value);
}

async function confirmCheck(event) {
  event.preventDefault();
  clearError();
  const kids = [...document.querySelectorAll("#check-kids .kid")]
    .map((row) => [row.querySelector('input[type="checkbox"]'), row.querySelector("input.everyday")])
    .filter(([box]) => box.checked)
    .map(([box, everyday]) => ({ name: box.value, everyday_name: everyday.value.trim() }));
  const answers = {
    kids,
    whatsapp: page.check.whatsapp === null ? null : ticked("check-groups"),
    senders: ticked("check-senders"),
    recipients: [...document.querySelectorAll("#check-recipients select")]
      .map((select) => ({ address: select.dataset.address, language: select.value })),
    evening: document.getElementById("check-evening").value,
  };
  if (page.check.feedback !== null) {
    answers.household_label = document.getElementById("check-household-label").value.trim();
  }
  const button = event.submitter;
  button.disabled = true;
  try {
    const r = await fetch("api/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(answers),
    });
    const out = await r.json();
    if (r.status === 400) return showError("check.invalid");
    if (!r.ok && out.result !== "checking") throw new Error(out.result);
    // Saved now, so Finish says the evening that was chosen, not the one the page loaded with.
    page.check.evening = answers.evening;
    healthResult(out);
  } catch (e) {
    failed(e);
  } finally {
    button.disabled = checkStep.checking;
  }
}

async function checkHealth() {
  try {
    healthResult(await post("api/check/health", {}));
  } catch (e) {
    failed(e);
  }
}

// Picks up a health check still running, or how the last one failed, after a reload.
async function resumeHealth() {
  try {
    const out = await post("api/check/health", {});
    if (out.result === "checking" || out.result === "not-ok") healthResult(out);
  } catch (e) {
    failed(e);
  }
}

// How the health check went: each check by name, and for each that failed, what to do.
// Once it's all OK, setup moves on.
function healthResult(out) {
  clearTimeout(checkStep.timer);
  checkStep.checking = out.result === "checking";
  document.getElementById("check-confirm").disabled = checkStep.checking;
  if (out.result === "ok") {
    page.progress = out.progress;
    show();
    return;
  }
  const box = document.getElementById("health");
  box.hidden = out.result === "no-check";
  box.dataset.result = "health." + out.result;
  setText(document.getElementById("health-message"), "health." + out.result);
  document.getElementById("health-checks").replaceChildren(...(out.checks || []).map((check) => {
    const item = document.createElement("li");
    item.dataset.status = check.status;
    const language = page.languages.find((l) => l.code === check.language);
    const values = { kid: check.kid, language: language ? language.name : check.language };
    const name = document.createElement("span");
    setText(name, "health.check." + check.check, values);
    const status = document.createElement("span");
    status.className = "state";
    setText(status, "health.status." + check.status);
    item.append(name, " ", status);
    if (check.status !== "ok") item.append(...nextStep("health", check, values));
    return item;
  }));
  if (checkStep.checking) checkStep.timer = setTimeout(checkHealth, HEALTH_MS);
}

// The button that fixes a WhatsApp check, by why it isn't OK: its text and what it does. One
// opens Full Disk Access, the other goes back to WhatsApp's step for groups not on this Mac.
// Once the page has closed, neither is shown.
const WHATSAPP_STEPS = {
  "no-access": ["whatsapp.open", openFullDiskAccessForCheck],
  "missing-groups": ["whatsapp.back", backToWhatsApp],
};

// What to do about a check that isn't OK, as the check page ("health") or Finish ("finish") says
// it: the text for why it isn't, where the check says why, else the check's own, and the button
// that fixes it, if there's one.
function nextStep(prefix, check, values) {
  const general = `${prefix}.fail.${check.check}`;
  const own = `${general}.${check.reason}`;
  const next = document.createElement("p");
  next.className = "hint";
  setText(next, check.reason && own in page.text[page.language] ? own : general,
    { ...values, groups: (check.groups || []).join(", "), ...pythonFile() });
  const step = check.check === "whatsapp" && WHATSAPP_STEPS[check.reason];
  if (!step) return [next];
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary";
  setText(button, step[0]);
  button.addEventListener("click", step[1]);
  return [next, button];
}

// As on WhatsApp's step, but here the page doesn't read WhatsApp again by itself: the text says
// to check again.
async function openFullDiskAccessForCheck() {
  clearError();
  try {
    const out = await post("api/whatsapp/open", {});
    if (out.result !== "opened") showError("whatsapp.open.not-opened");
  } catch (e) {
    failed(e);
  }
}

// Back to WhatsApp's step in Connect, which reads the groups again for Check. The page loads
// again there, since each step sets itself up only once.
async function backToWhatsApp(event) {
  clearError();
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await post("api/whatsapp/back", {});
    window.location.reload();
  } catch (e) {
    failed(e);
    button.disabled = false;
  }
}

// On Check and Finish, while a Source the list offers is skipped, the one link back to Connect's
// list, where it can be opened after all. A page opened again after setup is at Finish, so the
// same link adds a Source later.
function showBackToConnect(id) {
  const link = document.getElementById(id);
  link.hidden = !page.connect.sources.some(({ name }) => page.progress.sources[name] === "skipped");
  link.querySelector("button").addEventListener("click", backToConnect);
}

// As backToWhatsApp, to the list instead of one Source's step.
async function backToConnect(event) {
  clearError();
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await post("api/connect/back", {});
    window.location.reload();
  } catch (e) {
    failed(e);
    button.disabled = false;
  }
}

// ── First Brief: the real Brief, made from the Sources without sending it and shown as it
// will look in the inbox, then sent to the setup parent only.

const BRIEF_MS = 2000;
const briefStep = { timer: null };

function enterBrief() {
  document.getElementById("brief-again").addEventListener("click", makeBrief);
  document.getElementById("brief-send").addEventListener("click", sendBrief);
  document.getElementById("brief-feedback-open").addEventListener("click", openFeedback);
  document.getElementById("brief-done").addEventListener("click", briefDone);
  if (page.brief.sent) showSent("brief.sent"); // after a reload, once it has reached them
  checkBrief();
}

async function makeBrief() {
  clearError();
  try {
    briefResult(await post("api/brief", {}));
  } catch (e) {
    failed(e);
  }
}

async function checkBrief() {
  clearTimeout(briefStep.timer);
  let out;
  try {
    out = await post("api/brief/check", {});
  } catch (e) {
    failed(e);
    return;
  }
  if (out.result === "no-brief") return makeBrief(); // the first time, or after a restart
  briefResult(out);
}

// How far making the Brief is, and once it's made, the Brief in its frame.
function briefResult(out) {
  clearTimeout(briefStep.timer);
  const making = out.result === "making";
  const made = out.result === "made";
  renderBriefList(making ? out : null);
  document.getElementById("brief-status").hidden = false;
  setText(document.getElementById("brief-message"), "brief." + out.result);
  document.getElementById("brief-again").hidden = out.result !== "make-failed";
  const frame = document.getElementById("brief-frame");
  if (made) frame.src = "brief.html?" + Date.now(); // the one just made, not one kept from before
  frame.hidden = !made;
  document.getElementById("brief-actions").hidden = !made;
  document.getElementById("brief-feedback").hidden = !(made && out.feedback);
  if (making) briefStep.timer = setTimeout(checkBrief, BRIEF_MS);
}

// Each Source as it's read, then the writing.
function renderBriefList(making) {
  const list = document.getElementById("brief-list");
  list.hidden = !making;
  if (!making) return;
  const steps = [...(making.sources || []), "writing"];
  const at = steps.indexOf(making.step);
  list.replaceChildren(...steps.map((step, i) => {
    const item = document.createElement("li");
    item.dataset.status = i < at ? "done" : "to-do";
    if (i === at) item.setAttribute("aria-current", "step");
    const line = document.createElement("span");
    if (step === "writing") setText(line, "brief.writing");
    else setText(line, "brief.reading", { source: STEPS[step]?.label || step });
    item.append(line);
    return item;
  }));
}

async function sendBrief(event) {
  clearError();
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const out = await post("api/brief/send", {});
    if (out.result === "sent") showSent("brief.send.sent", { to: out.to });
    else showError("brief.send." + out.result);
  } catch (e) {
    failed(e);
  } finally {
    button.disabled = false;
  }
}

function showSent(key, values) {
  setText(document.getElementById("brief-sent-message"), key, values);
  document.getElementById("brief-sent").hidden = false;
}

async function openFeedback() {
  clearError();
  try {
    const out = await post("api/brief/feedback", {});
    if (out.result !== "opened") showError("brief.feedback.not-opened");
  } catch (e) {
    failed(e);
  }
}

async function briefDone(event) {
  clearError();
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const out = await post("api/brief/done", {});
    page.progress = out.progress;
    show();
  } catch (e) {
    failed(e);
  } finally {
    button.disabled = false;
  }
}

// ── Finish: the evening job and the Mac's wake-up, with the Mac password typed into macOS's own
// dialog, then the outcome checklist, with what's missing under each outcome that isn't true.
// Once every outcome is true, the server stops, and the page shows only the checklist; after a
// try that wasn't, "Finish for now" stops it too.

const FINISH_MS = 2000;
const finishStep = { timer: null };

function enterFinish() {
  document.getElementById("finish-start").addEventListener("click", () => startFinish(false));
  document.getElementById("finish-replace").addEventListener("click", () => startFinish(true));
  document.getElementById("finish-again").addEventListener("click", checkAgain);
  document.getElementById("finish-stop").addEventListener("click", stopForNow);
  document.getElementById("finish-ai-open").addEventListener("click", openFinishAI);
  document.getElementById("finish-ai-form").addEventListener("submit", signInFinishAI);
  showBackToConnect("finish-back");
  showHouseholdAI();
  checkFinish();
}

// What depends on the Household's one AI: who writes the Briefs, and where changes go.
function showHouseholdAI() {
  setText(document.getElementById("finish-ai-current"), "finish.ai.current." + page.welcome.ai);
  setText(document.getElementById("finish-changes"), "finish.changes." + page.welcome.ai);
  showChat();
}

// ── Finish: changing the AI, for one that stopped working, or a new Gateway Key (ADR 0017). The
// choices are Welcome's and the sign-ins Connect's own, moved here; the server makes the AI the
// Household's only once its test call passes, so a failed try leaves the one it has.

const finishAI = { name: null, ready: false };

function openFinishAI() {
  clearError();
  // Connect sets up its sign-ins' buttons; a page opened at Finish hasn't been there.
  if (!page.connectShown && !finishAI.ready) {
    renderAI();
    document.getElementById("source-copy").addEventListener("click", () => {
      navigator.clipboard.writeText(document.getElementById("source-install-line").textContent);
    });
  }
  finishAI.ready = true;
  document.getElementById("finish-ai-sign-in").append(
    document.getElementById("source-ai"), document.getElementById("source-status"));
  offerAIs("finish-ai");
  pick("finish-ai", page.welcome.ai);
  document.getElementById("finish-ai-open").hidden = true;
  document.getElementById("finish-ai-saved").hidden = true;
  document.getElementById("finish-ai-form").hidden = false;
}

// Shows the sign-in of the AI picked, which Codex's starts by itself, as in Connect.
function signInFinishAI(event) {
  event.preventDefault();
  clearError();
  clearTimeout(aiStep.timer);
  const name = picked("finish-ai");
  finishAI.name = name;
  showSource(null);
  document.getElementById("claude-token").value = "";
  document.getElementById("gateway-key").value = "";
  document.getElementById("claude-form").hidden = true;
  document.getElementById("codex-login").hidden = true;
  document.getElementById("codex-download").hidden = true;
  document.getElementById("codex-install").hidden = true;
  document.getElementById("source-ai").hidden = false;
  document.getElementById("ai-claude").hidden = name !== "claude";
  document.getElementById("ai-codex").hidden = name !== "codex";
  document.getElementById("ai-gateway").hidden = name !== "gateway";
  document.getElementById("finish-ai-sign-in").hidden = false;
  if (name === "codex") {
    showSource("codex.checking");
    checkCodex();
  }
}

// The new AI answered its test call and is the Household's: the outcomes are checked again.
async function switchedAI() {
  clearTimeout(aiStep.timer);
  page.welcome.ai = finishAI.name;
  showSource(null);
  document.getElementById("finish-ai-sign-in").hidden = true;
  document.getElementById("finish-ai-form").hidden = true;
  document.getElementById("finish-ai-open").hidden = false;
  document.getElementById("finish-ai-saved").hidden = false;
  showHouseholdAI();
  try {
    finishResult(await post("api/finish/outcomes", {}));
  } catch (e) {
    failed(e);
  }
}

async function startFinish(replaceWake) {
  clearError();
  try {
    finishResult(await post("api/finish", { replace_wake: replaceWake }));
  } catch (e) {
    failed(e);
  }
}

// "Check again": the outcomes, the health check among them, checked once more without
// installing anything or setting the wake-up, and the checklist updated in place.
async function checkAgain() {
  clearError();
  try {
    finishResult(await post("api/finish/outcomes", { again: true }));
  } catch (e) {
    failed(e);
  }
}

// How Finish is going. The first time, or after a restart, it checks what's set up already,
// without installing anything: a Household that has finished sees only the checklist.
async function checkFinish() {
  clearTimeout(finishStep.timer);
  try {
    let out = await post("api/finish/check", {});
    if (out.result === "no-finish") out = await post("api/finish/outcomes", {});
    finishResult(out);
  } catch (e) {
    failed(e);
  }
}

function finishResult(out) {
  clearTimeout(finishStep.timer);
  const running = out.result === "installing" || out.result === "checking";
  const done = out.result === "done";
  // Checked before anything is installed: the intro says what to do, not what isn't set up.
  const tried = out.result === "install-failed"
    || (out.result === "not-done" && Boolean(out.wake || out.tried));
  const status = document.getElementById("finish-status");
  status.hidden = out.result === "not-done" && !tried;
  status.dataset.result = "finish." + out.result;
  setText(document.getElementById("finish-message"), "finish." + out.result,
    { evening: page.check.evening });
  const wake = document.getElementById("finish-wake");
  wake.hidden = !out.wake;
  if (out.wake) setText(wake, "finish.wake." + out.wake);
  const other = document.getElementById("finish-other");
  other.replaceChildren(...(out.other || []).map((line) => {
    const item = document.createElement("li");
    item.textContent = line;
    return item;
  }));
  other.hidden = !out.other;
  document.getElementById("finish-replace").hidden = out.wake !== "other-schedule" || running;
  document.getElementById("finish-install").hidden = running || done;
  // Only once a try has left something failing: before Turn it on, it's the one thing to press.
  document.getElementById("finish-again").hidden = out.result !== "not-done" || !tried;
  document.getElementById("finish-stop").hidden = !tried;
  if (out.outcomes) renderChecklist(out.outcomes, tried);
  // Opened again after setup with a Source skipped, the server stays, for the link back.
  if (done && !out.stays) closed();
  if (running) finishStep.timer = setTimeout(checkFinish, FINISH_MS);
}

// What Turn it on does: until it's been pressed, these aren't true yet, and that's no failure.
const TURN_ON_DOES = { outcomes: ["nightly", "wake"], checks: ["schedule"] };

// Each outcome, ticked once it's true. After a try, each that isn't says what's missing and
// what to do, and a health check that isn't OK names its checks, as the check page does. A
// health check with only warnings is true, and always lists them with what to do. Before a try,
// what Turn it on does is only still to do.
function renderChecklist(outcomes, tried) {
  document.getElementById("finish-checklist").replaceChildren(...outcomes.map((o) => {
    const item = document.createElement("li");
    const toDo = !o.ok && !tried && TURN_ON_DOES.outcomes.includes(o.outcome);
    item.dataset.status = o.ok ? "ok" : toDo ? "to-do" : "fail";
    const line = document.createElement("span");
    setText(line, "finish.outcome." + o.outcome);
    item.append(line);
    const checks = tried ? o.checks
      : (o.checks || []).filter((c) => !TURN_ON_DOES.checks.includes(c.check));
    if (toDo) {
      const later = document.createElement("p");
      later.className = "hint";
      setText(later, "finish.to-do." + o.outcome);
      item.append(later);
    }
    if (o.ok && checks && checks.length) {
      const warnings = document.createElement("p");
      warnings.className = "hint";
      setText(warnings, "finish.warnings");
      item.append(warnings, failedChecks(checks));
    }
    if (o.ok || !tried) return item;
    const missing = document.createElement("p");
    missing.className = "hint";
    setText(missing, "finish.missing." + o.outcome);
    item.append(missing);
    if (o.checks) item.append(failedChecks(o.checks));
    return item;
  }));
}

function failedChecks(checks) {
  const list = document.createElement("ul");
  list.className = "checks";
  list.replaceChildren(...checks.map((check) => {
    const item = document.createElement("li");
    item.dataset.status = check.status;
    const language = page.languages.find((l) => l.code === check.language);
    const values = { kid: check.kid, language: language ? language.name : check.language };
    const name = document.createElement("span");
    setText(name, "health.check." + check.check, values);
    const status = document.createElement("span");
    status.className = "state";
    setText(status, "health.status." + check.status);
    item.append(name, " ", status, ...nextStep("finish", check, values));
    return item;
  }));
  return list;
}

// "Finish for now": the server stops with setup not done, and the page says how to come back.
async function stopForNow(event) {
  clearError();
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const out = await post("api/finish/stop", {});
    if (out.result !== "stopped") return checkFinish(); // still installing or checking
    // What the last try said offers buttons the page no longer has.
    document.getElementById("finish-install").hidden = true;
    document.getElementById("finish-status").hidden = true;
    setText(document.getElementById("finish-stopped-message"), "finish.stopped." + page.welcome.ai);
    document.getElementById("finish-stopped").hidden = false;
    closed();
  } catch (e) {
    failed(e);
  } finally {
    button.disabled = false;
  }
}

// The server has stopped: nothing more to do on this page.
function closed() {
  page.finished = true;
  document.getElementById("phases").hidden = true;
  document.getElementById("chat").hidden = true;
  for (const button of document.querySelectorAll("#finish-checklist button")) button.hidden = true;
  clearTimeout(aiStep.timer);
  for (const id of ["finish-ai-open", "finish-ai-form", "finish-ai-sign-in", "finish-back"]) {
    document.getElementById(id).hidden = true;
  }
  document.getElementById("finish-closed").hidden = false;
}

// ── Continue in the chat: Claude Code opens at the setup skill in Terminal, or the page says
// what to type in Codex. The skill carries on from the step saved here.

function renderChat() {
  document.getElementById("chat-continue").addEventListener("click", continueInChat);
  document.getElementById("chat-copy").addEventListener("click", () => {
    navigator.clipboard.writeText(document.getElementById("chat-line-text").textContent);
  });
}

// The AI the chat carries on with: on Welcome, the one picked there, even before it's saved.
function chatAI() {
  return page.progress.phase === "welcome" ? picked("ai") : page.welcome.ai;
}

// A gateway Household has neither Claude nor ChatGPT to carry on with in a chat.
function showChat() {
  // The chat skills set up a Mac, so Windows has no chat to continue in yet.
  document.getElementById("chat").hidden = !page.chosen || page.finished || chatAI() === "gateway"
    || page.system === "windows";
}

async function continueInChat(event) {
  clearError();
  const button = event.currentTarget;
  button.disabled = true;
  const name = chatAI();
  try {
    const out = await post("api/chat", { ai: name });
    const message = document.getElementById("chat-message");
    message.dataset.text = `chat.${name}.${out.result}`;
    message.textContent = t(message.dataset.text);
    const line = out.type || out.install;
    document.getElementById("chat-line").hidden = !line;
    document.getElementById("chat-line-text").textContent = line || "";
    document.getElementById("chat-status").hidden = false;
  } catch (e) {
    failed(e);
  } finally {
    button.disabled = false;
  }
}

function show() {
  const phase = page.chosen ? page.progress.phase : null;
  document.getElementById("language").hidden = Boolean(page.chosen);
  document.getElementById("phases").hidden = !page.chosen || page.finished;
  document.getElementById("welcome").hidden = phase !== "welcome";
  document.getElementById("connect").hidden = phase !== "connect";
  document.getElementById("working").hidden = phase !== "working";
  document.getElementById("check").hidden = phase !== "check";
  document.getElementById("first-brief").hidden = phase !== "first-brief";
  document.getElementById("finish").hidden = phase !== "finish";
  if (phase === "welcome" && !page.welcomeShown) {
    page.welcomeShown = true;
    renderWelcome();
  }
  if (phase === "connect" && !page.connectShown) {
    page.connectShown = true;
    renderConnect();
  }
  if (phase === "working" && !page.workingShown) {
    page.workingShown = true;
    enterWorking();
  }
  if (phase === "check" && !page.checkShown) {
    page.checkShown = true;
    enterCheck();
  }
  if (phase === "first-brief" && !page.briefShown) {
    page.briefShown = true;
    enterBrief();
  }
  if (phase === "finish" && !page.finishShown) {
    page.finishShown = true;
    enterFinish();
  }
  showChat(); // once Welcome's choices are on the page
  applyText();
}

async function start() {
  const [text, state] = await Promise.all([getJSON("text.json"), getJSON("api/state")]);
  page.text = text;
  page.languages = state.languages;
  page.system = state.system;
  page.ais = state.ais;
  page.chosen = state.language;
  page.language = state.language || state.preselected;
  page.progress = state.progress;
  page.welcome = state.welcome;
  page.connect = state.connect;
  page.gmail = state.gmail;
  page.whatsapp = state.whatsapp;
  page.myclub = state.myclub;
  page.check = state.check;
  page.brief = state.brief;
  // Said before the family types a password: in tmux or SSH, none can be saved.
  document.getElementById("keychain-unreachable").hidden = state.keychain.reachable;
  renderSwitch();
  renderChoices();
  renderChat();
  show();
}

// Without the text table there's no language yet, so this says it in all three.
const CLOSED = [
  "Käyttöönottosivu on suljettu. Käynnistä se uudelleen Terminalissa.",
  "The setup page has closed. Start it again in Terminal.",
  "设置页面已经关闭。请在终端里重新启动它。",
];

start().catch(() => {
  document.body.replaceChildren(...CLOSED.map((line) => {
    const p = document.createElement("p");
    p.textContent = line;
    return p;
  }));
});
