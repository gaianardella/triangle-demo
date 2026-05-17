import {
  AbsoluteFill,
  Video,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { seededRand } from "./hud-utils";
import { AcousticAnalyzerTopLeft } from "./AcousticAnalyzerTopLeft";

const HUD_COLOR = "#7DFFBE";
const HUD_DIM = "rgba(125, 255, 190, 0.45)";
const MONO_FONT = "'JetBrains Mono', 'Menlo', 'Courier New', monospace";

const Crosshair: React.FC = () => {
  const bracket = 24;
  const gap = 14;
  const stroke = 1.5;
  return (
    <div
      style={{
        position: "absolute",
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
        width: bracket * 2 + gap * 2,
        height: bracket * 2 + gap * 2,
      }}
    >
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          width: 3,
          height: 3,
          background: HUD_COLOR,
          transform: "translate(-50%, -50%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: bracket,
          height: bracket,
          borderColor: HUD_COLOR,
          borderStyle: "solid",
          borderWidth: `${stroke}px 0 0 ${stroke}px`,
        }}
      />
      <div
        style={{
          position: "absolute",
          top: 0,
          right: 0,
          width: bracket,
          height: bracket,
          borderColor: HUD_COLOR,
          borderStyle: "solid",
          borderWidth: `${stroke}px ${stroke}px 0 0`,
        }}
      />
      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          width: bracket,
          height: bracket,
          borderColor: HUD_COLOR,
          borderStyle: "solid",
          borderWidth: `0 0 ${stroke}px ${stroke}px`,
        }}
      />
      <div
        style={{
          position: "absolute",
          bottom: 0,
          right: 0,
          width: bracket,
          height: bracket,
          borderColor: HUD_COLOR,
          borderStyle: "solid",
          borderWidth: `0 ${stroke}px ${stroke}px 0`,
        }}
      />
    </div>
  );
};

const ArtificialHorizon: React.FC<{ frame: number }> = ({ frame }) => {
  const width = 520;
  const rollDeg = Math.sin(frame / 38) * 0.22 + Math.sin(frame / 11) * 0.08;

  return (
    <div
      style={{
        position: "absolute",
        top: "50%",
        left: "50%",
        transform: `translate(-50%, -50%) rotate(${rollDeg}deg)`,
        width,
        height: 1,
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: width / 2 - 70,
          height: 1,
          background: HUD_COLOR,
        }}
      />
      <div
        style={{
          position: "absolute",
          right: 0,
          top: 0,
          width: width / 2 - 70,
          height: 1,
          background: HUD_COLOR,
        }}
      />
      {[-2, -1, 1, 2].map((i) => (
        <div
          key={i}
          style={{
            position: "absolute",
            left: "50%",
            top: i * 28 - 0.5,
            transform: "translateX(-50%)",
            width: 60,
            height: 1,
            background: HUD_DIM,
          }}
        />
      ))}
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: -4,
          transform: "translateX(-50%)",
          fontFamily: MONO_FONT,
          fontSize: 10,
          color: HUD_DIM,
          letterSpacing: 1,
        }}
      >
        {rollDeg >= 0 ? "+" : ""}
        {rollDeg.toFixed(1)}°
      </div>
    </div>
  );
};

const TelemetryBottomLeft: React.FC<{ frame: number }> = ({ frame }) => {
  const alt = 4.2 + Math.sin(frame / 18) * 0.25 + seededRand(frame, 1.7) * 0.08;
  const spd = 6.0 + Math.sin(frame / 12) * 0.25 + seededRand(frame, 2.3) * 0.08;
  const hdg = 47;

  const Line: React.FC<{ label: string; value: string }> = ({
    label,
    value,
  }) => (
    <div style={{ display: "flex", gap: 10 }}>
      <span style={{ color: HUD_DIM, width: 48 }}>{label}</span>
      <span style={{ color: HUD_COLOR }}>{value}</span>
    </div>
  );

  return (
    <div
      style={{
        position: "absolute",
        left: 48,
        bottom: 48,
        fontFamily: MONO_FONT,
        fontSize: 16,
        lineHeight: 1.5,
        letterSpacing: 1,
      }}
    >
      <Line label="ALT:" value={`${alt.toFixed(1).padStart(4, "0")}m`} />
      <Line
        label="HDG:"
        value={`${hdg.toString().padStart(3, "0")}°`}
      />
      <Line label="SPD:" value={`${spd.toFixed(1).padStart(4, "0")} m/s`} />
    </div>
  );
};

