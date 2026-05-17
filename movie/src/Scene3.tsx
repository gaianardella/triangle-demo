import {
  AbsoluteFill,
  Audio,
  Img,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import manifest from "./spectrogramManifest.json";

const HUD_COLOR = "#7DFFBE";
const HUD_DIM = "rgba(125, 255, 190, 0.45)";
const HUD_FAINT = "rgba(125, 255, 190, 0.18)";
const GRID_COLOR = "rgba(125, 255, 190, 0.07)";
const THREAT_COLOR = "#FF3B3B";
const FLASH_COLOR = "#FFFFFF";
const MONO_FONT = "'JetBrains Mono', 'Menlo', 'Courier New', monospace";

const FRAME_WIDTH = 1280;
const FRAME_HEIGHT = 720;
const HEAD_FRAMES = manifest.headFrames;
const TAIL_FRAMES = manifest.tailFrames;

const PANEL_W = 580;
const PANEL_H = 340;
const PANEL_TOP = 150;
const PANEL_GAP = 40;
const LEFT_X = (FRAME_WIDTH - (PANEL_W * 2 + PANEL_GAP)) / 2;
const RIGHT_X = LEFT_X + PANEL_W + PANEL_GAP;

const VERDICT_TOP = PANEL_TOP + PANEL_H + 36;
const VERDICT_H = 130;

type Verdict = {
  label: string | null;
  labelHuman: string | null;
  relevant: boolean;
  peakConfidence: number;
  shareConfidence: number;
  windowCounts: Record<string, number>;
  silentWindows: number;
  totalWindows: number;
};

type Scenario = {
  id: string;
  title: string;
  rawPng: string;
  prePng: string;
  rawAudio: string;
  preAudio: string;
  audioStartSec: number;
  durationSec: number;
  startFrame: number;
  frames: number;
  replayAtFrame: number;
  switchAtFrame: number;
  verdictAtFrame: number;
  verdictTriggerSec: number;
  verdict: Verdict;
};

const ASSET_PREFIX = "spectrograms/";
const AUDIO_LEN_SEC = manifest.audioLenSec ?? 7;
const AUDIO_FRAMES = Math.round(AUDIO_LEN_SEC * manifest.fps);

const clamp = (v: number, lo: number, hi: number): number =>
  v < lo ? lo : v > hi ? hi : v;

const TacticalGrid: React.FC = () => {
  const spacing = 80;
  const verticals: number[] = [];
  for (let x = spacing; x < FRAME_WIDTH; x += spacing) verticals.push(x);
  const horizontals: number[] = [];
  for (let y = spacing; y < FRAME_HEIGHT; y += spacing) horizontals.push(y);
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

const Header: React.FC<{
  scenarioIndex: number;
  totalScenarios: number;
  localFrame: number;
}> = ({ scenarioIndex, totalScenarios, localFrame }) => {
  const opacity = HEAD_FRAMES > 0 ? clamp(localFrame / HEAD_FRAMES, 0, 1) : 1;
  return (
    <div
      style={{
        position: "absolute",
        top: 40,
        left: 0,
        right: 0,
        height: 90,
        opacity,
        fontFamily: MONO_FONT,
        color: HUD_COLOR,
        letterSpacing: 1.5,
      }}
    >
      <div
        style={{
          position: "absolute",
          top: 0,
          left: LEFT_X,
          fontSize: 14,
          color: HUD_DIM,
        }}
      >
        ACOUSTIC CLASSIFIER
      </div>
      <div
        style={{
          position: "absolute",
          top: 22,
          left: LEFT_X,
          fontSize: 28,
          color: HUD_COLOR,
        }}
      >
        SAMPLE #{scenarioIndex + 1}
      </div>
      <div
        style={{
          position: "absolute",
          top: 0,
          right: LEFT_X,
          fontSize: 14,
          color: HUD_DIM,
          textAlign: "right",
        }}
      >
        WINDOW 0.5s, HOP 0.25s
      </div>
      <div
        style={{
          position: "absolute",
          top: 22,
          right: LEFT_X,
          fontSize: 14,
          color: HUD_DIM,
          textAlign: "right",
        }}
      >
        SR 22050Hz, MEL 128 BINS
      </div>
    </div>
  );
};

const TIME_TICKS_SEC = Array.from(
  { length: Math.round(AUDIO_LEN_SEC) + 1 },
  (_, i) => i,
);

const SpectrogramPanel: React.FC<{
  label: string;
  sub: string;
  png: string;
  x: number;
  progress: number;
  showPlayhead: boolean;
  active: boolean;
}> = ({ label, sub, png, x, progress, showPlayhead, active }) => {
  const playheadX = clamp(progress, 0, 1) * PANEL_W;
  const borderColor = active ? HUD_COLOR : HUD_FAINT;
  const labelColor = active ? HUD_COLOR : HUD_DIM;
  const panelOpacity = active ? 1 : 0.32;
  const panelFilter = active ? "none" : "saturate(0.15) brightness(0.85)";
  return (
    <div
      style={{
        position: "absolute",
        top: PANEL_TOP,
        left: x,
        width: PANEL_W,
        height: PANEL_H,
        fontFamily: MONO_FONT,
      }}
    >
      <div
        style={{
          position: "absolute",
          top: -28,
          left: 0,
          fontSize: 14,
          color: labelColor,
          letterSpacing: 2,
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span
          style={{
            width: 8,
            height: 8,
            background: active ? HUD_COLOR : "transparent",
            border: `1px solid ${active ? HUD_COLOR : HUD_DIM}`,
            borderRadius: "50%",
          }}
        />
        {label}
        {!active && (
          <span
            style={{
              marginLeft: 8,
              fontSize: 11,
              letterSpacing: 2,
              color: HUD_DIM,
              border: `1px solid ${HUD_DIM}`,
              padding: "1px 6px",
            }}
          >
            MUTED
          </span>
        )}
      </div>
      <div
        style={{
          position: "absolute",
          top: -28,
          right: 0,
          fontSize: 12,
          color: HUD_DIM,
          letterSpacing: 1,
        }}
      >
        {sub}
      </div>
      <div
        style={{
          position: "absolute",
          inset: 0,
          border: `1px solid ${borderColor}`,
          overflow: "hidden",
          opacity: panelOpacity,
          filter: panelFilter,
        }}
      >
        <Img
          src={staticFile(`${ASSET_PREFIX}${png}`)}
          style={{ width: "100%", height: "100%", display: "block" }}
        />
        {[0.25, 0.5, 0.75].map((p) => (
          <div
            key={p}
            style={{
              position: "absolute",
              left: p * PANEL_W,
              top: 0,
              bottom: 0,
              width: 1,
              background: HUD_FAINT,
            }}
          />
        ))}
        {showPlayhead && (
          <div
            style={{
              position: "absolute",
              left: playheadX - 1,
              top: -2,
              bottom: -2,
              width: 2,
              background: active ? HUD_COLOR : HUD_DIM,
              boxShadow: active ? `0 0 8px ${HUD_COLOR}` : "none",
            }}
          />
        )}
      </div>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: PANEL_H + 4,
          display: "flex",
          justifyContent: "space-between",
          fontSize: 11,
          color: HUD_DIM,
          letterSpacing: 1,
        }}
      >
        {TIME_TICKS_SEC.map((t) => (
          <span key={t}>{t}s</span>
        ))}
      </div>
    </div>
  );
};

const labelDisplay = (key: string): string =>
  key === "missile_launch" ? "MSL" : key.toUpperCase();

const VerdictBlock: React.FC<{
  scenario: Scenario;
  reveal: number;
}> = ({ scenario, reveal }) => {
  const v = scenario.verdict;
  const flash = reveal < 2;
  const baseColor = v.relevant ? THREAT_COLOR : HUD_DIM;
  const color = flash ? FLASH_COLOR : baseColor;
  const peakBars = 30;
  const finalPct = Math.round(v.peakConfidence * 100);
  // Counter rolls up over 12 frames, gauge fills over 18 frames.
  const counterProgress = clamp(reveal / 12, 0, 1);
  const barsProgress = clamp(reveal / 18, 0, 1);
  const peakPct = Math.round(finalPct * counterProgress);
  const peakFilled = Math.round(v.peakConfidence * peakBars * barsProgress);

  const ordered = Object.entries(v.windowCounts).sort((a, b) => b[1] - a[1]);
  // Window breakdown row gets a 1-frame delay so the headline reads first.
  const showWindows = reveal >= 6;

  return (
    <div
      style={{
        position: "absolute",
        top: VERDICT_TOP,
        left: LEFT_X,
        width: FRAME_WIDTH - LEFT_X * 2,
        height: VERDICT_H,
        fontFamily: MONO_FONT,
        border: `1.5px solid ${color}`,
        background: flash ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.55)",
        padding: "14px 22px",
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 18 }}>
          <span style={{ color: HUD_DIM, fontSize: 13, letterSpacing: 2 }}>
            DETECTED
          </span>
          <span
            style={{
              color,
              fontSize: 32,
              letterSpacing: 3,
              fontWeight: 600,
            }}
          >
            {v.labelHuman ? v.labelHuman.toUpperCase() : "—"}
          </span>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ color: HUD_DIM, fontSize: 11, letterSpacing: 2 }}>
            PEAK CONFIDENCE
          </div>
          <div style={{ color, fontSize: 22, letterSpacing: 2 }}>
            {peakPct.toString().padStart(2, "0")}%
          </div>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginTop: 8,
        }}
      >
        <span style={{ color: HUD_DIM, fontSize: 11, letterSpacing: 1, width: 60 }}>
          GAUGE
        </span>
        <div style={{ display: "flex", gap: 2 }}>
          {Array.from({ length: peakBars }).map((_, i) => (
            <div
              key={i}
              style={{
                width: 14,
                height: 10,
                background: i < peakFilled ? color : "transparent",
                border: `1px solid ${i < peakFilled ? color : HUD_FAINT}`,
              }}
            />
          ))}
        </div>
      </div>

      <div
        style={{
          marginTop: 10,
          color: HUD_DIM,
          fontSize: 12,
          letterSpacing: 1,
          display: "flex",
          gap: 18,
          flexWrap: "wrap",
          visibility: showWindows ? "visible" : "hidden",
        }}
      >
        <span>WINDOWS:</span>
        {ordered.map(([k, count]) => (
          <span key={k}>
            <span style={{ color: k === v.label ? baseColor : HUD_DIM }}>
              {labelDisplay(k)}={count}
            </span>
          </span>
        ))}
        <span>—={v.silentWindows}/{v.totalWindows}</span>
        <span style={{ marginLeft: "auto" }}>
          ACTIVE-SHARE {Math.round(v.shareConfidence * 100)}%
        </span>
      </div>
    </div>
  );
};

