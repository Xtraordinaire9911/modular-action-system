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
 * A runtime Thing Directory on :8082 (W3C WoT Discovery style) lets any agent
 * discover the available TDs without hard-coding device names — this is what
 * makes dynamic Thing Description passing between agents possible:
 *   GET /things        -> array of every live Thing Description
 *   GET /things/links  -> lightweight registration entries {id, td}
 *
 * A separate control plane on :8081 drives WoT-side failure injection used by
 * the Chaos-Monkey evaluation (advisor §11.1):
 *   POST /failure  {"type":"timeout|offline|postcondition_mismatch|malformed",
 *                   "thing":"thermostat", "delay_ms":1000,
 *                   "read_delay_ms":450, "drop_probability":0.7,
 *                   "source_reliability":{"wot":0.35}}
 *   POST /reset
 *   POST /restore  {"state":{...}, "faults":{...}}
 *   POST /lease/acquire  {"episode_id":"..."}
 *   POST /lease/restore  (X-Episode-Lease: <lease_id>)
 *   POST /lease/release  (X-Episode-Lease: <lease_id>)
 */
"use strict";

const http = require("http");
const { randomUUID } = require("node:crypto");

// ── mutable device state ────────────────────────────────────────────────────
// Every Thing here separates what it was *told* from what it has *reached*.
// That distinction is the whole difference between a device and a variable: a
// setpoint changes the instant it is written, and the room does not. An agent
// that reads back the setpoint has confirmed that it was heard, which is not the
// same claim as the room being at that temperature - and only one of those two
// claims is the goal.
//
// Where a real device has no meaningful lag it is not given a fake one. A dimmer
// reaches its level in milliseconds, so `lights` has a single property and this
// file says so rather than inventing an interesting delay for it.
const INITIAL = {
  thermostat: { targetTemperature: 20, currentTemperature: 19 },
  lights: { brightness: 100 },
  projector: { power: "off", lamp: "off" }, // lamp: off | warming | on
  blinds: { position: 100, measuredPosition: 100 },
  occupancy: { occupied: false, peopleCount: 0 },
};
let state = structuredClone(INITIAL);
let stateGeneration = 0;
let activeLease = null;

// ── physics ──────────────────────────────────────────────────────────────────
// The room runs at TIME_SCALE times real speed so a demo fits inside a meeting.
// The rates below are the real ones; the scaling is applied here, once, and is
// reported by /state so nobody has to read this file to know that a room which
// reaches temperature in two seconds is a room running thirty times too fast.
//
// Only devices with a real lag are modelled. Adding a delay to something that is
// physically instant would be the same kind of invention as pretending something
// slow is immediate.
const TIME_SCALE = 30;
const RAMPS = {
  // A room changes temperature at roughly 3 C per minute with the HVAC driving.
  thermostat: { commanded: "targetTemperature", measured: "currentTemperature", realRatePerSec: 0.05 },
  // A blind motor takes about twenty seconds for full travel.
  blinds: { commanded: "position", measured: "measuredPosition", realRatePerSec: 5 },
};
// A projector lamp needs to strike and warm before it puts out an image. Power is
// the command; lamp is what the room can actually see.
const LAMP_WARMUP_REAL_SEC = 30;
const PHYSICS_TICK_MS = 200;

let physicsEnabled = true;
let lampWarmingSince = null;

function settleRamp(thing, ramp) {
  const target = state[thing]?.[ramp.commanded];
  if (target !== undefined) state[thing][ramp.measured] = target;
}

