"use strict";

// The setup page. Every address here is relative, so it keeps the page's one-time code.
const PHASES = ["welcome", "connect", "working", "check", "first-brief", "finish"];
const page = {
  text: null, language: "en", chosen: null, languages: [], progress: null, welcome: null,
  connect: null, gmail: null, myclub: null, check: null, brief: null, welcomeShown: false,
  connectShown: false, workingShown: false, checkShown: false, briefShown: false,
  finishShown: false, finished: false,
};

function t(key) {
  return page.text[page.language][key];
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
  if (page.connectShown) renderSources();
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

function renderWelcome() {
  const { ai: chosen, partner, feedback } = page.welcome;
  pick("ai", chosen);
  pick("partner", partner.add ? "add" : "only-me");
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

function showAI(name, result, install) {
  ai.result = result;
  const status = document.getElementById("ai-status");
  status.dataset.result = result;
  const message = document.getElementById("ai-message");
  message.dataset.text = result === "checking" ? "welcome.ai.checking" : `ai.${name}.${result}`;
  message.textContent = t(message.dataset.text);
  document.getElementById("ai-install").hidden = !install;
  document.getElementById("ai-install-line").textContent = install || "";
  document.getElementById("ai-again").hidden = result === "ready" || result === "checking";
  document.getElementById("welcome-continue").disabled = result !== "ready";
}

async function saveWelcome(event) {
  event.preventDefault();
  clearError();
  if (ai.result !== "ready") return;
  const button = event.submitter;
  button.disabled = true;
  const add = picked("partner") === "add";
  const answers = {
    ai: picked("ai"),
    partner: add ? {
      address: document.getElementById("partner-address").value.trim(),
      language: document.getElementById("partner-language").value,
    } : null,
    feedback: picked("feedback") === "yes",
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

function renderConnect() {
  document.getElementById("source-skip").addEventListener("click", () => {
    chooseSource(currentSource(), "skip");
  });
  document.getElementById("source-copy").addEventListener("click", () => {
    navigator.clipboard.writeText(document.getElementById("source-install-line").textContent);
  });
  renderWilma();
  renderAI();
  document.getElementById("whatsapp-open").addEventListener("click", openAppManagement);
  document.getElementById("myclub-open").addEventListener("click", () => openSite("myclub"));
  document.getElementById("myclub-form").addEventListener("submit", saveMyClubLink);
  document.getElementById("myclub-kid-form").addEventListener("submit", addKid);
  document.getElementById("myclub-continue").addEventListener("click", leaveMyClub);
  document.getElementById("gmail-open").addEventListener("click", () => openSite("app-passwords"));
  document.getElementById("gmail-two-step").addEventListener("click", () => openSite("two-step"));
  document.getElementById("gmail-unavailable").addEventListener("click", () => {
    showSource("gmail.app-passwords-unavailable");
  });
  document.getElementById("gmail-form").addEventListener("submit", connectGmail);
  document.getElementById("gmail-address").value = page.gmail.address || "";
  document.getElementById("connect-continue").addEventListener("click", startReading);
}

// The Source the parent is on: the one saved, else the first still to do (null once none is).
function currentSource() {
  const statuses = page.progress.sources;
  return page.progress.source ??
    page.connect.sources.map((s) => s.name).find((name) => statuses[name] === "to-do") ?? null;
}

// The Source the list last showed as current, and the hand-off once one was connected: the
// Source just done and the next one (null after the last).
const connectStep = { current: undefined, handoff: null };

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
  // Wilma's own step asks whether the school uses it, in place of the skip button.
  const skippable = page.connect.sources.find((s) => s.name === current)?.skippable;
  document.getElementById("source-skip").hidden =
    !skippable || statuses[current] !== "to-do" || current === "wilma";
  document.getElementById("source-wilma").hidden = current !== "wilma";
  document.getElementById("source-gmail").hidden = current !== "gmail";
  document.getElementById("source-ai").hidden = current !== "ai";
  document.getElementById("ai-claude").hidden = page.welcome.ai !== "claude";
  document.getElementById("ai-codex").hidden = page.welcome.ai !== "codex";
  document.getElementById("source-whatsapp").hidden = current !== "whatsapp";
  document.getElementById("source-myclub").hidden = current !== "myclub";
  document.getElementById("connect-done").hidden = Boolean(current);
  renderHandoff(moved);
  if (current === "wilma" && !wilmaStep.ready && !wilmaStep.preparing) getWilmaReady();
  if (current === "ai" && !aiStep.shown) {
    aiStep.shown = true;
    if (page.welcome.ai === "codex" && statuses.ai === "to-do") {
      showSource("codex.checking");
      checkCodex();
    }
    if (page.welcome.ai === "claude") resumeClaude();
  }
  if (current === "whatsapp" && !whatsappStep.shown) {
    whatsappStep.shown = true;
    showSource("whatsapp.checking");
    checkWhatsApp();
  }
  if (current === "myclub" && !myclubStep.shown) {
    myclubStep.shown = true;
    refreshMyClub();
  } else if (current === "myclub") {
    renderMyClub();
  }
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
    // Not kept once the parent moves on.
    document.getElementById("gmail-password").value = "";
    document.getElementById("wilma-password").value = "";
    document.getElementById("claude-token").value = "";
    document.getElementById("myclub-link").value = "";
    clearTimeout(wilmaStep.timer);
    clearTimeout(aiStep.timer);
    aiStep.shown = false;
    myclubStep.shown = false;
    leaveWhatsApp();
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
  "wilma.ready.no-npm", "wilma.ready.install-failed", "wilma.not-installed",
  "wilma.window.not-installed",
];

// What the last step said, under the Source: a text key, or null for nothing, with an address
// to open, the Kids found or a line to install something.
function showSource(key, { url, kids, install } = {}) {
  const status = document.getElementById("source-status");
  const message = document.getElementById("source-message");
  status.hidden = !key;
  status.dataset.result = key || "";
  if (key) {
    message.dataset.text = key;
    message.textContent = t(key);
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
  document.getElementById("wilma-terminal").addEventListener("click", openWilmaWindow);
  document.getElementById("wilma-again").addEventListener("click", getWilmaReady);
  showWilmaUses();
}

// With Wilma, the login fields; without, only the town.
function showWilmaUses() {
  const uses = picked("wilma-uses") === "yes";
  document.getElementById("wilma-form").hidden = !uses;
  document.getElementById("town-form").hidden = uses;
  renderTowns();
}

// Gets the pinned wilma CLI ready, installing it if it isn't: the town list comes with it.
async function getWilmaReady() {
  wilmaStep.preparing = true;
  showSource("wilma.ready.installing");
  try {
    const out = await post("api/wilma/install", {});
    if (out.result !== "installed") {
      showSource("wilma.ready." + out.result, { install: out.install });
      return;
    }
    wilmaStep.ready = true;
    showSource(null);
    const search = document.getElementById("wilma-search");
    search.disabled = false;
    search.focus();
    if (search.value.trim()) searchTowns();
  } catch (e) {
    showSource(null);
    failed(e);
  } finally {
    wilmaStep.preparing = false;
  }
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
// without anything copied, or Codex's ChatGPT login. Each ticks itself once it's done.

const AI_MS = 3000;
const aiStep = { shown: false, timer: null };
// After these the page offers Claude's Terminal window, or to sign in again.
const CLAUDE_TERMINAL = ["claude.sign-in-failed", "claude.timeout", "claude.window.no-terminal"];
const CLAUDE_AGAIN = [
  "claude.not-installed", "claude.test-call-failed", "claude.keychain-failed",
  "claude.window.not-installed",
];
// Codex's results after which the page checks again by itself.
const CODEX_AGAIN = ["codex.waiting", "codex.not-installed", "codex.check-failed"];

function renderAI() {
  document.getElementById("claude-start").addEventListener("click", signInClaude);
  document.getElementById("claude-again").addEventListener("click", signInClaude);
  document.getElementById("claude-terminal").addEventListener("click", openClaudeWindow);
  document.getElementById("claude-form").addEventListener("submit", saveClaudeToken);
  document.getElementById("codex-login").addEventListener("click", signInCodex);
}

// Once the AI sign-in is done, setup moves on to the next Source.
function aiDone(out, key) {
  page.progress = out.progress;
  document.getElementById("claude-form").hidden = true;
  renderSources();
  showSource(key);
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
// how an earlier one ended is old news.
async function resumeClaude() {
  try {
    const out = await post("api/claude/check", {});
    if (out.result === "waiting") claudeResult(out);
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
    const out = await post("api/claude/terminal", {});
    if (out.result === "opened") document.getElementById("claude-form").hidden = false;
    showSource("claude.window." + out.result, { install: out.install });
  } catch (e) {
    failed(e);
  }
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

function codexResult(out) {
  const key = "codex." + out.result;
  document.getElementById("codex-login").hidden =
    !["codex.signed-out", "codex.login-failed"].includes(key);
  if (out.result === "signed-in") return aiDone(out, key);
  showSource(key);
  if (CODEX_AGAIN.includes(key)) aiStep.timer = setTimeout(checkCodex, AI_MS);
}

// ── Connect: WhatsApp, read with the evening job's own permission. The page reads again every
// few seconds, so the entry ticks itself once the parent has given the permission.

const WHATSAPP_MS = 5000;
const whatsappStep = { shown: false, opened: false, checking: 0, timer: null };

function leaveWhatsApp() {
  clearTimeout(whatsappStep.timer);
  whatsappStep.checking++; // a read still on its way is old news
  whatsappStep.shown = false;
  whatsappStep.opened = false;
}

// Finder shows the Python to allow, and System Settings opens at App Management next to it.
async function openAppManagement() {
  clearError();
  try {
    const out = await post("api/whatsapp/open", {});
    whatsappStep.opened = out.result === "opened";
    showSource("whatsapp.open." + out.result);
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

const myclubStep = { shown: false, kid: null };

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
    input.addEventListener("change", () => { myclubStep.kid = name; });
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

// What's been read: the Sources Connect read, then Gmail's senders, as far as they've got.
function renderWorking() {
  const statuses = page.progress.sources;
  const reading = workingStep.reading || { result: "reading", read: 0, total: null };
  const items = [];
  if (statuses.wilma === "done") {
    items.push(["working.wilma", { kids: page.check.kids.map((k) => k.everyday_name).join(", ") }]);
  }
  if (statuses.whatsapp === "done") {
    items.push(["working.whatsapp", { count: (page.progress.whatsapp_chats || []).length }]);
  }
  if (statuses.myclub === "done") items.push(["working.myclub", null]);
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
  renderCheck();
  resumeHealth();
}

function renderCheck() {
  const { kids, whatsapp, senders, recipients, evening } = page.check;
  document.getElementById("check-kids").replaceChildren(...kids.map(kidRow));
  document.getElementById("check-whatsapp").hidden = whatsapp === null;
  document.getElementById("check-groups").replaceChildren(...(whatsapp || []).map((group) =>
    tickRow(group.name, group.ticked, group.kids.length ? { text: group.kids.join(", ") } : null)));
  document.getElementById("check-senders").replaceChildren(...senders.map((sender) =>
    tickRow(sender.domain, sender.ticked, sender.count
      ? { key: "check.sender.mails", values: { count: sender.count, example: sender.example } }
      : { key: "check.sender.no-mail" })));
  // Without school mail among what came in, the Briefs come out empty.
  document.getElementById("check-senders-none").hidden =
    senders.some((s) => s.ticked && s.count > 0);
  document.getElementById("check-recipients").replaceChildren(...recipients.map(recipientRow));
  document.getElementById("check-evening").value = evening;
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
    if (check.status !== "ok") {
      const next = document.createElement("p");
      next.className = "hint";
      setText(next, "health.fail." + check.check, values);
      item.append(next);
    }
    return item;
  }));
  if (checkStep.checking) checkStep.timer = setTimeout(checkHealth, HEALTH_MS);
}

// ── First Brief: the real Brief, made from the Sources without sending it and shown as it
// will look in the inbox, then sent to the setup parent only.

const BRIEF_MS = 2000;
const briefStep = { timer: null };
const SOURCE_NAMES = { gmail: "Gmail", myclub: "MyClub", wilma: "Wilma", whatsapp: "WhatsApp" };

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
    else setText(line, "brief.reading", { source: SOURCE_NAMES[step] || step });
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
  document.getElementById("finish-stop").addEventListener("click", stopForNow);
  setText(document.getElementById("finish-changes"), "finish.changes." + page.welcome.ai);
  checkFinish();
}

async function startFinish(replaceWake) {
  clearError();
  try {
    finishResult(await post("api/finish", { replace_wake: replaceWake }));
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
  const tried = out.result === "install-failed" || (out.result === "not-done" && Boolean(out.wake));
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
  document.getElementById("finish-stop").hidden = !tried;
  if (out.outcomes) renderChecklist(out.outcomes, tried);
  if (done) closed();
  if (running) finishStep.timer = setTimeout(checkFinish, FINISH_MS);
}

// Each outcome, ticked once it's true. After a try, each that isn't says what's missing and
// what to do, and a health check that isn't OK names its checks, as the check page does.
function renderChecklist(outcomes, explain) {
  document.getElementById("finish-checklist").replaceChildren(...outcomes.map((o) => {
    const item = document.createElement("li");
    item.dataset.status = o.ok ? "ok" : "fail";
    const line = document.createElement("span");
    setText(line, "finish.outcome." + o.outcome);
    item.append(line);
    if (o.ok || !explain) return item;
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
    const next = document.createElement("p");
    next.className = "hint";
    setText(next, "finish.fail." + check.check, values);
    item.append(name, " ", status, next);
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

async function continueInChat(event) {
  clearError();
  const button = event.currentTarget;
  button.disabled = true;
  // On Welcome, the AI picked there, even before it's saved.
  const name = page.progress.phase === "welcome" ? picked("ai") : page.welcome.ai;
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
  document.getElementById("chat").hidden = !page.chosen || page.finished;
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
  applyText();
}

async function start() {
  const [text, state] = await Promise.all([getJSON("text.json"), getJSON("api/state")]);
  page.text = text;
  page.languages = state.languages;
  page.chosen = state.language;
  page.language = state.language || state.preselected;
  page.progress = state.progress;
  page.welcome = state.welcome;
  page.connect = state.connect;
  page.gmail = state.gmail;
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
