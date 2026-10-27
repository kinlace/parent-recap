// The Linked Device (ADR 0025): Parent Recap's own Node program, run by the pinned Node on the
// Baileys tree the install puts in Parent Recap's folder (baileys_install.py).
//
//   node linked_device.mjs pair   --modules TREE --auth FOLDER --log FILE [--timeout SECONDS]
//   node linked_device.mjs groups --modules TREE --auth FOLDER --log FILE [--timeout SECONDS]
//
// `pair` connects with no auth state and prints each QR string Baileys emits as a line of JSON,
// {"qr": "..."}, until the phone pairs. Only then is the auth state written to FOLDER, and
// {"paired": true} printed, so a pairing that never happens leaves none. `groups` connects with
// the auth state in FOLDER. Both then print {"groups": [{"id": "...", "subject": "..."}]} and
// exit; no message is kept.
//
// It only reads: no send, read-receipt or presence call is ever made. Delivery receipts and
// presence nodes Baileys sends on its own are accepted (#181). Log lines go to FILE, never to
// stdout, and never hold a message's text: Baileys' own lines are kept as their fixed wording,
// without the objects it logs with them.
//
// linked_device.py names its exit codes, EXIT below, for Python.

import { appendFileSync } from "node:fs";
import { chmod, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

const EXIT = { failed: 1, usage: 2, timedOut: 3, needsPairing: 4 };
// The phone lists a linked device by the first of these.
const BROWSER = ["Parent Recap", "Chrome", "1.0"];
const SECONDS = { pair: 300, groups: 60 };
const RECONNECT_MS = 2000;

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

function connect(baileys, auth, handlers) {
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
  return socket;
}

async function listGroups(socket) {
  const groups = Object.values(await socket.groupFetchAllParticipating())
    .map(({ id, subject }) => ({ id, subject }))
    .sort((a, b) => String(a.subject).localeCompare(String(b.subject)));
  log("info", `listed ${groups.length} groups`);
  say({ groups });
}

async function main() {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      modules: { type: "string" },
      auth: { type: "string" },
      log: { type: "string" },
      timeout: { type: "string" },
    },
  });
  const [mode] = positionals;
  if (positionals.length !== 1 || !(mode in SECONDS) || !values.modules || !values.auth ||
      !values.log) {
    process.stderr.write(
      "Usage: linked_device.mjs pair|groups --modules TREE --auth FOLDER --log FILE " +
      "[--timeout SECONDS]\n");
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
    return finish(EXIT.needsPairing);
  }

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