function stepPhysics(elapsedSec) {
  if (!physicsEnabled) return;

  for (const [thing, ramp] of Object.entries(RAMPS)) {
    // A jammed motor still accepts the command and reports the setpoint; what it
    // stops doing is arriving. This is the failure a digital write cannot have.
    if (faults[thing] && faults[thing].type === "motor_jam") continue;
    const target = state[thing][ramp.commanded];
    const measured = state[thing][ramp.measured];
    if (typeof target !== "number" || typeof measured !== "number") continue;
    const step = ramp.realRatePerSec * TIME_SCALE * elapsedSec;
    const gap = target - measured;
    if (Math.abs(gap) <= step) {
      state[thing][ramp.measured] = target;
    } else {
      state[thing][ramp.measured] = measured + Math.sign(gap) * step;
    }
  }

  // The lamp: off -> warming -> on, and a failed lamp never leaves "off" however
  // many times it is switched on.
  const lampFailed = faults.projector && faults.projector.type === "lamp_failure";
  if (state.projector.power === "off") {
    state.projector.lamp = "off";
    lampWarmingSince = null;
  } else if (lampFailed) {
    state.projector.lamp = "off";
    lampWarmingSince = null;
  } else if (state.projector.lamp === "off") {
    state.projector.lamp = "warming";
    lampWarmingSince = Date.now();
  } else if (state.projector.lamp === "warming") {
    const warmedFor = (Date.now() - (lampWarmingSince ?? Date.now())) / 1000;
    if (warmedFor * TIME_SCALE >= LAMP_WARMUP_REAL_SEC) state.projector.lamp = "on";
  }
}

setInterval(() => stepPhysics(PHYSICS_TICK_MS / 1000), PHYSICS_TICK_MS).unref?.();

// ── fault injection registry ─────────────────────────────────────────────────
// faults[thing] = { type, delay_ms, read_delay_ms, drop_probability, source_reliability }
const faults = {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// The first four are transport and protocol failures: the call does not arrive,
// does not return, or returns nonsense. `delayed_rollback` is an optimistic
// transition: the command is briefly observable and the device then returns to
// its previous state. The last two are physical, and a purely digital
// environment cannot produce them - the call succeeds, the setpoint updates,
// every status code is 2xx, and the room still does not do it.
const FAILURE_TYPES = new Set([
  "timeout",
  "offline",
  "postcondition_mismatch",
  "malformed",
  "delayed_rollback",
  "lamp_failure",
  "motor_jam",
]);

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value, expected) {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index]);
}

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function isValidState(candidate) {
  if (!isPlainObject(candidate) || !hasExactKeys(candidate, Object.keys(INITIAL))) return false;
  const { thermostat, lights, projector, blinds, occupancy } = candidate;
  return (
    isPlainObject(thermostat) &&
    hasExactKeys(thermostat, ["targetTemperature", "currentTemperature"]) &&
    isFiniteNumber(thermostat.targetTemperature) &&
    thermostat.targetTemperature >= 16 &&
    thermostat.targetTemperature <= 30 &&
    isFiniteNumber(thermostat.currentTemperature) &&
    thermostat.currentTemperature >= 16 &&
    thermostat.currentTemperature <= 30 &&
    isPlainObject(lights) &&
    hasExactKeys(lights, ["brightness"]) &&
    Number.isInteger(lights.brightness) &&
    lights.brightness >= 0 &&
    lights.brightness <= 100 &&
    isPlainObject(projector) &&
    hasExactKeys(projector, ["power", "lamp"]) &&
    ["on", "off"].includes(projector.power) &&
    ["on", "off", "warming"].includes(projector.lamp) &&
    isPlainObject(blinds) &&
    hasExactKeys(blinds, ["position", "measuredPosition"]) &&
    Number.isInteger(blinds.position) &&
    blinds.position >= 0 &&
    blinds.position <= 100 &&
    // The measured position is mid-travel between ticks, so it is not an integer.
    isFiniteNumber(blinds.measuredPosition) &&
    blinds.measuredPosition >= 0 &&
    blinds.measuredPosition <= 100 &&
    isPlainObject(occupancy) &&
    hasExactKeys(occupancy, ["occupied", "peopleCount"]) &&
    typeof occupancy.occupied === "boolean" &&
    Number.isInteger(occupancy.peopleCount) &&
    occupancy.peopleCount >= 0
  );
}

function isValidFaults(candidate) {
  if (!isPlainObject(candidate)) return false;
  return Object.entries(candidate).every(([thing, fault]) => {
    if (!Object.hasOwn(INITIAL, thing) || !isPlainObject(fault)) return false;
    const keys = Object.keys(fault);
    if (!keys.includes("type") || keys.some((key) => !["type", "delay_ms"].includes(key))) return false;
    if (!FAILURE_TYPES.has(fault.type)) return false;
    return fault.delay_ms === undefined || (isFiniteNumber(fault.delay_ms) && fault.delay_ms >= 0);
  });
}

