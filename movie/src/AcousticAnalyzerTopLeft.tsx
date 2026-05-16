import { seededRand } from "./hud-utils";

const HUD_COLOR = "#7DFFBE";
const HUD_DIM = "rgba(125, 255, 190, 0.45)";
const MONO_FONT = "'JetBrains Mono', 'Menlo', 'Courier New', monospace";

const BAR_COUNT = 16;
const BAR_WIDTH = 6;
const BAR_GAP = 2;
const BAR_TRACK_HEIGHT = 22;

const APPROACH_START = 15;
const APPROACH_END = 299;
const LOCK_FRAME = 180;

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

const lockedReadout = (frame: number) => {
  const t = Math.max(
    0,
    Math.min(1, (frame - LOCK_FRAME) / (APPROACH_END - LOCK_FRAME))
  );
  const confJitter = (seededRand(frame, 5.1) - 0.5) * 2;
  const confidence = Math.round(lerp(87, 95, t) + confJitter);
  const rangeJitter = (seededRand(frame, 7.3) - 0.5) * 10;
  const rangeRaw = lerp(410, 290, t) + rangeJitter;
  const range = Math.round(rangeRaw / 10) * 10;
  return { confidence, range, bearing: 218 };
};

const approach = (frame: number): number => {
  if (frame < APPROACH_START) return 0;
  const t = (frame - APPROACH_START) / (APPROACH_END - APPROACH_START);
  return Math.sqrt(t);
};

const lowpassWeight = (i: number): number => {
  const w = 1 - Math.pow(i / 6, 1.5);
  return Math.max(0, w);
};

const SpectrumBars: React.FC<{ frame: number }> = ({ frame }) => {
  return (
    <div
      style={{
        display: "flex",
        gap: BAR_GAP,
        marginTop: 4,
      }}
    >
      {Array.from({ length: BAR_COUNT }).map((_, i) => {
        const baseline = 0.08 + 0.04 * seededRand(0, i + 1);
        const env = approach(frame);
        const lp = lowpassWeight(i);
        const jitter = (seededRand(frame, i * 3.7 + 0.9) - 0.5) * 0.1;
        const signal = baseline + env * lp * 0.9;
        const heightNorm = Math.max(
          0,
          Math.min(1, signal + (baseline + 0.4 * env * lp) * jitter)
        );
        const fillPx = Math.round(heightNorm * BAR_TRACK_HEIGHT);
        return (
          <div
            key={i}
            style={{
              position: "relative",
              width: BAR_WIDTH,
              height: BAR_TRACK_HEIGHT,
              border: `1px solid ${HUD_DIM}`,
              boxSizing: "border-box",
            }}
          >
            <div
              style={{
                position: "absolute",
                left: 0,
                right: 0,
                bottom: 0,
                height: fillPx,
                background: HUD_COLOR,
              }}
            />
          </div>
        );
      })}
    </div>
  );
};

const WAVE_SAMPLES = 60;
const WAVE_WIDTH = 180;
const WAVE_HEIGHT = 16;

const Waveform: React.FC<{ frame: number }> = ({ frame }) => {
  const points: string[] = [];
  for (let s = 0; s < WAVE_SAMPLES; s++) {
    const k = frame - (WAVE_SAMPLES - 1 - s);
    const amp = approach(k) * 0.9;
    const phase = k * ((2 * Math.PI) / 8);
    const noise = (seededRand(k, 11.2) - 0.5) * 0.2;
    const sample = amp * Math.sin(phase) + amp * noise;
    const x = (s / (WAVE_SAMPLES - 1)) * WAVE_WIDTH;
    const y = WAVE_HEIGHT / 2 - sample * (WAVE_HEIGHT / 2 - 1);
    points.push(`${x.toFixed(2)},${y.toFixed(2)}`);
  }
  return (
    <svg
      width={WAVE_WIDTH}
      height={WAVE_HEIGHT}
      style={{ display: "block", marginTop: 2 }}
    >
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={HUD_COLOR}
        strokeWidth={1}
      />
    </svg>
  );
};

const StatusLine: React.FC<{ frame: number }> = ({ frame }) => {
  const isLocked = frame >= LOCK_FRAME;
  const isFlash = frame === LOCK_FRAME;
  const color = isFlash ? "#FFFFFF" : isLocked ? HUD_COLOR : HUD_DIM;

  if (!isLocked) {
    return (
      <div style={{ marginTop: 4, color }}>
        <div>LISTENING</div>
        <div>—</div>
      </div>
    );
  }

  const { confidence, range, bearing } = lockedReadout(frame);
  return (
    <div style={{ marginTop: 4, color }}>
      <div>CLASS: TANK   {confidence}%</div>
      <div>
        BRG {bearing}°  RNG {range}m
      </div>
    </div>
  );
};

export const AcousticAnalyzerTopLeft: React.FC<{ frame: number }> = ({
  frame,
}) => {
  return (
    <div
      style={{
        position: "absolute",
        left: 48,
        top: 48,
        width: 200,
        fontFamily: MONO_FONT,
        fontSize: 16,
        lineHeight: 1.5,
        letterSpacing: 1,
        color: HUD_COLOR,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          style={{
            display: "inline-block",
            width: 8,
            height: 8,
            background:
              frame < LOCK_FRAME ||
              Math.floor((frame - LOCK_FRAME) / 15) % 2 === 0
                ? HUD_COLOR
                : HUD_DIM,
            borderRadius: "50%",
          }}
        />
        ACOUSTIC
      </div>
      <SpectrumBars frame={frame} />
      <Waveform frame={frame} />
      <StatusLine frame={frame} />
    </div>
  );
};
