import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const componentSource = readFileSync(
  resolve(process.cwd(), "src/components/RitualizationStage.tsx"),
  "utf8",
);
const mediaStyles = readFileSync(resolve(process.cwd(), "src/ritual-media.css"), "utf8");

describe("third-stage media interaction contract", () => {
  it("keeps speech narration explicit and exposes play, pause, resume, and stop paths", () => {
    expect(componentSource).toContain("new SpeechSynthesisUtterance");
    expect(componentSource).toContain("window.speechSynthesis.pause()");
    expect(componentSource).toContain("window.speechSynthesis.resume()");
    expect(componentSource).toContain("window.speechSynthesis.cancel()");
    expect(componentSource).toContain("播放本幕旁白");
    expect(componentSource).toContain("停止旁白");
  });

  it("no longer ships ambient audio or local recording", () => {
    expect(componentSource).not.toContain("createAmbientEngine");
    expect(componentSource).not.toContain("navigator.mediaDevices.getUserMedia");
    expect(componentSource).not.toContain("new MediaRecorder");
    expect(componentSource).not.toContain("环境音");
    expect(componentSource).not.toContain("允许麦克风并开始录音");
  });

  it("narrates through the server-side CosyVoice route and auto-plays per act", () => {
    expect(componentSource).toContain("createActNarration");
    // The narration request must go through the API client, never straight
    // to a provider from the browser.
    expect(componentSource).not.toContain("dashscope");
    expect(componentSource).toContain("narratedActRef");
    // Opening the curtain starts the performance.
    const curtain = componentSource.slice(
      componentSource.indexOf("function toggleCurtain()"),
      componentSource.indexOf("useEffect", componentSource.indexOf("function toggleCurtain()")),
    );
    expect(curtain).toContain("setPlaying(true)");
  });

  it("makes the curtain a stateful gate and preserves accessibility and reduced motion", () => {
    expect(componentSource).toContain("curtain-is-open");
    expect(componentSource).toContain("curtain-is-closed");
    expect(componentSource).toContain("拉开幕布");
    expect(componentSource).toContain("合上幕布");
    expect(componentSource).toContain('role="progressbar"');
    expect(componentSource).toContain('aria-live="polite"');
    expect(mediaStyles).toContain("@media (prefers-reduced-motion: reduce)");
    expect(mediaStyles).toContain("animation: none");
  });

  it("generates a separate scene for every dynamic act and keeps a retryable paper-shadow fallback", () => {
    expect(componentSource).toContain("createTheatreSceneImage(sessionId, script.id, targetAct.id)");
    expect(componentSource).toContain("acts.filter((item) => item.id !== act.id)");
    expect(componentSource).toContain("正在绘制第");
    expect(componentSource).toContain("重试本幕画面");
    expect(componentSource).toContain("纸影舞台已接替显示");
    expect(componentSource).not.toContain("boundedActIndex(actIndex, 3)");
    expect(componentSource).not.toContain("actIndex % 5");
    expect(componentSource).toContain("act.sourceNodeIds");
    expect(componentSource).toContain("act.sceneTitle");
    expect(componentSource).toContain("act.mood");
    expect(componentSource).toContain('data-scene-motif={motif}');
    expect(componentSource).not.toContain("THREE-ACT THEATRE");
    expect(componentSource).not.toContain("看完三幕");
    expect(mediaStyles).toContain(".generated-scene-image");
    expect(mediaStyles).toContain(".scene-image-status");
  });

  it("converts every act-facing text boundary to simplified Chinese", () => {
    expect(componentSource).toContain("toSimplifiedDisplay(act.sceneTitle ?? act.title)");
    expect(componentSource).toContain("toSimplifiedDisplay(act.narration)");
    expect(componentSource).toContain("toSimplifiedDisplay(displayedDialogue)");
    expect(componentSource).toContain("toSimplifiedDisplay(act.stageDirection)");
    expect(componentSource).toContain("return toSimplifiedDisplay(");
    expect(componentSource).not.toContain("RITUALIZATION / 剧场再演");
    expect(componentSource).not.toContain("YOUR FINAL ACTION");
    expect(componentSource).not.toContain(">REC<");
  });

  it("removes repeated stage, finale, and source kickers while retaining the artifact category", () => {
    expect(componentSource).not.toContain('section-code">再演');
    expect(componentSource).not.toContain('section-code">终幕动作');
    expect(componentSource).not.toContain('section-code">来源记录');
    expect(componentSource).toContain('section-code">当代改编');
  });
});
