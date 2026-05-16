import { AbsoluteFill, Video, staticFile, useCurrentFrame } from "remotion";

const HUD_COLOR = "#7DFFBE";
const HUD_DIM = "rgba(125, 255, 190, 0.45)";
const GRID_COLOR = "rgba(125, 255, 190, 0.08)";
const THREAT_COLOR = "#FF3B3B";
const FLASH_COLOR = "#FFFFFF";
const MONO_FONT = "'JetBrains Mono', 'Menlo', 'Courier New', monospace";

const FRAME_WIDTH = 1280;
const FRAME_HEIGHT = 720;

const TARGET = { x: 768, y: 360 };

const WAVE_START_FRAME = 30;
const WAVE_SPEED_PX_PER_FRAME = 7.3;
const WAVE_FADE_START_FRAME = 105;
const WAVE_FADE_END_FRAME = 114;
// "Comparison pause": between the wave fading out and the triangulation lines snapping in,
// the drones hold their RCV_T readouts while the system "compares timestamps".
const LINES_START_FRAME = 135;
const THREAT_FRAME = 145;

const MS_PER_PIXEL = 0.3;

// All drones drift rightward at this speed to match the forest moving
// past in the video (camera follows the stationary-on-screen tank).
// Tweak this if the drone motion doesn't match the apparent tree speed.
const DRIFT_VX = 0.5;

type Vec2 = { x: number; y: number };

type DroneSpec = {
  id: "D1" | "D2" | "D3";
  start: Vec2;
  vx: number;
  vy: number;
  wobbleAmp: number;
  wobblePeriod: number;
};

const DRONES: DroneSpec[] = [
  { id: "D1", start: { x: 200, y: 180 }, vx: DRIFT_VX, vy: -0.1, wobbleAmp: 2,   wobblePeriod: 40 },
  { id: "D2", start: { x: 950, y: 180 }, vx: DRIFT_VX, vy: 0, wobbleAmp: 2,   wobblePeriod: 40 },
  { id: "D3", start: { x: 600, y: 560 }, vx: DRIFT_VX, vy: 0, wobbleAmp: 1.5, wobblePeriod: 35 },
];

const dronePos = (spec: DroneSpec, frame: number): Vec2 => ({
  x: spec.start.x + frame * spec.vx + Math.sin(frame / spec.wobblePeriod) * spec.wobbleAmp,
  y: spec.start.y + frame * spec.vy,
});

const dist = (a: Vec2, b: Vec2): number => Math.hypot(a.x - b.x, a.y - b.y);

const waveRadius = (frame: number): number => {
  if (frame < WAVE_START_FRAME) return 0;
  return (frame - WAVE_START_FRAME) * WAVE_SPEED_PX_PER_FRAME;
};

const waveOpacity = (frame: number): number => {
  if (frame < WAVE_FADE_START_FRAME) return 1;
  if (frame > WAVE_FADE_END_FRAME) return 0;
  return 1 - (frame - WAVE_FADE_START_FRAME) / (WAVE_FADE_END_FRAME - WAVE_FADE_START_FRAME);
};

type DroneHit = {
  spec: DroneSpec;
  hitFrame: number | null;
  hitDistance: number | null;
};

const computeDroneHits = (currentFrame: number): DroneHit[] => {
  return DRONES.map((spec) => {
    let hitFrame: number | null = null;
    let hitDistance: number | null = null;
    for (let f = WAVE_START_FRAME; f <= currentFrame; f++) {
      const d = dist(dronePos(spec, f), TARGET);
      if (waveRadius(f) >= d) {
        hitFrame = f;
        hitDistance = d;
        break;
      }
    }
    return { spec, hitFrame, hitDistance };
  });
};

const formatRcvT = (ms: number): string => {
  const sign = ms < 0 ? "-" : "";
  const abs = Math.abs(ms);
  const intPart = Math.floor(abs).toString().padStart(2, "0");
  const decPart = Math.round((abs - Math.floor(abs)) * 100)
    .toString()
    .padStart(2, "0");
  return `${sign}${intPart}.${decPart}ms`;
};

const GRID_SPACING = 80;

const TacticalGrid: React.FC = () => {
  const verticals: number[] = [];
  for (let x = GRID_SPACING; x < FRAME_WIDTH; x += GRID_SPACING) verticals.push(x);
  const horizontals: number[] = [];
  for (let y = GRID_SPACING; y < FRAME_HEIGHT; y += GRID_SPACING) horizontals.push(y);

  return (
    <svg
      width={FRAME_WIDTH}
      height={FRAME_HEIGHT}
      style={{ position: "absolute", top: 0, left: 0, pointerEvents: "none" }}
    >
      {verticals.map((x) => (
        <line key={`v${x}`} x1={x} y1={0} x2={x} y2={FRAME_HEIGHT} stroke={GRID_COLOR} strokeWidth={1} />
      ))}
      {horizontals.map((y) => (
        <line key={`h${y}`} x1={0} y1={y} x2={FRAME_WIDTH} y2={y} stroke={GRID_COLOR} strokeWidth={1} />
      ))}
    </svg>
  );
};

