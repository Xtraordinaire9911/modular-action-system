import React, { useEffect, useMemo, useRef, useState } from "react";

const WOT = "http://localhost:8080";
const POLL_MS = 1500;

/*
 * What changed here, and what deliberately did not.
 *
 * The visual layer was rebuilt: cards, a real type scale, TUM blue to match the
 * slide deck, and a commanded/measured split on every device that has one. The
 * last of those is the only change that carries meaning rather than polish -
 * this project's whole argument is that "the command succeeded" and "the room
 * complied" are different facts, and the interface used to show only the first.
 *
 * Everything the agent and the tests touch is unchanged on purpose:
 *
 *   - every data-testid, spelled the same
 *   - every rendered string, character for character ("booked: Room A @ 14:00",
 *     "20 C", "NOT READY"), because bindings match on text
 *   - the four fault behaviours (stale_temperature, selector_mutation,
 *     layout_shift, disabled_button) and the window hooks that drive them
 *   - the 1500 ms poll and the properties it reads
 *
 * A redesign that moved a testid would have turned a cosmetic commit into a
 * broken demo, and the failure would have looked like a planner bug.
 */

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

function Panel({ title, testid, tag, children }) {
  return (
    <section data-testid={testid} className="panel">
      <header className="panel-head">
        <h2>{title}</h2>
        {tag ? <span className="panel-tag">{tag}</span> : null}
      </header>
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

/* A labelled row. `kind` only tints the label, so the value text stays exactly
 * what it was: bindings and the vision model both read this string. */
function Reading({ label, kind, children }) {
  return (
    <div className="reading">
      <span className={`reading-label reading-${kind}`}>{label}</span>
      <span className="reading-value">{children}</span>
    </div>
  );
}

/* Shown when a device's commanded and measured values disagree.
 *
 * Worded as the plain fact rather than as a diagnosis: from a single reading the
 * page cannot tell a blind still travelling from a blind whose motor is jammed.
 * Claiming "settling" would be a guess presented as a status. */
function Divergence({ diverged }) {
  if (!diverged) return null;
  return <span className="badge badge-diverged">commanded &ne; measured</span>;
}

function DemoPointer({ pointer }) {
  if (!pointer.visible) return null;
  return (
    <div
      className="demo-pointer"
      style={{ left: pointer.x, top: pointer.y }}
    >
      <div className="demo-pointer-arrow" />
      {pointer.label && <div className="demo-pointer-label">{pointer.label}</div>}
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
    position: 100,
    measuredPosition: 100,
  });
  const [tick, setTick] = useState(0);
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
          { outline: "3px solid #8383ff", boxShadow: "0 0 0 6px rgba(131,131,255,.18)" },
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
      const [tt, ct, br, pw, lp, bp, bm] = await Promise.all([
        readProp("thermostat", "targetTemperature"),
        readProp("thermostat", "currentTemperature"),
        readProp("lights", "brightness"),
        readProp("projector", "power"),
        readProp("projector", "lamp"),
        readProp("blinds", "position"),
        readProp("blinds", "measuredPosition"),
      ]);
      if (alive) {
        setDevice((d) => ({
          targetTemperature: tt ?? d.targetTemperature,
          currentTemperature: ct ?? d.currentTemperature,
          brightness: br ?? d.brightness,
          power: pw ?? d.power,
          lamp: lp ?? d.lamp,
          position: bp ?? d.position,
          measuredPosition: bm ?? d.measuredPosition,
        }));
        setTick((n) => n + 1);
      }
    };
    poll();
    const id = setInterval(poll, POLL_MS);
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

  return (
    <main className="shell">
      <style>{`
        :root {
          --tum: #0065BD;
          --tum-dark: #003359;
          --ink: #10141f;
          --muted: #64748b;
          --line: #e2e8f0;
          --card: #ffffff;
          --amber: #b45309;
          --amber-bg: #fef3c7;
          --agent: #8383ff;
        }
        * { box-sizing: border-box; }
        body {
          margin: 0;
          background:
            radial-gradient(1100px 520px at 12% -12%, #e8f1fb 0%, transparent 62%),
            linear-gradient(180deg, #f7f9fc 0%, #f2f5f9 100%);
          color: var(--ink);
          font-family: "Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
          -webkit-font-smoothing: antialiased;
        }
        .shell { max-width: 860px; margin: 0 auto; padding: 26px 22px 40px; }

        .topbar {
          display: flex; align-items: center; gap: 14px;
          padding-bottom: 16px; margin-bottom: 20px;
          border-bottom: 1px solid var(--line);
        }
        .brand {
          width: 34px; height: 34px; flex: none; border-radius: 9px;
          background: linear-gradient(145deg, var(--tum) 0%, var(--tum-dark) 100%);
          box-shadow: 0 3px 10px rgba(0,101,189,.28);
        }
        .topbar h1 {
          margin: 0; font-size: 19px; font-weight: 700; letter-spacing: -.2px;
        }
        .topbar .sub { margin: 2px 0 0; font-size: 12px; color: var(--muted); }
        .pulse {
          margin-left: auto; display: flex; align-items: center; gap: 7px;
          font: 500 11.5px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
          color: var(--muted); white-space: nowrap;
        }
        .pulse i {
          width: 7px; height: 7px; border-radius: 50%; background: #22c55e;
          box-shadow: 0 0 0 0 rgba(34,197,94,.55);
          animation: ping 1.5s ease-out infinite;
        }
        @keyframes ping {
          0%   { box-shadow: 0 0 0 0 rgba(34,197,94,.5); }
          70%  { box-shadow: 0 0 0 7px rgba(34,197,94,0); }
          100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); }
        }

        .grid {
          display: grid; gap: 14px;
          grid-template-columns: repeat(auto-fit, minmax(258px, 1fr));
        }
        .panel {
          background: var(--card); border: 1px solid var(--line);
          border-radius: 13px; padding: 15px 17px 16px;
          box-shadow: 0 1px 2px rgba(16,20,31,.04), 0 8px 22px -14px rgba(16,20,31,.16);
          transition: box-shadow .22s ease, border-color .22s ease;
        }
        .panel:hover {
          border-color: #cfdcea;
          box-shadow: 0 1px 2px rgba(16,20,31,.05), 0 12px 28px -14px rgba(16,20,31,.22);
        }
        .panel-wide { grid-column: 1 / -1; }
        .panel-head {
          display: flex; align-items: center; gap: 9px; margin-bottom: 12px;
        }
        .panel-head h2 {
          margin: 0; font-size: 11px; font-weight: 700;
          letter-spacing: .09em; text-transform: uppercase; color: var(--tum);
        }
        .panel-tag {
          margin-left: auto; font: 500 10.5px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
          color: var(--muted); background: #f1f5f9; border: 1px solid var(--line);
          padding: 3px 7px; border-radius: 20px; white-space: nowrap;
        }

        .reading {
          display: flex; align-items: baseline; gap: 10px;
          padding: 5px 0;
        }
        .reading + .reading { border-top: 1px dashed #eef2f7; }
        .reading-label {
          font: 600 10px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
          letter-spacing: .07em; text-transform: uppercase;
          min-width: 82px; flex: none;
        }
        .reading-commanded { color: var(--tum); }
        .reading-measured  { color: #0f766e; }
        .reading-plain     { color: var(--muted); }
        .reading-value {
          font: 600 21px/1.25 ui-monospace, SFMono-Regular, Menlo, monospace;
          letter-spacing: -.4px;
        }

        /* Preserved from the previous version: the class names and the
           value-pop keyframe are what the flash-on-change behaviour is, and a
           demo script may look for them. Only the palette moved. */
        .value {
          display: inline-block;
          min-width: 2ch;
          border-radius: 5px;
          padding: 1px 5px;
          transition: background-color .25s ease, transform .25s ease;
        }
        .value-flash { animation: value-pop 1.1s ease-out; }
        @keyframes value-pop {
          0%   { background: #cfe4fa; transform: scale(1); }
          35%  { background: #a8cdf5; transform: scale(1.1); }
          100% { background: transparent; transform: scale(1); }
        }

        .badge {
          font: 700 9.5px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
          letter-spacing: .05em; padding: 4px 7px; border-radius: 5px;
          margin-left: 9px; white-space: nowrap; vertical-align: 2px;
        }
        .badge-diverged {
          color: var(--amber); background: var(--amber-bg); border: 1px solid #fcd34d;
        }

        .field-row { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
        .field { display: flex; flex-direction: column; gap: 5px; }
        .field span {
          font: 600 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
          letter-spacing: .07em; text-transform: uppercase; color: var(--muted);
        }
        .field input {
          font: 500 14px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
          padding: 8px 10px; width: 108px; color: var(--ink);
          border: 1px solid #cfdae7; border-radius: 8px; background: #fbfdff;
          transition: border-color .16s ease, box-shadow .16s ease;
        }
        .field input:focus {
          outline: none; border-color: var(--tum);
          box-shadow: 0 0 0 3px rgba(0,101,189,.13);
        }
        button {
          font: 600 13px/1 "Inter", ui-sans-serif, system-ui, sans-serif;
          padding: 10px 17px; color: #fff; cursor: pointer;
          background: linear-gradient(180deg, var(--tum) 0%, #00589f 100%);
          border: 1px solid #005099; border-radius: 8px;
          box-shadow: 0 1px 2px rgba(0,80,153,.28);
          transition: transform .1s ease, filter .16s ease, box-shadow .16s ease;
        }
        button:hover:not(:disabled) { filter: brightness(1.08); box-shadow: 0 3px 10px rgba(0,80,153,.3); }
        button:active:not(:disabled) { transform: translateY(1px); }
        button:disabled {
          cursor: not-allowed; background: #e5eaf1; color: #9aa6b5;
          border-color: #d8e0e9; box-shadow: none;
        }

        .status-line { margin-top: 13px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .status-line .value { font: 600 14px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }

        .ready-wrap { display: flex; align-items: center; gap: 12px; }
        .ready-dot { width: 11px; height: 11px; border-radius: 50%; flex: none; }
        .ready-yes { background: #22c55e; box-shadow: 0 0 0 4px rgba(34,197,94,.16); }
        .ready-no  { background: #cbd5e1; box-shadow: 0 0 0 4px rgba(203,213,225,.24); }
        .ready-wrap .value { font: 700 18px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; }

        .foot {
          margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--line);
          font: 400 11px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
          color: var(--muted);
        }
        .reliability {
          margin-top: 7px;
          font: 500 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
          color: var(--muted);
        }

        .demo-pointer {
          position: fixed; z-index: 9999; pointer-events: none;
          transform: translate(-8px, -8px);
          transition: left 420ms ease, top 420ms ease;
        }
        .demo-pointer-arrow {
          width: 0; height: 0;
          border-left: 18px solid var(--agent);
          border-top: 10px solid transparent;
          border-bottom: 10px solid transparent;
          filter: drop-shadow(0 0 6px rgba(131,131,255,.8));
        }
        .demo-pointer-label {
          margin: 6px 0 0 16px; padding: 5px 9px; border-radius: 7px;
          background: rgba(17,20,32,.93); color: #fff; white-space: nowrap;
          font: 600 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
          border-left: 3px solid var(--agent);
        }

        @media (prefers-reduced-motion: reduce) {
          * { animation-duration: .01ms !important; transition-duration: .01ms !important; }
        }
      `}</style>

      <DemoPointer pointer={pointer} />

      <div className="topbar">
        <div className="brand" />
        <div>
          <h1>Smart-Room Dashboard</h1>
          <p className="sub">W3C Web of Things &middot; five Things on :8080</p>
        </div>
        <div className="pulse">
          <i />
          reading devices &middot; {POLL_MS} ms &middot; {tick} polls
        </div>
      </div>

      <div className="grid">
        <Panel title="Booking" testid="booking-panel" tag="page state">
          <div className="field-row" style={shiftStyle}>
            <label className="field">
              <span>Room</span>
              <input data-testid="room-input" value={room} onChange={(e) => setRoom(e.target.value)} />
            </label>
            <label className="field">
              <span>Time</span>
              <input data-testid="time-input" value={time} onChange={(e) => setTime(e.target.value)} />
            </label>
            <button data-testid={bookTestId} disabled={bookDisabled} onClick={() => setBooked(true)}>
              Book Room
            </button>
          </div>
          <div className="status-line">
            <Value testid="booking-status" value={booked ? `booked: Room ${room} @ ${time}` : "not booked"} />
          </div>
        </Panel>

        <Panel title="Thermostat" testid="thermostat-panel" tag="thermostat">
          <Reading label="Target" kind="commanded">
            <Value testid="target-temp" value={shownTarget} suffix=" C" />
          </Reading>
          <Reading label="Current" kind="measured">
            <Value testid="current-temp" value={device.currentTemperature} suffix=" C" />
            <Divergence diverged={device.currentTemperature !== device.targetTemperature} />
          </Reading>
          {faultConfig.source_reliability ? (
            <div data-testid="source-reliability" className="reliability">
              source_reliability={faultConfig.source_reliability}
            </div>
          ) : null}
        </Panel>

        <Panel title="Lighting" testid="lighting-panel" tag="lights">
          <Reading label="Brightness" kind="plain">
            <Value testid="brightness" value={device.brightness} suffix=" %" />
          </Reading>
        </Panel>

        <Panel title="Projector" testid="projector-panel" tag="projector">
          <Reading label="Power" kind="commanded">
            <Value testid="projector-power" value={device.power} />
          </Reading>
          <Reading label="Lamp" kind="measured">
            <Value testid="projector-lamp" value={device.lamp} />
            <Divergence diverged={(device.power === "on") !== (device.lamp === "on")} />
          </Reading>
        </Panel>

        <Panel title="Blinds" testid="blinds-panel" tag="blinds">
          <Reading label="Position" kind="commanded">
            <Value testid="blinds-position" value={device.position} suffix=" %" />
          </Reading>
          <Reading label="Measured" kind="measured">
            <Value testid="blinds-measured" value={device.measuredPosition} suffix=" %" />
            <Divergence diverged={device.position !== device.measuredPosition} />
          </Reading>
        </Panel>

        <Panel title="Readiness" testid="readiness-panel" tag="derived" >
          <div className="ready-wrap">
            <span className={ready ? "ready-dot ready-yes" : "ready-dot ready-no"} />
            <Value testid="readiness-status" value={ready ? "READY" : "NOT READY"} />
          </div>
        </Panel>
      </div>

      {/* Captions live out here, not inside the panels.
        *
        * Every panel above is a region the vision probe screenshots and asks a
        * model about ("a readiness panel reading READY"). Explanatory prose
        * inside one of those rectangles is extra text in the image the model is
        * judging, which is a quiet way to move a measured number. So the words
        * stay outside the measured regions. */}
      <p className="foot">
        The upper reading in each device is what it was <strong>told</strong>; the lower is
        what it <strong>measures</strong>. A dimmer has only the first &mdash; it really is
        instant. Readiness is derived: booked on this page, projector on, target 22 C,
        lights at or under 40%.
        <br />
        This page only reads. Anything that changes above was written to a Thing by an
        agent, not clicked here.
      </p>
    </main>
  );
}
