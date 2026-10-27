// A stand-in for @whiskeysockets/baileys, which the Linked Device's program imports from the
// test's module folder (fake_node.install_fake_baileys). It plays the steps the test wrote to
// $FAKE_BAILEYS_SCENARIO, one list per socket the program opens, and appends each call the
// program made on a socket, and the options it opened it with, to $FAKE_BAILEYS_CALLS.
//
// Steps: {"qr": "..."}, {"open": true}, {"close": STATUS}, {"creds": {...}} (a creds.update),
// {"keys": {...}} (the program's keys.set), {"get": [TYPE, [IDS]]} (its keys.get, recorded),
// {"message": MESSAGE} (a messages.upsert), {"history": [MESSAGE, ...]} (a
// messaging-history.set, as WhatsApp hands over at connect), {"rename": {"id": "...", "subject":
// "..."}} (a groups.update), {"wait": NAME} (waits until the test makes NAME beside the
// scenario) and {"log": "..."} (a warning in Baileys' logger, with the text in its fields).
//
// A MESSAGE is {"chat": "...", "text": "..."}, with "media" (a content type such as
// "imageMessage") instead of "text", or "content" for the whole message content, and optional
// "id", "sender", "fromMe" and "time" (seconds).

import { appendFileSync, existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

const scenario = JSON.parse(readFileSync(process.env.FAKE_BAILEYS_SCENARIO, "utf8"));
const record = (line) => appendFileSync(process.env.FAKE_BAILEYS_CALLS, JSON.stringify(line) + "\n");
const pause = () => new Promise((resolve) => setTimeout(resolve, 20));
let opened = 0;

async function waitFor(name) {
  const file = join(dirname(process.env.FAKE_BAILEYS_SCENARIO), name);
  while (!existsSync(file)) await pause();
}

function waMessage({ chat, id = "M1", text, media, content, sender = "Liisa", fromMe = false,
                     time = 1761555600 }) {
  return {
    key: { remoteJid: chat, id, fromMe, participant: fromMe ? undefined : "358409999999@lid" },
    message: content ?? (media ? { [media]: {} } : { conversation: text }),
    messageTimestamp: time,
    pushName: fromMe ? undefined : sender,
  };
}

// As Baileys' own, for the wrappers the program meets.
export function normalizeMessageContent(content) {
  for (let i = 0; i < 5 && content; i++) {
    const inner = content.ephemeralMessage ?? content.viewOnceMessage ??
      content.viewOnceMessageV2 ?? content.documentWithCaptionMessage ?? content.editedMessage;
    if (!inner) break;
    content = inner.message;
  }
  return content;
}

export function getContentType(content) {
  return content ? Object.keys(content).find((key) =>
    key !== "senderKeyDistributionMessage" && key !== "messageContextInfo") : undefined;
}

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
    at: Date.now(),
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
    groupMetadata: async (jid) => {
      if (!scenario.groups[jid]) throw new Error("item-not-found");
      return scenario.groups[jid];
    },
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
        await emit("messages.upsert", { type: "notify", messages: [waMessage(step.message)] });
      }
      if ("history" in step) {
        await emit("messaging-history.set", {
          chats: [], contacts: [], messages: step.history.map(waMessage), isLatest: true,
        });
      }
      if ("rename" in step) await emit("groups.update", [step.rename]);
      if ("wait" in step) await waitFor(step.wait);
      if ("log" in step) {
        logger.warn({ message: { conversation: step.log } }, "failed to decrypt message");
        logger.info({ text: step.log }, `received ${step.log}`);
      }
    }
  })();
  return recorded(socket, "");
}

export default makeWASocket;