const DRONE_SIZE = 16;

const Drone: React.FC<{
  pos: Vec2;
  active: boolean;
  rcvText: string | null;
}> = ({ pos, active, rcvText }) => {
  const half = DRONE_SIZE / 2;
  const points = `${pos.x},${pos.y - half} ${pos.x - half},${pos.y + half} ${pos.x + half},${pos.y + half}`;
  const stroke = active ? HUD_COLOR : HUD_DIM;
  return (
    <>
      <polygon points={points} fill="none" stroke={stroke} strokeWidth={1.5} />
      {active && <circle cx={pos.x} cy={pos.y} r={1} fill={HUD_COLOR} />}
      {active && rcvText !== null && (
        <text
          x={pos.x + half + 12}
          y={pos.y + 5}
          fill={HUD_COLOR}
          fontFamily={MONO_FONT}
          fontSize={18}
        >
          {rcvText}
        </text>
      )}
    </>
  );
};

const Drones: React.FC<{ frame: number }> = ({ frame }) => {
  const hits = computeDroneHits(frame);

  return (
    <svg
      width={FRAME_WIDTH}
      height={FRAME_HEIGHT}
      style={{ position: "absolute", top: 0, left: 0, pointerEvents: "none" }}
    >
      {hits.map((hit) => {
        const pos = dronePos(hit.spec, frame);
        const active = hit.hitFrame !== null && frame >= hit.hitFrame;
        let rcvText: string | null = null;
        if (active && hit.hitDistance !== null) {
          rcvText = `RCV_T: ${formatRcvT(hit.hitDistance * MS_PER_PIXEL)}`;
        }
        return (
          <Drone
            key={hit.spec.id}
            pos={pos}
            active={active}
            rcvText={rcvText}
          />
        );
      })}
    </svg>
  );
};

const Shockwave: React.FC<{ frame: number }> = ({ frame }) => {
  const r = waveRadius(frame);
  const opacity = waveOpacity(frame);
  if (r <= 0 || opacity <= 0) return null;
  return (
    <svg
      width={FRAME_WIDTH}
      height={FRAME_HEIGHT}
      style={{ position: "absolute", top: 0, left: 0, pointerEvents: "none" }}
    >
      <circle
        cx={TARGET.x}
        cy={TARGET.y}
        r={r}
        fill="none"
        stroke={HUD_COLOR}
        strokeWidth={1.5}
        opacity={opacity}
      />
    </svg>
  );
};

const TriangulationLines: React.FC<{ frame: number }> = ({ frame }) => {
  if (frame < LINES_START_FRAME) return null;
  return (
    <svg
      width={FRAME_WIDTH}
      height={FRAME_HEIGHT}
      style={{ position: "absolute", top: 0, left: 0, pointerEvents: "none" }}
    >
      {DRONES.map((spec) => {
        const p = dronePos(spec, frame);
        return (
          <line
            key={spec.id}
            x1={p.x}
            y1={p.y}
            x2={TARGET.x}
            y2={TARGET.y}
            stroke={HUD_COLOR}
            strokeWidth={1}
            strokeDasharray="4 4"
          />
        );
      })}
    </svg>
  );
};

const THREAT_RADIUS = 12;
const THREAT_STROKE = 2;

const ThreatMarker: React.FC<{ frame: number }> = ({ frame }) => {
  if (frame < THREAT_FRAME) return null;
  const color = frame === THREAT_FRAME ? FLASH_COLOR : THREAT_COLOR;
  return (
    <svg
      width={FRAME_WIDTH}
      height={FRAME_HEIGHT}
      style={{ position: "absolute", top: 0, left: 0, pointerEvents: "none" }}
    >
      <circle
        cx={TARGET.x}
        cy={TARGET.y}
        r={THREAT_RADIUS}
        fill="none"
        stroke={color}
        strokeWidth={THREAT_STROKE}
      />
      <line
        x1={TARGET.x - THREAT_RADIUS}
        y1={TARGET.y}
        x2={TARGET.x + THREAT_RADIUS}
        y2={TARGET.y}
        stroke={color}
        strokeWidth={THREAT_STROKE}
      />
      <line
        x1={TARGET.x}
        y1={TARGET.y - THREAT_RADIUS}
        x2={TARGET.x}
        y2={TARGET.y + THREAT_RADIUS}
        stroke={color}
        strokeWidth={THREAT_STROKE}
      />
      <text
        x={TARGET.x + THREAT_RADIUS + 16}
        y={TARGET.y + 6}
        fill={color}
        fontFamily={MONO_FONT}
        fontSize={20}
        letterSpacing={1}
      >
        THREAT LOCALIZED: TANK
      </text>
    </svg>
  );
};

export const Scene2: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ background: "black" }}>
      <Video
        src={staticFile("topdown.mp4")}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
      <TacticalGrid />
      <Drones frame={frame} />
      <Shockwave frame={frame} />
      <TriangulationLines frame={frame} />
      <ThreatMarker frame={frame} />
    </AbsoluteFill>
  );
};