function isValidFailureRequest(candidate) {
  if (!isPlainObject(candidate) || !Object.hasOwn(INITIAL, candidate.thing)) return false;
  if (Object.hasOwn(candidate, "clear")) {
    return candidate.clear === true && hasExactKeys(candidate, ["thing", "clear"]);
  }
  const allowed = ["thing", "type", "delay_ms", "read_delay_ms", "drop_probability", "source_reliability"];
  if (Object.keys(candidate).some((key) => !allowed.includes(key))) return false;
  if (!FAILURE_TYPES.has(candidate.type)) return false;
  for (const key of ["delay_ms", "read_delay_ms"]) {
    if (candidate[key] !== undefined && !(isFiniteNumber(candidate[key]) && candidate[key] >= 0)) return false;
  }
  if (candidate.drop_probability !== undefined) {
    if (!isFiniteNumber(candidate.drop_probability)) return false;
    if (candidate.drop_probability < 0 || candidate.drop_probability > 1) return false;
  }
  if (candidate.source_reliability !== undefined && !isPlainObject(candidate.source_reliability)) return false;
  return true;
}

function isValidCheckpoint(candidate) {
  return (
    isPlainObject(candidate) &&
    hasExactKeys(candidate, ["state", "faults"]) &&
    isValidState(candidate.state) &&
    isValidFaults(candidate.faults)
  );
}

function restoreCheckpoint(checkpoint) {
  stateGeneration += 1;
  state = structuredClone(checkpoint.state);
  for (const key of Object.keys(faults)) delete faults[key];
  Object.assign(faults, structuredClone(checkpoint.faults));
}

function currentCheckpoint() {
  return { state: structuredClone(state), faults: structuredClone(faults) };
}

function resetToInitial() {
  stateGeneration += 1;
  state = structuredClone(INITIAL);
  for (const key of Object.keys(faults)) delete faults[key];
}

function leaseConflict(leaseId) {
  if (activeLease === null) {
    return leaseId
      ? { statusCode: 409, payload: { error: "stale episode lease" } }
      : null;
  }
  if (leaseId === activeLease.leaseId) return null;
  return {
    statusCode: 423,
    payload: { error: "control plane is leased", episode_id: activeLease.episodeId },
  };
}

function shouldDropRead(f) {
  if (!f || !f.drop_probability) return false;
  const probability = Math.max(0, Math.min(1, Number(f.drop_probability) || 0));
  if (probability <= 0) return false;
  return Math.random() < probability;
}

async function guard(thing, { read = false } = {}) {
  const generationAtStart = stateGeneration;
  const f = faults[thing];
  if (!f) return generationAtStart;
  if (read && f.read_delay_ms) await sleep(f.read_delay_ms);
  if (read && shouldDropRead(f)) throw new Error("backend dropped read (injected)");
  if (f.type === "timeout") await sleep(f.delay_ms || 1500);
  assertCurrentGeneration(generationAtStart);
  if (f.type === "offline") throw new Error("backend offline (injected)");
  return generationAtStart;
}

function assertCurrentGeneration(generation) {
  if (generation !== stateGeneration) {
    throw new Error("interaction invalidated by reset or restore");
  }
}

