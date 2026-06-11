import React, { useEffect, useMemo, useState } from "react";

const WOT = "http://localhost:8080";

// ── controlled failure hooks (advisor §11.1 Chaos Monkey, DOM side) ──────────
// Toggle via URL (?fault=layout_shift) or programmatically from Playwright:
//   await page.evaluate(() => window.__injectFault("selector_mutation"))
function useFaults() {
  const [faults, setFaults] = useState(() => {
    const q = new URLSearchParams(window.location.search).get("fault");
    return new Set(q ? q.split(",") : []);
  });
  useEffect(() => {
    window.__injectFault = (t) => setFaults((s) => new Set(s).add(t));
    window.__clearFaults = () => setFaults(new Set());
  }, []);
  return faults;
}

async function readProp(thing, prop) {
  try {
    const r = await fetch(`${WOT}/${thing}/properties/${prop}`, { headers: { "X-API-Key": "demo" } });
    return r.ok ? await r.json() : null;
  } catch {
    return null; // WoT unreachable → dashboard still renders from local state
  }
}

function Panel({ title, testid, children }) {
  return (
    <section data-testid={testid} style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, margin: 12 }}>
      <h2 style={{ marginTop: 0, fontSize: 16 }}>{title}</h2>
      {children}
    </section>
  );
}

export default function App() {
  const faults = useFaults();
  const [booked, setBooked] = useState(false);
  const [room, setRoom] = useState("A");
  const [time, setTime] = useState("14:00");
  const [device, setDevice] = useState({ targetTemperature: 20, currentTemperature: 19, brightness: 100, power: "off" });

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      const [tt, ct, br, pw] = await Promise.all([
        readProp("thermostat", "targetTemperature"),
        readProp("thermostat", "currentTemperature"),
        readProp("lights", "brightness"),
        readProp("projector", "power"),
      ]);
      if (alive) {
        setDevice((d) => ({
          targetTemperature: tt ?? d.targetTemperature,
          currentTemperature: ct ?? d.currentTemperature,
          brightness: br ?? d.brightness,
          power: pw ?? d.power,
        }));
      }
    };
    poll();
    const id = setInterval(poll, 1500);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // stale_temperature fault: dashboard disagrees with the physical sensor by 4°C.
  const shownTarget = faults.has("stale_temperature") ? device.targetTemperature - 4 : device.targetTemperature;
  // selector_mutation fault: the Book button's stable test id changes.
  const bookTestId = faults.has("selector_mutation") ? "book-room-button-v2" : "book-room-button";
  // layout_shift fault: push the booking action 50px to the right.
  const shiftStyle = faults.has("layout_shift") ? { marginLeft: 50 } : {};
  const bookDisabled = faults.has("disabled_button");

  const ready = useMemo(
    () => booked && device.power === "on" && device.targetTemperature === 22 && device.brightness <= 40,
    [booked, device]
  );

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 720, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22 }}>Smart-Room Dashboard</h1>

      <Panel title="Booking" testid="booking-panel">
        <label>Room <input data-testid="room-input" value={room} onChange={(e) => setRoom(e.target.value)} /></label>{" "}
        <label>Time <input data-testid="time-input" value={time} onChange={(e) => setTime(e.target.value)} /></label>
        <div style={{ marginTop: 12, ...shiftStyle }}>
          <button data-testid={bookTestId} disabled={bookDisabled} onClick={() => setBooked(true)}>
            Book Room
          </button>{" "}
          <span data-testid="booking-status">{booked ? `booked: Room ${room} @ ${time}` : "not booked"}</span>
        </div>
      </Panel>

      <Panel title="Thermostat" testid="thermostat-panel">
        <div>Target: <span data-testid="target-temp">{shownTarget}</span> °C</div>
        <div>Current: <span data-testid="current-temp">{device.currentTemperature}</span> °C</div>
      </Panel>

      <Panel title="Lighting" testid="lighting-panel">
        <div>Brightness: <span data-testid="brightness">{device.brightness}</span> %</div>
      </Panel>

      <Panel title="Projector" testid="projector-panel">
        <div>Power: <span data-testid="projector-power">{device.power}</span></div>
      </Panel>

      <Panel title="Readiness" testid="readiness-panel">
        <span data-testid="readiness-status">{ready ? "READY" : "NOT READY"}</span>
      </Panel>
    </main>
  );
}
