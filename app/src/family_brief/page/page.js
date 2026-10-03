"use strict";

// The setup page. Every address here is relative, so it keeps the page's one-time code.
const PHASES = ["welcome", "connect", "working", "check", "first-brief", "finish"];
const page = {
  text: null, language: "en", chosen: null, languages: [], progress: null, welcome: null,
  connect: null, gmail: null, welcomeShown: false, connectShown: false,
};

function t(key) {
  return page.text[page.language][key];
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
    el.textContent = t(el.dataset.text);
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
    if (!page.chosen) return; // on the first page, Continue saves it
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
    item.textContent = t("phase." + phase);
    if (phase === current) item.setAttribute("aria-current", "step");
    else if (i < PHASES.indexOf(current)) item.className = "done";
    list.append(item);
  });
  document.getElementById("phase-title").textContent = t("phase." + current);
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

function renderSources() {
  const current = currentSource();
  const statuses = page.progress.sources;
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
    button.addEventListener("click", () => {
      if (name !== current || statuses[name] === "skipped") chooseSource(name, "open");
    });
    item.append(button);
    list.append(item);
  }
  const skippable = page.connect.sources.find((s) => s.name === current)?.skippable;
  document.getElementById("source-skip").hidden = !skippable || statuses[current] !== "to-do";
  document.getElementById("source-gmail").hidden = current !== "gmail";
  document.getElementById("source-later").hidden = !current || current === "gmail";
  document.getElementById("source-later-title").textContent = current ? t("source." + current) : "";
  document.getElementById("connect-done").hidden = Boolean(current);
}

// Skips the Source, or opens it from the list, which brings a skipped one back.
async function chooseSource(name, action) {
  clearError();
  try {
    const out = await post("api/source", { source: name, action });
    page.progress = out.progress;
    document.getElementById("gmail-password").value = ""; // not kept once the parent moves on
    showSource(null);
    renderSources();
  } catch (e) {
    failed(e);
  }
}

// What the last step said, under the Source: a text key, or null for nothing.
function showSource(key, url) {
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
  document.getElementById("gmail-two-step").hidden =
    !["gmail.app-passwords-unavailable", "gmail.rejected"].includes(key);
}

// The server opens the site, so the page itself names none.
async function openSite(site) {
  clearError();
  try {
    const out = await post("api/open", { site });
    if (out.result !== "opened") showSource("open.not-opened", out.url);
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

function show() {
  const phase = page.chosen ? page.progress.phase : null;
  document.getElementById("language").hidden = Boolean(page.chosen);
  document.getElementById("phases").hidden = !page.chosen;
  document.getElementById("welcome").hidden = phase !== "welcome";
  document.getElementById("connect").hidden = phase !== "connect";
  document.getElementById("phase").hidden = !phase || phase === "welcome" || phase === "connect";
  if (phase === "welcome" && !page.welcomeShown) {
    page.welcomeShown = true;
    renderWelcome();
  }
  if (phase === "connect" && !page.connectShown) {
    page.connectShown = true;
    renderConnect();
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
  renderSwitch();
  renderChoices();
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