function applyWrite(thing, key, value) {
  // postcondition_mismatch: accept the call (HTTP 200) but do NOT change state.
  if (faults[thing] && faults[thing].type === "postcondition_mismatch") return;
  const previous = state[thing][key];
  const rollback = faults[thing] && faults[thing].type === "delayed_rollback" ? faults[thing] : null;
  state[thing][key] = value;

  if (rollback) {
    // One shot. The recovery action must see a normal environment after the
    // rollback; otherwise every valid alternative would be rolled back too and
    // the episode could only loop or escalate. The generation guard prevents a
    // timer from an old episode mutating a reset/restored room.
    const generation = stateGeneration;
    const delayMs = rollback.delay_ms || 800;
    delete faults[thing];
    const timer = setTimeout(() => {
      if (generation !== stateGeneration) return;
      state[thing][key] = previous;
      if (!physicsEnabled) {
        const ramp = RAMPS[thing];
        if (ramp && ramp.commanded === key) state[thing][ramp.measured] = previous;
        if (thing === "projector" && key === "power") {
          state.projector.lamp = previous === "on" ? "on" : "off";
        }
      }
    }, delayMs);
    timer.unref?.();
  }

  // With physics off, a measured value has to follow its command here, because
  // the tick that would otherwise carry it is not running. Leaving it alone
  // freezes the room at whatever the last physical run left behind, which is
  // neither the old instantaneous behaviour nor the new one - it is a third
  // thing that silently fails every device verification.
  if (physicsEnabled) return;
  const ramp = RAMPS[thing];
  if (ramp && ramp.commanded === key) state[thing][ramp.measured] = value;
  if (thing === "projector" && key === "power") state.projector.lamp = value === "on" ? "on" : "off";
}

