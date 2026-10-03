"use strict";

// The setup page. Every address here is relative, so it keeps the page's one-time code.
const PHASES = ["welcome", "connect", "working", "check", "first-brief", "finish"];
const page = {
  text: null, language: "en", chosen: null, languages: [], progress: null, welcome: null,
  welcomeShown: false,
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

function show() {
  const welcome = Boolean(page.chosen) && page.progress.phase === "welcome";
  document.getElementById("language").hidden = Boolean(page.chosen);
  document.getElementById("phases").hidden = !page.chosen;
  document.getElementById("welcome").hidden = !welcome;
  document.getElementById("phase").hidden = !page.chosen || welcome;
  if (welcome && !page.welcomeShown) {
    page.welcomeShown = true;
    renderWelcome();
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
