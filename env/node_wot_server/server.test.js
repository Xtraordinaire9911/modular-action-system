"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { guard, isValidCheckpoint, processControlRequest } = require("./server");

test("control plane restores a validated checkpoint without accepting malformed state", async () => {
  assert.equal(processControlRequest("POST", "/reset").statusCode, 200);
  const baseline = processControlRequest("GET", "/state").payload;
  const checkpoint = structuredClone(baseline);
  checkpoint.state.thermostat.targetTemperature = 23.5;
  checkpoint.state.thermostat.currentTemperature = 23.5;
  checkpoint.state.lights.brightness = 35;
  checkpoint.faults.thermostat = { type: "offline", delay_ms: 0 };
  assert.equal(isValidCheckpoint(checkpoint), true);

  const restoredResponse = processControlRequest("POST", "/restore", JSON.stringify(checkpoint));
  assert.equal(restoredResponse.statusCode, 200);
  const restored = restoredResponse.payload;
  assert.equal(restored.status, "restored");
  assert.deepEqual({ state: restored.state, faults: restored.faults }, checkpoint);
  assert.deepEqual(processControlRequest("GET", "/state").payload, checkpoint);

  const malformed = structuredClone(checkpoint);
  delete malformed.state.occupancy;
  assert.equal(isValidCheckpoint(malformed), false);
  const rejectedResponse = processControlRequest("POST", "/restore", JSON.stringify(malformed));
  assert.equal(rejectedResponse.statusCode, 400);
  assert.deepEqual(processControlRequest("GET", "/state").payload, checkpoint);

  const invalidFault = structuredClone(checkpoint);
  invalidFault.faults.thermostat.type = "unknown";
  assert.equal(processControlRequest("POST", "/restore", JSON.stringify(invalidFault)).statusCode, 400);
  assert.equal(processControlRequest("POST", "/restore", "not-json").statusCode, 400);
  assert.deepEqual(processControlRequest("GET", "/state").payload, checkpoint);

  const cleanCheckpoint = processControlRequest("POST", "/reset").payload;
  const cleanState = { state: cleanCheckpoint.state, faults: {} };
  processControlRequest("POST", "/failure", JSON.stringify({ thing: "thermostat", type: "timeout", delay_ms: 10 }));
  const delayedInteraction = guard("thermostat");
  assert.equal(processControlRequest("POST", "/restore", JSON.stringify(cleanState)).statusCode, 200);
  await assert.rejects(delayedInteraction, /interaction invalidated/);
  assert.deepEqual(processControlRequest("GET", "/state").payload, cleanState);
});

test("episode lease atomically resets and restores while rejecting competing control clients", async () => {
  processControlRequest("POST", "/reset");
  assert.equal(
    processControlRequest(
      "POST",
      "/failure",
      JSON.stringify({ thing: "thermostat", type: "offline" }),
    ).statusCode,
    200,
  );
  const baseline = processControlRequest("GET", "/state").payload;

  for (const invalid of [
    { thing: "unknown", type: "offline" },
    { thing: "lights", type: "unknown" },
    { thing: "lights", type: "timeout", delay_ms: -1 },
    { thing: "lights", type: "offline", extra: true },
    { thing: "lights", clear: false },
  ]) {
    assert.equal(processControlRequest("POST", "/failure", JSON.stringify(invalid)).statusCode, 400);
  }
  assert.deepEqual(processControlRequest("GET", "/state").payload, baseline);

  const acquired = processControlRequest("POST", "/lease/acquire", JSON.stringify({ episode_id: "episode-a" }));
  assert.equal(acquired.statusCode, 200);
  const leaseA = acquired.payload.lease_id;
  assert.deepEqual(acquired.payload.checkpoint, baseline);
  assert.deepEqual(processControlRequest("GET", "/state").payload.faults, {});

  assert.equal(
    processControlRequest("POST", "/lease/acquire", JSON.stringify({ episode_id: "episode-b" })).statusCode,
    409,
  );
  assert.equal(
    processControlRequest("POST", "/failure", JSON.stringify({ thing: "lights", type: "offline" })).statusCode,
    423,
  );
  assert.equal(processControlRequest("POST", "/reset", "", "wrong-lease").statusCode, 423);
  assert.equal(
    processControlRequest("POST", "/failure", JSON.stringify({ thing: "lights", type: "timeout", delay_ms: 5 }), leaseA)
      .statusCode,
    200,
  );
  await assert.doesNotReject(guard("projector"));

  assert.equal(processControlRequest("POST", "/lease/restore", "", leaseA).statusCode, 200);
  assert.deepEqual(processControlRequest("GET", "/state").payload, baseline);
  assert.equal(processControlRequest("POST", "/reset").statusCode, 423);

  assert.equal(processControlRequest("POST", "/lease/release", "", leaseA).statusCode, 200);
  assert.deepEqual(processControlRequest("GET", "/state").payload, baseline);
  assert.equal(processControlRequest("POST", "/reset", "", leaseA).statusCode, 409);

  const acquiredB = processControlRequest("POST", "/lease/acquire", JSON.stringify({ episode_id: "episode-b" }));
  assert.equal(acquiredB.statusCode, 200);
  assert.equal(processControlRequest("POST", "/lease/release", "", acquiredB.payload.lease_id).statusCode, 200);
  assert.deepEqual(processControlRequest("GET", "/state").payload, baseline);
});