const RpmTopRight: React.FC<{ frame: number }> = ({ frame }) => {
  const rpm = Math.round(
    6300 + seededRand(frame, 3.1) * 200 + Math.sin(frame * 1.3) * 8
  );

  return (
    <div
      style={{
        position: "absolute",
        right: 48,
        top: 48,
        fontFamily: MONO_FONT,
        fontSize: 16,
        lineHeight: 1.5,
        letterSpacing: 1,
        textAlign: "right",
      }}
    >
      <div>
        <span style={{ color: HUD_DIM }}>MTR_RPM: </span>
        <span style={{ color: HUD_COLOR }}>{rpm}</span>
      </div>
      <div>
        <span style={{ color: HUD_DIM }}>FILTER:  </span>
        <span style={{ color: HUD_COLOR }}>ACTIVE</span>
      </div>
      <div style={{ marginTop: 8, display: "flex", justifyContent: "flex-end", gap: 2 }}>
        {Array.from({ length: 20 }).map((_, i) => {
          const norm = (rpm - 6300) / 200;
          const active = i / 20 < norm;
          return (
            <div
              key={i}
              style={{
                width: 6,
                height: 10,
                background: active ? HUD_COLOR : "transparent",
                border: `1px solid ${active ? HUD_COLOR : HUD_DIM}`,
              }}
            />
          );
        })}
      </div>
    </div>
  );
};

const BatteryBottomRight: React.FC = () => {
  const pct = 84;
  const segments = 10;
  const filled = Math.round((pct / 100) * segments);

  return (
    <div
      style={{
        position: "absolute",
        right: 48,
        bottom: 48,
        fontFamily: MONO_FONT,
        fontSize: 16,
        letterSpacing: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-end",
        gap: 6,
      }}
    >
      <div>
        <span style={{ color: HUD_DIM }}>PWR: </span>
        <span style={{ color: HUD_COLOR }}>{pct}%</span>
      </div>
      <div style={{ display: "flex", alignItems: "center" }}>
        <div
          style={{
            display: "flex",
            border: `1.5px solid ${HUD_COLOR}`,
            padding: 2,
            gap: 2,
          }}
        >
          {Array.from({ length: segments }).map((_, i) => (
            <div
              key={i}
              style={{
                width: 10,
                height: 14,
                background: i < filled ? HUD_COLOR : "transparent",
              }}
            />
          ))}
        </div>
        <div
          style={{
            width: 4,
            height: 10,
            background: HUD_COLOR,
            marginLeft: 2,
          }}
        />
      </div>
    </div>
  );
};

const FrameCounter: React.FC<{ frame: number }> = ({ frame }) => {
  const { fps } = useVideoConfig();
  const t = (frame / fps).toFixed(2);
  return (
    <div
      style={{
        position: "absolute",
        left: "50%",
        bottom: 24,
        transform: "translateX(-50%)",
        fontFamily: MONO_FONT,
        fontSize: 12,
        color: HUD_DIM,
        letterSpacing: 2,
      }}
    >
      T+{t.padStart(5, "0")}s · FRM {String(frame).padStart(4, "0")}
    </div>
  );
};

export const Scene1: React.FC = () => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill style={{ background: "black" }}>
      <Video
        src={staticFile("drone-fpv-720p.mp4")}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
      <AbsoluteFill style={{ pointerEvents: "none" }}>
        <Crosshair />
        <ArtificialHorizon frame={frame} />
        <TelemetryBottomLeft frame={frame} />
        <RpmTopRight frame={frame} />
        <AcousticAnalyzerTopLeft frame={frame} />
        <BatteryBottomRight />
        <FrameCounter frame={frame} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