// ── thing factory ─────────────────────────────────────────────────────────────
async function exposeThing(servient, def) {
  const wot = await servient.start();
  const thing = await wot.produce(def.td);
  for (const [name, key] of Object.entries(def.readables)) {
    thing.setPropertyReadHandler(name, async () => {
      await guard(def.thing, { read: true });
      if (faults[def.thing] && faults[def.thing].type === "malformed") return "NOT_A_NUMBER";
      return state[def.thing][key];
    });
  }
  for (const [name, key] of Object.entries(def.writables || {})) {
    thing.setPropertyWriteHandler(name, async (v) => {
      const generation = await guard(def.thing);
      const value = await v.value();
      assertCurrentGeneration(generation);
      applyWrite(def.thing, key, value);
    });
  }
  for (const [action, handler] of Object.entries(def.actions)) {
    thing.setActionHandler(action, async (params) => {
      const generation = await guard(def.thing);
      const value = params ? await params.value() : undefined;
      assertCurrentGeneration(generation);
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
          // Only the setpoint. The room reaches it on its own schedule, or does
          // not - which is the question the agent has to actually answer. This
          // line used to assign currentTemperature as well, which made the
          // sensor a copy of the command and every verification trivially true.
          applyWrite("thermostat", "targetTemperature", v);
          return undefined;
        },
      },
      td: {
        "@context": "https://www.w3.org/2022/wot/td/v1.1",
        title: "thermostat",
        // Thingweb's HTTP server exposes nosec Things in this demo environment.
        // Parser unit tests and config/wot_td fixtures still cover apikey/basic.
        securityDefinitions: { nosec_sc: { scheme: "nosec" } },
        security: "nosec_sc",
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
      readables: { power: "power", lamp: "lamp" },
      writables: { power: "power" },
      actions: { setPower: (v) => { applyWrite("projector", "power", v); } },
      td: {
        "@context": "https://www.w3.org/2022/wot/td/v1.1",
        title: "projector",
        properties: {
          power: { type: "string", enum: ["on", "off"] },
          // What the switch was told, and what the lamp is doing, are different
          // facts. A dead lamp reports power "on" and never leaves "off".
          lamp: { type: "string", enum: ["on", "off", "warming"], readOnly: true, observable: true },
        },
        actions: { setPower: { input: { type: "string", enum: ["on", "off"] } } },
      },
    },
    {
      thing: "blinds",
      readables: { position: "position", measuredPosition: "measuredPosition" },
      writables: { position: "position" },
      actions: { setPosition: (v) => { applyWrite("blinds", "position", v); } },
      td: {
        "@context": "https://www.w3.org/2022/wot/td/v1.1",
        title: "blinds",
        securityDefinitions: { nosec_sc: { scheme: "nosec" } },
        security: "nosec_sc",
        properties: {
          position: { type: "integer", minimum: 0, maximum: 100 },
          // Where the motor has actually travelled to. Equal to `position` once
          // it arrives, short of it while it is moving, and stuck short of it
          // for good if the motor jams.
          measuredPosition: { type: "number", minimum: 0, maximum: 100, readOnly: true, observable: true },
        },
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

// ── runtime Thing Directory (WoT Discovery) ──────────────────────────────────
function httpGetJson(url) {
  return new Promise((resolve, reject) => {
    http
      .get(url, (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => {
          if (res.statusCode && res.statusCode >= 400) return reject(new Error(`HTTP ${res.statusCode} from ${url}`));
          try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
        });
      })
      .on("error", reject);
  });
}

function startThingDirectory(thingNames, port = 8082, wotPort = 8080) {
  const srv = http.createServer(async (req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Access-Control-Allow-Origin", "*");
    if (req.method === "GET" && (req.url === "/things" || req.url === "/.well-known/wot")) {
      // Aggregate the *live* TDs the servient is exposing, so discovery always
      // reflects the real forms/security rather than a hand-maintained copy.
      const tds = [];
      for (const name of thingNames) {
        try {
          tds.push(await httpGetJson(`http://localhost:${wotPort}/${name}`));
        } catch (e) {
          // A Thing that is momentarily unavailable is simply omitted.
        }
      }
      return res.end(JSON.stringify(tds));
    }
    if (req.method === "GET" && req.url === "/things/links") {
      return res.end(
        JSON.stringify(thingNames.map((n) => ({ id: n, td: `http://localhost:${wotPort}/${n}` }))),
      );
    }
    res.statusCode = 404;
    res.end(JSON.stringify({ error: "not found" }));
  });
  srv.listen(port, () => console.log(`thing directory on :${port} (GET /things)`));
}

// ── control plane (failure injection) ────────────────────────────────────────
function processControlRequest(method, url, body = "", leaseId = "") {
  if (method === "POST" && url === "/lease/acquire") {
    let request;
    try {
      request = JSON.parse(body || "{}");
    } catch (_error) {
      return { statusCode: 400, payload: { error: "invalid lease JSON" } };
    }
    if (
      !isPlainObject(request) ||
      !hasExactKeys(request, ["episode_id"]) ||
      typeof request.episode_id !== "string" ||
      request.episode_id.trim().length === 0 ||
      request.episode_id.length > 256
    ) {
      return { statusCode: 400, payload: { error: "invalid episode lease request" } };
    }
    if (activeLease !== null) {
      return {
        statusCode: 409,
        payload: { error: "episode lease already active", episode_id: activeLease.episodeId },
      };
    }
    const checkpoint = currentCheckpoint();
    activeLease = {
      leaseId: randomUUID(),
      episodeId: request.episode_id,
      checkpoint,
    };
    resetToInitial();
    return {
      statusCode: 200,
      payload: {
        status: "acquired",
        lease_id: activeLease.leaseId,
        episode_id: activeLease.episodeId,
        checkpoint: structuredClone(checkpoint),
      },
    };
  }
  if (method === "POST" && (url === "/lease/restore" || url === "/lease/release")) {
    if (activeLease === null) {
      return { statusCode: 409, payload: { error: "no episode lease is active" } };
    }
    const conflict = leaseConflict(leaseId);
    if (conflict !== null) return conflict;
    const checkpoint = activeLease.checkpoint;
    const episodeId = activeLease.episodeId;
    restoreCheckpoint(checkpoint);
    if (url === "/lease/release") activeLease = null;
    return {
      statusCode: 200,
      payload: {
        status: url === "/lease/release" ? "released" : "restored",
        episode_id: episodeId,
        ...currentCheckpoint(),
      },
    };
  }
  if (method === "POST" && url === "/reset") {
    const conflict = leaseConflict(leaseId);
    if (conflict !== null) return conflict;
    resetToInitial();
    return { statusCode: 200, payload: { status: "reset", state: structuredClone(state) } };
  }
  if (method === "POST" && url === "/restore") {
    const conflict = leaseConflict(leaseId);
    if (conflict !== null) return conflict;
    let checkpoint;
    try {
      checkpoint = JSON.parse(body || "{}");
    } catch (_error) {
      return { statusCode: 400, payload: { error: "invalid checkpoint JSON" } };
    }
    if (!isValidCheckpoint(checkpoint)) {
      return { statusCode: 400, payload: { error: "invalid checkpoint" } };
    }
    restoreCheckpoint(checkpoint);
    return {
      statusCode: 200,
      payload: { status: "restored", state: structuredClone(state), faults: structuredClone(faults) },
    };
  }
  if (method === "POST" && url === "/failure") {
    const conflict = leaseConflict(leaseId);
    if (conflict !== null) return conflict;
    let failure;
    try {
      failure = JSON.parse(body || "{}");
    } catch (_error) {
      return { statusCode: 400, payload: { error: "invalid failure JSON" } };
    }
    if (!isValidFailureRequest(failure)) {
      return { statusCode: 400, payload: { error: "invalid failure request" } };
    }
    if (failure.clear) {
      delete faults[failure.thing];
    } else {
      faults[failure.thing] = { type: failure.type };
      for (const key of ["delay_ms", "read_delay_ms", "drop_probability", "source_reliability"]) {
        if (failure[key] !== undefined) faults[failure.thing][key] = failure[key];
      }
    }
    return { statusCode: 200, payload: { status: "ok", faults: structuredClone(faults) } };
  }
  if (method === "GET" && url === "/state") {
    return {
      statusCode: 200,
      payload: {
        state: structuredClone(state),
        faults: structuredClone(faults),
        // Reported rather than left in the source: a room that reaches
        // temperature in two seconds is running thirty times too fast, and a
        // reader of this endpoint should not have to guess that.
        physics: {
          enabled: physicsEnabled,
          time_scale: TIME_SCALE,
          note: `measured values move at ${TIME_SCALE}x real time`,
          ramps: Object.fromEntries(
            Object.entries(RAMPS).map(([thing, r]) => [
              thing,
              { commanded: r.commanded, measured: r.measured, real_rate_per_sec: r.realRatePerSec },
            ])
          ),
          lamp_warmup_real_sec: LAMP_WARMUP_REAL_SEC,
        },
      },
    };
  }
  if (method === "POST" && url === "/physics") {
    // An escape hatch for evaluation runs that need the old instantaneous room:
    // physics off makes every measured value follow its command immediately.
    let requested;
    try {
      requested = JSON.parse(body || "{}");
    } catch {
      return { statusCode: 400, payload: { error: "body must be JSON" } };
    }
    if (typeof requested.enabled !== "boolean") {
      return { statusCode: 400, payload: { error: "enabled must be true or false" } };
    }
    physicsEnabled = requested.enabled;
    if (!physicsEnabled) {
      // Leaving it off mid-travel would strand the measured values wherever the
      // last tick left them, which is neither physical nor instantaneous.
      for (const [thing, ramp] of Object.entries(RAMPS)) settleRamp(thing, ramp);
      state.projector.lamp = state.projector.power === "on" ? "on" : "off";
    }
    return { statusCode: 200, payload: { status: "ok", physics: { enabled: physicsEnabled } } };
  }
  return { statusCode: 404, payload: { error: "not found" } };
}

function startControlPlane(port = 8081) {
  const srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      res.setHeader("Content-Type", "application/json");
      const leaseId = String(req.headers["x-episode-lease"] || "");
      const result = processControlRequest(req.method || "", req.url || "", body, leaseId);
      res.statusCode = result.statusCode;
      res.end(JSON.stringify(result.payload));
    });
  });
  srv.listen(port, () => console.log(`control plane on :${port}`));
  return srv;
}

async function main() {
  const { Servient } = require("@node-wot/core");
  const { HttpServer } = require("@node-wot/binding-http");
  const servient = new Servient();
  servient.addServer(new HttpServer({ port: 8080 }));
  const defs = buildDefs();
  for (const def of defs) {
    await exposeThing(servient, def);
  }
  startControlPlane(8081);
  startThingDirectory(defs.map((d) => d.thing), 8082, 8080);
  console.log("smart-room WoT servient ready on :8080 (TDs at /<thing>, directory at :8082/things)");
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

module.exports = { applyWrite, guard, isValidCheckpoint, processControlRequest, startControlPlane };
