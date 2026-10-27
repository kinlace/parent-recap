// A stand-in for @whiskeysockets/baileys, which the Linked Device's program imports from the
// test's module folder (fake_node.install_fake_baileys). It plays the steps the test wrote to
// $FAKE_BAILEYS_SCENARIO, one list per socket the program opens, and appends each call the
// program made on a socket, and the options it opened it with, to $FAKE_BAILEYS_CALLS.
//
// Steps: {"qr": "..."}, {"open": true}, {"close": STATUS}, {"creds": {...}} (a creds.update),
// {"keys": {...}} (the program's keys.set), {"get": [TYPE, [IDS]]} (its keys.get, recorded),
// {"message": {"chat": "...", "text": "..."}} (a messages.upsert) and {"log": "..."} (a warning
// in Baileys' logger, with the text in its fields).

import { appendFileSync, readFileSync } from "node:fs";

const scenario = JSON.parse(readFileSync(process.env.FAKE_BAILEYS_SCENARIO, "utf8"));
const record = (line) => appendFileSync(process.env.FAKE_BAILEYS_CALLS, JSON.stringify(line) + "\n");
const pause = () => new Promise((resolve) => setTimeout(resolve, 20));
let opened = 0;

export const DisconnectReason = {
  connectionClosed: 428, connectionLost: 408, loggedOut: 401, restartRequired: 515, timedOut: 408,
};

// As Baileys' own: a Buffer as base64.
export const BufferJSON = {
  replacer: (_key, value) =>
    Buffer.isBuffer(value) || value instanceof Uint8Array || value?.type === "Buffer"
      ? { type: "Buffer", data: Buffer.from(value?.data || value).toString("base64") }
      : value,
  reviver: (_key, value) =>
    value?.type === "Buffer" && typeof value.data === "string"
      ? Buffer.from(value.data, "base64")
      : value,
};

export const proto = { Message: { AppStateSyncKeyData: { fromObject: (value) => value } } };

export function initAuthCreds() {
  return { registrationId: 7, noiseKey: { private: Buffer.from("noise"), public: Buffer.from("n") } };
}

// Records each call on `target`, and answers one it doesn't have, such as sendMessage, so the
// test sees it rather than a crash.
function recorded(target, prefix) {
  return new Proxy(target, {
    get(object, name) {
      if (typeof name === "symbol" || name === "then") return object[name];
      if (name === "ev") return object.ev;
      const value = object[name];
      if (value !== undefined && typeof value !== "function") return value;
      return (...args) => {
        record({ call: prefix + name, args: name === "on" ? [args[0]] : [] });
        return value ? value.apply(object, args) : Promise.resolve(undefined);
      };
    },
  });
}

export function makeWASocket(config) {
  const steps = scenario.sockets[opened++] ?? [];
  const { auth, logger } = config;
  record({
    call: "makeWASocket",
    options: {
      browser: config.browser,
      markOnlineOnConnect: config.markOnlineOnConnect,
      syncFullHistory: config.syncFullHistory,
      me: auth.creds.me ?? null,
      noiseKeyIsBuffer: Buffer.isBuffer(auth.creds.noiseKey?.private),
    },
  });
  const listeners = {};
  const emit = async (name, value) => {
    for (const listener of listeners[name] ?? []) await listener(value);
  };
  const socket = {
    ev: recorded({ on: (name, listener) => (listeners[name] ??= []).push(listener) }, "ev."),
    groupFetchAllParticipating: async () => scenario.groups,
    end: () => {},
  };

  (async () => {
    for (const step of steps) {
      await pause();
      if ("qr" in step) await emit("connection.update", { qr: step.qr });
      if ("open" in step) await emit("connection.update", { connection: "open" });
      if ("close" in step) {
        await emit("connection.update", {
          connection: "close", lastDisconnect: { error: { output: { statusCode: step.close } } },
        });
      }
      if ("creds" in step) {
        Object.assign(auth.creds, step.creds);
        await emit("creds.update", step.creds);
      }
      if ("keys" in step) await auth.keys.set(step.keys);
      if ("get" in step) record({ call: "keys.get", result: await auth.keys.get(...step.get) });
      if ("message" in step) {
        await emit("messages.upsert", { type: "notify", messages: [{
          key: { remoteJid: step.message.chat, id: "M1", fromMe: false },
          message: { conversation: step.message.text },
        }] });
      }
      if ("log" in step) {
        logger.warn({ message: { conversation: step.log } }, "failed to decrypt message");
        logger.info({ text: step.log }, `received ${step.log}`);
      }
    }
  })();
  return recorded(socket, "");
}

export default makeWASocket;
