import { Composition } from "remotion";
import { Scene1 } from "./Scene1";
import { Scene2 } from "./Scene2";
import { Scene3 } from "./Scene3";
import spectrogramManifest from "./spectrogramManifest.json";

export const Root: React.FC = () => {
  return (
    <>
      <Composition
        id="Scene1"
        component={Scene1}
        durationInFrames={300}
        fps={30}
        width={1280}
        height={720}
      />
      <Composition
        id="Scene2"
        component={Scene2}
        durationInFrames={300}
        fps={30}
        width={1280}
        height={720}
      />
      <Composition
        id="Scene3"
        component={Scene3}
        durationInFrames={spectrogramManifest.totalFrames}
        fps={spectrogramManifest.fps}
        width={1280}
        height={720}
      />
    </>
  );
};