const PhaseIndicator: React.FC<{ inPassA: boolean; inPassB: boolean }> = ({
  inPassA,
  inPassB,
}) => {
  if (!inPassA && !inPassB) return null;
  const label = inPassA ? "PASS 1/2, RAW MIC INPUT" : "PASS 2/2, DENOISED REPLAY";
  const color = inPassA ? HUD_DIM : HUD_COLOR;
  return (
    <div
      style={{
        position: "absolute",
        top: 100,
        left: 0,
        right: 0,
        textAlign: "center",
        fontFamily: MONO_FONT,
        fontSize: 14,
        letterSpacing: 4,
        color,
      }}
    >
      {label}
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
        bottom: 18,
        transform: "translateX(-50%)",
        fontFamily: MONO_FONT,
        fontSize: 12,
        color: HUD_DIM,
        letterSpacing: 2,
      }}
    >
      T+{t.padStart(5, "0")}s, FRM {String(frame).padStart(4, "0")}
    </div>
  );
};

const ScenarioBlock: React.FC<{
  scenario: Scenario;
  scenarioIndex: number;
  totalScenarios: number;
}> = ({ scenario, scenarioIndex, totalScenarios }) => {
  const localFrame = useCurrentFrame();
  const replayAt = scenario.replayAtFrame ?? scenario.switchAtFrame;
  const inPassA = localFrame >= HEAD_FRAMES && localFrame < replayAt;
  const inPassB = localFrame >= replayAt;

  // Independent playhead per panel. Raw freezes at 100% during pass B; denoised stays at 0 until pass B.
  const rawProgress = clamp((localFrame - HEAD_FRAMES) / AUDIO_FRAMES, 0, 1);
  const preProgress = clamp((localFrame - replayAt) / AUDIO_FRAMES, 0, 1);
  const showPlayhead = localFrame >= HEAD_FRAMES;
  const reveal = localFrame - scenario.verdictAtFrame;

  return (
    <AbsoluteFill>
      <Sequence from={HEAD_FRAMES} durationInFrames={AUDIO_FRAMES}>
        <Audio src={staticFile(`${ASSET_PREFIX}${scenario.rawAudio}`)} />
      </Sequence>
      <Sequence from={replayAt} durationInFrames={AUDIO_FRAMES}>
        <Audio src={staticFile(`${ASSET_PREFIX}${scenario.preAudio}`)} />
      </Sequence>
      <Header
        scenarioIndex={scenarioIndex}
        totalScenarios={totalScenarios}
        localFrame={localFrame}
      />
      <PhaseIndicator inPassA={inPassA} inPassB={inPassB} />
      <SpectrogramPanel
        label="DRONE MICROPHONE INPUT"
        sub=""
        png={scenario.rawPng}
        x={LEFT_X}
        progress={rawProgress}
        showPlayhead={showPlayhead && inPassA}
        active={inPassA}
      />
      <SpectrogramPanel
        label="AFTER ML DENOISE"
        sub="replay"
        png={scenario.prePng}
        x={RIGHT_X}
        progress={preProgress}
        showPlayhead={inPassB}
        active={inPassB}
      />
      {reveal >= 0 && <VerdictBlock scenario={scenario} reveal={reveal} />}
    </AbsoluteFill>
  );
};

export const Scene3: React.FC = () => {
  const frame = useCurrentFrame();
  const scenarios = manifest.scenarios as unknown as Scenario[];
  return (
    <AbsoluteFill style={{ background: "black" }}>
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: FRAME_WIDTH,
          height: FRAME_HEIGHT,
          transform: "scale(1.5)",
          transformOrigin: "top left",
        }}
      >
        <TacticalGrid />
        {scenarios.map((s, i) => (
          <Sequence key={s.id} from={s.startFrame} durationInFrames={s.frames}>
            <ScenarioBlock
              scenario={s}
              scenarioIndex={i}
              totalScenarios={scenarios.length}
            />
          </Sequence>
        ))}
        <FrameCounter frame={frame} />
      </div>
    </AbsoluteFill>
  );
};
