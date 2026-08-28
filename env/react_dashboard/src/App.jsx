import React, { useEffect, useMemo, useRef, useState } from "react";

const WOT = new URLSearchParams(window.location.search).get("wot_base") || "http://localhost:8080";

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

function useFaultConfig() {
  return useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    const staleOffset = Number(params.get("stale_offset") ?? "-4");
    const reliability = params.get("source_reliability");
    return {
      stale_offset: Number.isFinite(staleOffset) ? staleOffset : -4,
      source_reliability: reliability || "",
    };
  }, []);
}

async function readProp(thing, prop) {
  try {
    const r = await fetch(`${WOT}/${thing}/properties/${prop}`, { headers: { "X-API-Key": "demo" } });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

async function writeProp(thing, prop, value) {
  try {
    const r = await fetch(`${WOT}/${thing}/properties/${prop}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-API-Key": "demo" },
      body: JSON.stringify(value),
    });
    return r.ok;
  } catch {
    return false;
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
  const faultConfig = useFaultConfig();
  const [booked, setBooked] = useState(false);
  const [room, setRoom] = useState("A");
  const [time, setTime] = useState("14:00");
  const [device, setDevice] = useState({
    targetTemperature: 20,
    currentTemperature: 19,
    brightness: 100,
    power: "off",
    lamp: "off",
  });
  const [pointer, setPointer] = useState({ visible: false, x: 120, y: 120, label: "" });
  const [sessionValid, setSessionValid] = useState(!faults.has("session_expiry"));
  const [presentationStatus, setPresentationStatus] = useState("Idle — projector is off.");
  const [presentationAttempted, setPresentationAttempted] = useState(false);
  const [obstructionPresent, setObstructionPresent] = useState(faults.has("overlay_obstruction"));
  const sessionExpiryInjected = faults.has("session_expiry");
  const overlayObstructionInjected = faults.has("overlay_obstruction");

  useEffect(() => {
    setSessionValid(!sessionExpiryInjected);
  }, [sessionExpiryInjected]);

  useEffect(() => {
    setObstructionPresent(overlayObstructionInjected);
  }, [overlayObstructionInjected]);

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
      const [tt, ct, br, pw, lamp] = await Promise.all([
        readProp("thermostat", "targetTemperature"),
        readProp("thermostat", "currentTemperature"),
        readProp("lights", "brightness"),
        readProp("projector", "power"),
        readProp("projector", "lamp"),
      ]);
      if (alive) {
        setDevice((d) => ({
          targetTemperature: tt ?? d.targetTemperature,
          currentTemperature: ct ?? d.currentTemperature,
          brightness: br ?? d.brightness,
          power: pw ?? d.power,
          lamp: lamp ?? d.lamp,
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

  const shownTarget = faults.has("stale_temperature")
    ? device.targetTemperature + faultConfig.stale_offset
    : device.targetTemperature;
  const bookTestId = faults.has("selector_mutation") ? "book-room-button-v2" : "book-room-button";
  const shiftStyle = faults.has("layout_shift") ? { marginLeft: 50 } : {};
  const bookDisabled = faults.has("disabled_button");

  const ready = useMemo(
    () => booked && device.power === "on" && device.targetTemperature === 22 && device.brightness <= 40,
    [booked, device]
  );

  useEffect(() => {
    if (!presentationAttempted) return;
    if (device.lamp === "on") {
      setPresentationStatus("VERIFIED — the projector lamp is on.");
    } else if (faults.has("optimistic_rollback") && device.power === "off") {
      setPresentationStatus("ROLLED BACK — the dashboard acknowledgement did not persist.");
    }
  }, [device.lamp, device.power, faults, presentationAttempted]);

  const preparePresentation = async () => {
    setPresentationAttempted(true);
    if (!sessionValid) {
      setPresentationStatus("SESSION EXPIRED — command was not sent.");
      return;
    }
    if (faults.has("ineffective_affordance")) {
      setPresentationStatus("ACCEPTED — but the projector is still off.");
      return;
    }
    if (faults.has("optimistic_rollback")) {
      setPresentationStatus("OPTIMISTIC UI — projector reported on, awaiting device confirmation.");
    } else {
      setPresentationStatus("Command sent — waiting for the physical lamp.");
    }
    const accepted = await writeProp("projector", "power", "on");
    if (!accepted) setPresentationStatus("COMMAND FAILED — the device endpoint rejected the write.");
  };

  const renewSession = () => {
    setSessionValid(true);
    setPresentationStatus("SESSION RENEWED — the original goal can resume.");
  };

  const useDirectProjectorControl = async () => {
    setPresentationAttempted(true);
    setPresentationStatus("DIRECT DEVICE CONTROL — waiting for the physical lamp.");
    const accepted = await writeProp("projector", "power", "on");
    if (!accepted) setPresentationStatus("DIRECT CONTROL FAILED — the device endpoint rejected the write.");
  };

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

      {obstructionPresent ? (
        <div
          role="dialog"
          aria-modal="true"
          data-testid="room-policy-overlay"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 9000,
            background: "rgba(15, 23, 42, .72)",
            display: "grid",
            placeItems: "center",
          }}
        >
          <section style={{ width: 420, background: "white", borderRadius: 12, padding: 24 }}>
            <h2>Room policy update</h2>
            <p>Accept the updated room policy before using presentation controls.</p>
            <button
              type="button"
              data-testid="accept-room-policy"
              data-dismiss="dialog"
              onClick={() => setObstructionPresent(false)}
            >
              Accept and continue
            </button>
          </section>
        </div>
      ) : null}

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
        {faultConfig.source_reliability ? (
          <div data-testid="source-reliability" style={{ fontSize: 12, color: "#666" }}>
            source_reliability={faultConfig.source_reliability}
          </div>
        ) : null}
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
        <div>
          Physical lamp: <Value testid="projector-lamp" value={device.lamp} />
        </div>
      </Panel>

      <Panel title="Presentation mode" testid="presentation-panel">
        <div>
          Session: <Value testid="session-state" value={sessionValid ? "valid" : "expired"} />
        </div>
        <div style={{ marginTop: 12 }}>
          <button data-testid="presentation-mode-button" onClick={preparePresentation}>
            Enable presentation mode
          </button>{" "}
          {!sessionValid ? (
            <button data-testid="renew-room-session" onClick={renewSession}>
              Renew room session
            </button>
          ) : null}
          <button data-testid="direct-projector-control" onClick={useDirectProjectorControl}>
            Direct projector control
          </button>
        </div>
        <p data-testid="presentation-status">{presentationStatus}</p>
      </Panel>

      <Panel title="Readiness" testid="readiness-panel">
        <Value testid="readiness-status" value={ready ? "READY" : "NOT READY"} />
      </Panel>
    </main>
  );
}
