import React, { useEffect, useMemo, useRef, useState } from "react";

const WOT = "http://localhost:8080";

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
    return null;
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

function useFlashOnChange(value) {
  const first = useRef(true);
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    setFlash(true);
    const id = setTimeout(() => setFlash(false), 1100);
    return () => clearTimeout(id);
  }, [value]);
  return flash;
}

function Value({ testid, value, suffix = "" }) {
  const flash = useFlashOnChange(value);
  return (
    <span data-testid={testid} className={flash ? "value value-flash" : "value"}>
      {value}
      {suffix}
    </span>
  );
}

function DemoPointer({ pointer }) {
  if (!pointer.visible) return null;
  return (
    <div
      style={{
        position: "fixed",
        left: pointer.x,
        top: pointer.y,
        zIndex: 9999,
        pointerEvents: "none",
        transform: "translate(-8px, -8px)",
        transition: "left 420ms ease, top 420ms ease",
      }}
    >
      <div
        style={{
          width: 0,
          height: 0,
          borderLeft: "18px solid #111",
          borderTop: "10px solid transparent",
          borderBottom: "10px solid transparent",
          filter: "drop-shadow(0 2px 3px rgba(0,0,0,.35))",
        }}
      />
      {pointer.label && (
        <div
          style={{
            marginTop: 6,
            marginLeft: 16,
            background: "#111",
            color: "#fff",
            borderRadius: 4,
            padding: "5px 8px",
            fontSize: 12,
            whiteSpace: "nowrap",
          }}
        >
          {pointer.label}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const faults = useFaults();
  const [booked, setBooked] = useState(false);
  const [room, setRoom] = useState("A");
  const [time, setTime] = useState("14:00");
  const [device, setDevice] = useState({
    targetTemperature: 20,
    currentTemperature: 19,
    brightness: 100,
    power: "off",
  });
  const [pointer, setPointer] = useState({ visible: false, x: 120, y: 120, label: "" });

  useEffect(() => {
    window.__demoPointTo = (selector, label = "") => {
      const el = document.querySelector(selector);
      if (!el) return false;
      const r = el.getBoundingClientRect();
      setPointer({ visible: true, x: r.left + r.width / 2, y: r.top + r.height / 2, label });
      el.animate(
        [
          { outline: "0 solid rgba(0,0,0,0)", boxShadow: "0 0 0 0 rgba(0,0,0,0)" },
          { outline: "3px solid #111", boxShadow: "0 0 0 6px rgba(0,0,0,.10)" },
          { outline: "0 solid rgba(0,0,0,0)", boxShadow: "0 0 0 0 rgba(0,0,0,0)" },
        ],
        { duration: 1200, easing: "ease-out" }
      );
      return true;
    };
    window.__demoHidePointer = () => setPointer((p) => ({ ...p, visible: false }));
  }, []);

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
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const shownTarget = faults.has("stale_temperature") ? device.targetTemperature - 4 : device.targetTemperature;
  const bookTestId = faults.has("selector_mutation") ? "book-room-button-v2" : "book-room-button";
  const shiftStyle = faults.has("layout_shift") ? { marginLeft: 50 } : {};
  const bookDisabled = faults.has("disabled_button");

  const ready = useMemo(
    () => booked && device.power === "on" && device.targetTemperature === 22 && device.brightness <= 40,
    [booked, device]
  );

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 720, margin: "0 auto" }}>
      <style>{`
        .value {
          display: inline-block;
          min-width: 2ch;
          border-radius: 4px;
          padding: 1px 4px;
          transition: background-color .25s ease, transform .25s ease;
        }
        .value-flash {
          animation: value-pop 1.1s ease-out;
        }
        @keyframes value-pop {
          0% { background: #ffe08a; transform: scale(1); }
          35% { background: #ffd24d; transform: scale(1.14); }
          100% { background: transparent; transform: scale(1); }
        }
      `}</style>
      <DemoPointer pointer={pointer} />
      <h1 style={{ fontSize: 22 }}>Smart-Room Dashboard</h1>

      <Panel title="Booking" testid="booking-panel">
        <label>
          Room <input data-testid="room-input" value={room} onChange={(e) => setRoom(e.target.value)} />
        </label>{" "}
        <label>
          Time <input data-testid="time-input" value={time} onChange={(e) => setTime(e.target.value)} />
        </label>
        <div style={{ marginTop: 12, ...shiftStyle }}>
          <button data-testid={bookTestId} disabled={bookDisabled} onClick={() => setBooked(true)}>
            Book Room
          </button>{" "}
          <Value testid="booking-status" value={booked ? `booked: Room ${room} @ ${time}` : "not booked"} />
        </div>
      </Panel>

      <Panel title="Thermostat" testid="thermostat-panel">
        <div>
          Target: <Value testid="target-temp" value={shownTarget} suffix=" C" />
        </div>
        <div>
          Current: <Value testid="current-temp" value={device.currentTemperature} suffix=" C" />
        </div>
      </Panel>

      <Panel title="Lighting" testid="lighting-panel">
        <div>
          Brightness: <Value testid="brightness" value={device.brightness} suffix=" %" />
        </div>
      </Panel>

      <Panel title="Projector" testid="projector-panel">
        <div>
          Power: <Value testid="projector-power" value={device.power} />
        </div>
      </Panel>

      <Panel title="Readiness" testid="readiness-panel">
        <Value testid="readiness-status" value={ready ? "READY" : "NOT READY"} />
      </Panel>
    </main>
  );
}
