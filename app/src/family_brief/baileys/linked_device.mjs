// The Linked Device (ADR 0025): Parent Recap's own Node program, run by the pinned Node on the
// Baileys tree the install puts in Parent Recap's folder (baileys_install.py).
//
//   node linked_device.mjs pair   --modules TREE --auth FOLDER --log FILE [--timeout SECONDS]
//   node linked_device.mjs groups --modules TREE --auth FOLDER --log FILE [--timeout SECONDS]
//   node linked_device.mjs follow --modules TREE --auth FOLDER --log FILE
//                                 --picked FILE --store FILE --status FILE
//
// `pair` connects with no auth state and prints each QR string Baileys emits as a line of JSON,
// {"qr": "..."}, until the phone pairs. Only then is the auth state written to FOLDER, and
// {"paired": true} printed, so a pairing that never happens leaves none. `groups` connects with
// the auth state in FOLDER. Both then print {"groups": [{"id": "...", "subject": "..."}]} and
// exit; no message is kept.
//
// `follow` runs all day (ADR 0026) on the auth state in FOLDER. The picked groups are the ids in
// the `--picked` file, {"groups": ["...@g.us"]}, which Python writes; it is read again when it
// changes. Every message, as it arrives or in the history WhatsApp hands over at connect, whose
// chat isn't a picked group is dropped before anything is written; the rest are appended to the
// `--store` file, one line of JSON each: {"id", "group_id", "group_subject", "sender",
// "timestamp" (seconds), "text" (or a placeholder such as "[image]"), "from_me"}. The `--status`
// file is {"paired", "connected_since", "last_connected", "link_ended_at"}, times in ISO 8601.
// After a drop it connects again, after a pause that doubles with each drop up to five minutes.
// When WhatsApp ends the link it sets "link_ended_at" and exits with EXIT.linkEnded, as it does
// without auth state: the only exit the OS job doesn't start it again after (jobs.LINK_ENDED).
// Stopped by the OS, it exits with EXIT.stopped. The store and status are 0600 in folders made
// 0700.
//
// It only reads: no send, read-receipt or presence call is ever made. Delivery receipts and
// presence nodes Baileys sends on its own are accepted (#181). Log lines go to FILE, never to
// stdout, and never hold a message's text: Baileys' own lines are kept as their fixed wording,
// without the objects it logs with them.
//
// linked_device.py names its exit codes, EXIT below, for Python.

