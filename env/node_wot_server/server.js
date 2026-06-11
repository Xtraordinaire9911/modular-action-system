/*
 * Smart-room WoT backend — Eclipse Thingweb node-wot servient.
 *
 * Replaces the earlier custom Flask mock (advisor §9.1): the agent now parses
 * *real* W3C Thing Descriptions emitted by node-wot, so the Affordance Parser
 * is exercised against valid JSON-LD with forms/href/securityDefinitions rather
 * than simplified responses.
 *
 *   TD:        http://localhost:8080/<thing>                 (e.g. /thermostat)
 *   property:  http://localhost:8080/<thing>/properties/<name>
 *   action:    http://localhost:8080/<thing>/actions/<name>
 *
 * A separate control plane on :8081 drives WoT-side failure injection used by
 * the Chaos-Monkey evaluation (advisor §11.1):
 *   POST /failure  {"type":"timeout|offline|postcondition_mismatch|malformed",
 *                   "thing":"thermostat", "delay_ms":1000}
 *   POST /reset
 */
"use strict";

const http = require("http");
const { Servient } = require("@node-wot/core");
const { HttpServer } = require("@node-wot/binding-http");

// ── mutable device state ────────────────────────────────────────────────────
const INITIAL = {
  thermostat: { targetTemperature: 20, currentTemperature: 19 },
  lights: { brightness: 100 },
  projector: { power: "off" },
  blinds: { position: 100 },
  occupancy: { occupied: false, peopleCount: 0 },
};
let state = structuredClone(INITIAL);

// ── fault injection registry ─────────────────────────────────────────────────
// faults[thing] = { type, delay_ms }
const faults = {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function guard(thing) {
  const f = faults[thing];
  if (!f) return;
  if (f.type === "timeout") await sleep(f.delay_ms || 1500);
  if (f.type === "offline") throw new Error("backend offline (injected)");
}

function applyWrite(thing, key, value) {
  // postcondition_mismatch: accept the call (HTTP 200) but do NOT change state.
  if (faults[thing] && faults[thing].type === "postcondition_mismatch") return;
  state[thing][key] = value;
}

// ── thing factory ─────────────────────────────────────────────────────────────
async function exposeThing(servient, def) {
  const wot = await servient.start();
  const thing = await wot.produce(def.td);
  for (const [name, key] of Object.entries(def.readables)) {
    thing.setPropertyReadHandler(name, async () => {
      await guard(def.thing);
      if (faults[def.thing] && faults[def.thing].type === "malformed") return "NOT_A_NUMBER";
      return state[def.thing][key];
    });
  }
  for (const [name, key] of Object.entries(def.writables || {})) {
    thing.setPropertyWriteHandler(name, async (v) => {
      await guard(def.thing);
      applyWrite(def.thing, key, await v.value());
    });
  }
  for (const [action, handler] of Object.entries(def.actions)) {
    thing.setActionHandler(action, async (params) => {
      await guard(def.thing);
      const value = params ? await params.value() : undefined;
      return handler(value);
    });
  }
  await thing.expose();
  console.log(`exposed Thing: ${def.thing}`);
}

function buildDefs() {
  return [
    {
      thing: "thermostat",
      readables: { targetTemperature: "targetTemperature", currentTemperature: "currentTemperature" },
      writables: { targetTemperature: "targetTemperature" },
      actions: {
        setTargetTemperature: (v) => {
          applyWrite("thermostat", "targetTemperature", v);
          state.thermostat.currentTemperature = v; // physical convergence
          return undefined;
        },
      },
      td: {
        "@context": "https://www.w3.org/2022/wot/td/v1.1",
        title: "thermostat",
        securityDefinitions: { apikey_sc: { scheme: "apikey", in: "header", name: "X-API-Key" } },
        security: "apikey_sc",
        properties: {
          targetTemperature: { type: "number", minimum: 16, maximum: 30 },
          currentTemperature: { type: "number", readOnly: true },
        },
        actions: { setTargetTemperature: { input: { type: "number", minimum: 16, maximum: 30 } } },
      },
    },
    {
      thing: "lights",
      readables: { brightness: "brightness" },
      writables: { brightness: "brightness" },
      actions: { setBrightness: (v) => { applyWrite("lights", "brightness", v); } },
      td: {
        "@context": "https://www.w3.org/2022/wot/td/v1.1",
        title: "lights",
        properties: { brightness: { type: "integer", minimum: 0, maximum: 100 } },
        actions: { setBrightness: { input: { type: "integer", minimum: 0, maximum: 100 } } },
      },
    },
    {
      thing: "projector",
      readables: { power: "power" },
      writables: { power: "power" },
      actions: { setPower: (v) => { applyWrite("projector", "power", v); } },
      td: {
        "@context": "https://www.w3.org/2022/wot/td/v1.1",
        title: "projector",
        properties: { power: { type: "string", enum: ["on", "off"] } },
        actions: { setPower: { input: { type: "string", enum: ["on", "off"] } } },
      },
    },
    {
      thing: "blinds",
      readables: { position: "position" },
      writables: { position: "position" },
      actions: { setPosition: (v) => { applyWrite("blinds", "position", v); } },
      td: {
        "@context": "https://www.w3.org/2022/wot/td/v1.1",
        title: "blinds",
        securityDefinitions: { basic_sc: { scheme: "basic", in: "header" } },
        security: "basic_sc",
        properties: { position: { type: "integer", minimum: 0, maximum: 100 } },
        actions: { setPosition: { input: { type: "integer", minimum: 0, maximum: 100 } } },
      },
    },
    {
      thing: "occupancy",
      readables: { occupied: "occupied", peopleCount: "peopleCount" },
      actions: {},
      td: {
        "@context": "https://www.w3.org/2022/wot/td/v1.1",
        title: "occupancy",
        properties: {
          occupied: { type: "boolean", readOnly: true, observable: true },
          peopleCount: { type: "integer", readOnly: true },
        },
      },
    },
  ];
}

// ── control plane (failure injection) ────────────────────────────────────────
function startControlPlane(port = 8081) {
  const srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      res.setHeader("Content-Type", "application/json");
      if (req.method === "POST" && req.url === "/reset") {
        state = structuredClone(INITIAL);
        for (const k of Object.keys(faults)) delete faults[k];
        return res.end(JSON.stringify({ status: "reset", state }));
      }
      if (req.method === "POST" && req.url === "/failure") {
        const f = JSON.parse(body || "{}");
        if (f.clear) delete faults[f.thing];
        else faults[f.thing] = { type: f.type, delay_ms: f.delay_ms };
        return res.end(JSON.stringify({ status: "ok", faults }));
      }
      if (req.method === "GET" && req.url === "/state") {
        return res.end(JSON.stringify({ state, faults }));
      }
      res.statusCode = 404;
      res.end(JSON.stringify({ error: "not found" }));
    });
  });
  srv.listen(port, () => console.log(`control plane on :${port}`));
}

async function main() {
  const servient = new Servient();
  servient.addServer(new HttpServer({ port: 8080 }));
  for (const def of buildDefs()) {
    await exposeThing(servient, def);
  }
  startControlPlane(8081);
  console.log("smart-room WoT servient ready on :8080 (TDs at /<thing>)");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