import { appendFileSync } from "node:fs";
import { chmod, mkdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

// follow's linkEnded is 0, since launchd tells only a successful exit from the rest; any other
// way it ends, a crash included, has another code, so the OS job starts it again.
const EXIT = { failed: 1, usage: 2, timedOut: 3, needsPairing: 4, stopped: 5, linkEnded: 0 };
// The phone lists a linked device by the first of these.
const BROWSER = ["Parent Recap", "Chrome", "1.0"];
// How long pair and groups wait; follow doesn't stop.
const SECONDS = { pair: 300, groups: 60, follow: null };
const RECONNECT_MS = 2000;
const MOST_RECONNECT_MS = 5 * 60_000;
// What the store keeps for a message without text, by its content type; others aren't kept.
const PLACEHOLDERS = {
  imageMessage: "[image]",
  videoMessage: "[video]",
  ptvMessage: "[video]",
  audioMessage: "[voice]",
  contactMessage: "[contact]",
  contactsArrayMessage: "[contact]",
  locationMessage: "[location]",
  liveLocationMessage: "[live location]",
  documentMessage: "[document]",
  stickerMessage: "[sticker]",
  pollCreationMessage: "[poll]",
  pollCreationMessageV2: "[poll]",
  pollCreationMessageV3: "[poll]",
};

let logFile;

function log(level, text) {
  if (logFile) {
    appendFileSync(logFile, `${new Date().toISOString()} ${level} ${text}\n`, { mode: 0o600 });
  }
}

function say(line) {
  process.stdout.write(JSON.stringify(line) + "\n");
}

// Once stdout has written everything, which on a pipe can be after process.exit would cut it.
function finish(code) {
  process.stdout.write("", () => process.exit(code));
}

// Baileys' logger: pino's interface, with only warnings and errors kept, as their wording alone.
function baileysLogger() {
  const levels = { trace: 10, debug: 20, info: 30, warn: 40, error: 50, fatal: 60 };
  const logger = { level: "warn", child: () => logger };
  for (const [name, rank] of Object.entries(levels)) {
    logger[name] = (fields, wording) => {
      if (rank < levels.warn) return;
      const text = typeof fields === "string" ? fields : wording;
      log(name, `baileys: ${typeof text === "string" ? text : "(no wording)"}`);
    };
  }
  return logger;
}

// The tree's Baileys, imported by its package's own entry point: an import by name would look
// next to this program, not in the tree.
async function loadBaileys(tree) {
  const root = join(tree, "node_modules", "@whiskeysockets", "baileys");
  const pkg = JSON.parse(await readFile(join(root, "package.json"), "utf8"));
  let entry = pkg.exports;
  if (entry && typeof entry === "object" && "." in entry) entry = entry["."];
  while (entry && typeof entry === "object") entry = entry.import ?? entry.node ?? entry.default;
  return import(pathToFileURL(join(root, entry ?? pkg.main ?? "index.js")).href);
}

async function writePrivate(file, text) {
  const partial = `${file}.${process.pid}.${Math.random().toString(36).slice(2)}.partial`;
  await writeFile(partial, text, { mode: 0o600 });
  await rename(partial, file);
}

async function makePrivateFolder(folder) {
  await mkdir(folder, { recursive: true, mode: 0o700 });
  await chmod(folder, 0o700);
}

// The auth state, laid out as Baileys' useMultiFileAuthState lays it out, but with every file
// 0600 in a 0700 folder. While pairing it is kept in memory, and `keep` writes it to the folder.
function authState(baileys, folder, onDisk) {
  const { BufferJSON, initAuthCreds, proto } = baileys;
  const memory = new Map(); // file name -> value, null once removed
  const fileName = (type, id) => `${type}-${id}.json`.replace(/\//g, "__").replace(/:/g, "-");

  async function read(name) {
    if (memory.has(name)) return memory.get(name);
    if (!onDisk) return null;
    try {
      const value = JSON.parse(await readFile(join(folder, name), "utf8"), BufferJSON.reviver);
      memory.set(name, value);
      return value;
    } catch (e) {
      if (e.code === "ENOENT") return null;
      throw e;
    }
  }

  async function write(name, value) {
    memory.set(name, value);
    if (!onDisk) return;
    if (value === null) await rm(join(folder, name), { force: true });
    else await writePrivate(join(folder, name), JSON.stringify(value, BufferJSON.replacer));
  }

  const state = {
    creds: null,
    keys: {
      async get(type, ids) {
        const found = {};
        await Promise.all(ids.map(async (id) => {
          let value = await read(fileName(type, id));
          if (type === "app-state-sync-key" && value) {
            value = proto.Message.AppStateSyncKeyData.fromObject(value);
          }
          found[id] = value;
        }));
        return found;
      },
      async set(data) {
        const writes = [];
        for (const [type, values] of Object.entries(data)) {
          for (const [id, value] of Object.entries(values)) {
            writes.push(write(fileName(type, id), value ?? null));
          }
        }
        await Promise.all(writes);
      },
    },
  };

  return {
    state,
    async load() {
      state.creds = onDisk ? await read("creds.json") : initAuthCreds();
      return state.creds !== null;
    },
    saveCreds: () => write("creds.json", state.creds),
    // Writes what pairing made into the folder in one move, in place of whatever was there.
    async keep() {
      await mkdir(join(folder, ".."), { recursive: true, mode: 0o700 });
      const staging = `${folder}.${process.pid}.pairing`;
      await rm(staging, { recursive: true, force: true });
      await makePrivateFolder(staging);
      memory.set("creds.json", state.creds);
      for (const [name, value] of memory) {
        if (value !== null) {
          await writePrivate(join(staging, name), JSON.stringify(value, BufferJSON.replacer));
        }
      }
      await rm(folder, { recursive: true, force: true });
      await rename(staging, folder);
      onDisk = true;
    },
  };
}

// `events` maps more of the socket's events to a handler, called with the socket and the event.
function connect(baileys, auth, handlers, events = {}) {
  const makeSocket = baileys.makeWASocket ?? baileys.default;
  const socket = makeSocket({
    auth: auth.state,
    browser: BROWSER,
    markOnlineOnConnect: false,
    syncFullHistory: false,
    logger: baileysLogger(),
  });
  socket.ev.on("creds.update", async (update) => {
    Object.assign(auth.state.creds, update);
    await auth.saveCreds();
  });
  socket.ev.on("connection.update", (update) => handlers(socket, update).catch(stop));
  for (const [name, handler] of Object.entries(events)) {
    socket.ev.on(name, (value) => handler(socket, value).catch(stop));
  }
  return socket;
}

async function listGroups(socket) {
  const groups = Object.values(await socket.groupFetchAllParticipating())
    .map(({ id, subject }) => ({ id, subject }))
    .sort((a, b) => String(a.subject).localeCompare(String(b.subject)));
  log("info", `listed ${groups.length} groups`);
  say({ groups });
}

const now = () => new Date().toISOString();

// A message's time in seconds: Baileys gives a number or a protobuf Long.
function epochSeconds(time) {
  if (time === null || time === undefined) return null;
  if (typeof time !== "object") return Number(time);
  return time.toNumber?.() ?? time.high * 2 ** 32 + (time.low >>> 0);
}

// The store's record of a message in a picked group, without its subject, or null for one with
// nothing to keep: a reaction, an edit, a deletion, a key exchange.
function storeRecord(baileys, creds, message) {
  const content = baileys.normalizeMessageContent(message.message);
  const type = baileys.getContentType(content);
  const body = type && content[type];
  let text;
  if (type === "conversation") text = body;
  else if (type === "extendedTextMessage") text = body?.text;
  else if (type in PLACEHOLDERS) {
    text = body?.caption?.trim() || (body?.gifPlayback ? "[GIF]" : PLACEHOLDERS[type]);
  }
  text = typeof text === "string" ? text.trim() : "";
  if (!text) return null;
  const fromMe = Boolean(message.key.fromMe);
  return {
    id: message.key.id,
    group_id: message.key.remoteJid,
    group_subject: null,
    sender: (fromMe ? creds.me?.name : message.pushName) ?? null,
    timestamp: epochSeconds(message.messageTimestamp),
    text,
    from_me: fromMe,
  };
}

async function follow(baileys, auth, { picked, store, status }) {
  const { DisconnectReason } = baileys;
  for (const file of [store, status]) await makePrivateFolder(dirname(file));

  // Until WhatsApp lets it connect, a link that ended stays ended: the OS starts this again at
  // sign-in on the same auth state.
  let link = { paired: true, connected_since: null, last_connected: null, link_ended_at: null };
  try {
    const last = JSON.parse(await readFile(status, "utf8"));
    for (const field of ["paired", "last_connected", "link_ended_at"]) {
      if (field in last) link[field] = last[field];
    }
  } catch {
    // No status yet, or one cut short: it is written whole below.
  }
  const setStatus = async (change) => {
    link = { ...link, ...change };
    await writePrivate(status, JSON.stringify(link) + "\n");
  };
  await setStatus({});

  // The picked groups, read again whenever the file is another, as Python's replace makes it.
  let pickedIds = new Set();
  let pickedFile;
  const readPicked = async () => {
    let seen = "none";
    try {
      const { ino, size, mtimeMs } = await stat(picked);
      seen = `${ino}:${size}:${mtimeMs}`;
    } catch (e) {
      if (e.code !== "ENOENT") throw e;
    }
    if (seen === pickedFile) return;
    pickedFile = seen;
    if (seen === "none") {
      pickedIds = new Set();
      log("warn", "no picked-groups file: no group is kept");
      return;
    }
    try {
      const { groups } = JSON.parse(await readFile(picked, "utf8"));
      if (!Array.isArray(groups)) throw new Error("it has no list of groups");
      pickedIds = new Set(groups.map(String));
      log("info", `${pickedIds.size} picked groups`);
    } catch (e) {
      log("warn", `the picked-groups file can't be read, so the last pick holds: ${e.message}`);
    }
  };

  const subjects = new Map(); // group id -> subject
  const subjectOf = async (socket, id) => {
    if (!subjects.has(id)) {
      try {
        subjects.set(id, (await socket.groupMetadata(id))?.subject ?? null);
      } catch (e) {
        log("warn", `a picked group's subject couldn't be fetched: ${e?.message ?? e}`);
        return null;
      }
    }
    return subjects.get(id);
  };

  // A message whose chat isn't picked is dropped here, before anything is written.
  const keep = async (socket, messages) => {
    await readPicked();
    const lines = [];
    for (const message of messages ?? []) {
      if (!message?.key || !pickedIds.has(message.key.remoteJid)) continue;
      const record = storeRecord(baileys, auth.state.creds, message);
      if (!record) continue;
      record.group_subject = await subjectOf(socket, record.group_id);
      lines.push(JSON.stringify(record) + "\n");
    }
    if (lines.length === 0) return;
    appendFileSync(store, lines.join(""), { mode: 0o600 });
    log("info", `stored ${lines.length} message${lines.length === 1 ? "" : "s"}`);
  };
  // In arrival order, one batch at a time.
  let kept = Promise.resolve();
  const keepInTurn = (socket, messages) => (kept = kept.then(() => keep(socket, messages)));

  const disconnected = async () => {
    if (link.connected_since) await setStatus({ connected_since: null, last_connected: now() });
  };
  const ended = async () => {
    await kept.catch(() => {});
    const at = now();
    await setStatus({
      paired: false,
      connected_since: null,
      last_connected: link.connected_since ? at : link.last_connected,
      link_ended_at: at,
    });
    finish(EXIT.linkEnded);
  };

  await readPicked();
  // Node would end once a socket has closed and the next isn't open yet.
  setInterval(() => {}, 60_000);
  let current;
  let drops = 0;
  const handle = async (socket, { connection, lastDisconnect, qr }) => {
    if (socket !== current) return;
    if (qr) {
      log("warn", "asked for a QR code: the link ended");
      return ended();
    }
    if (connection === "open") {
      drops = 0;
      const at = now();
      log("info", "connected");
      await setStatus({
        paired: true, connected_since: at, last_connected: at, link_ended_at: null,
      });
    }
    if (connection === "close") {
      const code = lastDisconnect?.error?.output?.statusCode;
      log("info", `connection closed (${code ?? "no status"})`);
      if (code === DisconnectReason.loggedOut) {
        log("warn", "logged out: the link ended");
        return ended();
      }
      await disconnected();
      const pause = code === DisconnectReason.restartRequired
        ? 0 : Math.min(RECONNECT_MS * 2 ** drops++, MOST_RECONNECT_MS);
      log("info", `connecting again in ${pause / 1000} seconds`);
      setTimeout(() => (current = connect(baileys, auth, handle, events)), pause);
    }
  };
  // New messages and the history WhatsApp hands over at connect are kept the same way.
  const arrived = async (socket, { messages }) => keepInTurn(socket, messages);
  const events = {
    "messages.upsert": arrived,
    "messaging-history.set": arrived,
    "groups.update": async (_socket, updates) => {
      for (const { id, subject } of updates ?? []) {
        if (id && subject !== undefined) subjects.set(id, subject);
      }
    },
  };
  for (const signal of ["SIGTERM", "SIGINT"]) {
    process.on(signal, () => {
      log("info", `stopped by ${signal}`);
      disconnected().finally(() => finish(EXIT.stopped));
    });
  }
  current = connect(baileys, auth, handle, events);
}

async function main() {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      modules: { type: "string" },
      auth: { type: "string" },
      log: { type: "string" },
      timeout: { type: "string" },
      picked: { type: "string" },
      store: { type: "string" },
      status: { type: "string" },
    },
  });
  const [mode] = positionals;
  const following = mode === "follow";
  if (positionals.length !== 1 || !(mode in SECONDS) || !values.modules || !values.auth ||
      !values.log ||
      (following && (!values.picked || !values.store || !values.status || values.timeout))) {
    process.stderr.write(
      "Usage: linked_device.mjs pair|groups --modules TREE --auth FOLDER --log FILE " +
      "[--timeout SECONDS]\n" +
      "       linked_device.mjs follow --modules TREE --auth FOLDER --log FILE " +
      "--picked FILE --store FILE --status FILE\n");
    return finish(EXIT.usage);
  }
  process.umask(0o077);
  logFile = values.log;
  const seconds = Number(values.timeout ?? SECONDS[mode]);
  log("info", `${mode} started`);

  const baileys = await loadBaileys(values.modules);
  const pairing = mode === "pair";
  const auth = authState(baileys, values.auth, !pairing);
  if (!(await auth.load())) {
    log("warn", "no auth state: needs a new pairing");
    return finish(following ? EXIT.linkEnded : EXIT.needsPairing);
  }
  if (following) return follow(baileys, auth, values);

  setTimeout(() => {
    log("warn", `${pairing ? "not paired" : "not connected"} after ${seconds} seconds`);
    finish(EXIT.timedOut);
  }, seconds * 1000).unref();
  // The timer alone doesn't keep Node running; the socket does, and this does until it exits.
  const alive = setInterval(() => {}, 60_000);

  const { DisconnectReason } = baileys;
  let current;
  let kept = !pairing;
  const keep = async () => {
    if (kept) return;
    kept = true;
    await auth.keep();
    log("info", "paired, auth state saved");
    say({ paired: true });
  };
  const handle = async (socket, { connection, lastDisconnect, qr }) => {
    if (socket !== current) return; // a socket that has closed
    if (qr && !pairing) {
      log("warn", "asked for a QR code: needs a new pairing");
      return finish(EXIT.needsPairing);
    }
    if (qr) {
      log("info", "QR code shown");
      say({ qr });
    }
    if (connection === "open") {
      log("info", "connected");
      try {
        await keep();
        await listGroups(socket);
      } catch (e) {
        log("error", `listing the groups failed: ${e?.message ?? e}`);
        return finish(EXIT.failed);
      }
      clearInterval(alive);
      socket.end(undefined);
      return finish(0);
    }
    if (connection === "close") {
      const status = lastDisconnect?.error?.output?.statusCode;
      log("info", `connection closed (${status ?? "no status"})`);
      if (status === DisconnectReason.loggedOut) {
        log("warn", "logged out: needs a new pairing");
        return finish(EXIT.needsPairing);
      }
      // A scan ends with WhatsApp asking for a restart, which needs a new socket at once. The
      // phone lists the device from then on, so the pairing is kept even if that socket never
      // opens.
      const restart = status === DisconnectReason.restartRequired;
      if (restart && auth.state.creds.me) await keep();
      setTimeout(() => (current = connect(baileys, auth, handle)), restart ? 0 : RECONNECT_MS);
    }
  };
  current = connect(baileys, auth, handle);
}

function stop(e) {
  log("error", `stopped: ${e?.message ?? e}`);
  finish(EXIT.failed);
}

main().catch(stop);
