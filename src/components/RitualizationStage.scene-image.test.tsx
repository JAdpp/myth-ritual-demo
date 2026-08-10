import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StoryCard, TheatreScript } from "../types";

const apiMocks = vi.hoisted(() => ({
  createTheatreSceneImage: vi.fn(),
}));

vi.mock("../api", () => ({
  createTheatreSceneImage: apiMocks.createTheatreSceneImage,
}));

import { RitualizationStage } from "./RitualizationStage";

const actEnvironment = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean };
actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  apiMocks.createTheatreSceneImage.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const story: StoryCard = {
  storyVersionId: "story-v1",
  storyFamilyId: "story",
  title: "精衛填海",
  summary: "测试梗概",
  characters: ["主角"],
  conflict: "变化",
  motifs: ["选择"],
  imagery: ["灯"],
  emotionalArc: "变化—回应",
  possibleResonance: "普通生活变化",
  mayNotFit: "不想触及变化时可换卡",
  contentWarnings: [],
  sourceCanon: {
    storyVersionId: "story-v1",
    title: "精衛填海",
    sourceTitle: "山海經",
    excerpt: "原典摘句",
    originalEnding: "原典结局",
    motifs: ["选择"],
    mustKeep: ["原结局"],
    allowedTransformations: ["场景"],
    prohibitedChanges: ["冒充原典"],
    references: [{ id: "ref-1", title: "山海經", locator: "北山經" }],
  },
};

const script: TheatreScript = {
  id: "theatre-v1",
  version: 1,
  branchVersionId: "branch-v2",
  title: "六幕再演",
  acts: Array.from({ length: 6 }, (_, index) => ({
    id: `act-${index + 1}`,
    title: index === 0 ? "序幕 · 轉身" : `第${index + 1}幕`,
    narration: index === 0 ? "燈火在遠山旁亮起。" : `第${index + 1}幕旁白`,
    stageDirection: index === 0 ? "幕布緩緩拉開。" : `第${index + 1}幕调度`,
    durationSeconds: 20,
    mood: index === 0 ? "opening" : "turning",
  })),
  totalDurationSeconds: 120,
  finalLineSuggestions: ["留下一点可能。"],
};

const semanticScript: TheatreScript = {
  ...script,
  id: "theatre-semantic",
  title: "七幕再演",
  acts: [
    { id: "semantic-1", title: "灯起", sceneTitle: "远山微明", narration: "开场", stageDirection: "灯起", durationSeconds: 20, mood: "opening" },
    { id: "semantic-2", title: "变化", sceneTitle: "原来的生活", narration: "裂缝", stageDirection: "山影错开", durationSeconds: 20, mood: "opening", sourceNodeIds: ["world_crack"] },
    { id: "semantic-3", title: "一步", sceneTitle: "向前", narration: "跨过", stageDirection: "门影出现", durationSeconds: 20, mood: "turning", sourceNodeIds: ["cross_threshold"] },
    { id: "semantic-4", title: "有人同行", sceneTitle: "并肩", narration: "相助", stageDirection: "两道纸影靠近", durationSeconds: 20, mood: "return", sourceNodeIds: ["allies_resources"] },
    { id: "semantic-5", title: "再看一次", sceneTitle: "换个方向", narration: "转向", stageDirection: "月影移过", durationSeconds: 20, mood: "opening", sourceNodeIds: ["new_understanding"] },
    { id: "semantic-6", title: "带在身边", sceneTitle: "仍向前走", narration: "回返", stageDirection: "水与桥相接", durationSeconds: 20, mood: "turning", sourceNodeIds: ["bring_back"] },
    { id: "semantic-7", title: "最后一幕", sceneTitle: "尾声 · 灯火收束", narration: "余音", stageDirection: "灯光收束", durationSeconds: 20, mood: "opening" },
  ],
  totalDurationSeconds: 140,
};

async function flushSceneQueue() {
  await act(async () => {
    for (let index = 0; index < 30; index += 1) await Promise.resolve();
  });
}

describe("RitualizationStage per-act generated scenes", () => {
  it("queues all six acts, converts display text, and retries one failed act", async () => {
    const attempts = new Map<string, number>();
    apiMocks.createTheatreSceneImage.mockImplementation(
      async (_sessionId: string, _scriptId: string, actId: string) => {
        const attempt = (attempts.get(actId) ?? 0) + 1;
        attempts.set(actId, attempt);
        if (actId === "act-2" && attempt === 1) {
          return {
            actId,
            status: "fallback" as const,
            imageUrl: null,
            altText: "第二幕纸影舞台",
            message: "本幕画面暂未生成，可继续观看或重试。",
            retryable: true,
          };
        }
        return {
          actId,
          status: "ready" as const,
          imageUrl: `https://example.invalid/${actId}-${attempt}.png`,
          altText: `${actId}的中式舞台画面`,
          message: null,
          retryable: true,
        };
      },
    );

    await act(async () => {
      root.render(
        <RitualizationStage
          sessionId="session-1"
          story={story}
          script={script}
          busy={false}
          error={null}
          notice={null}
          onComplete={async () => undefined}
        />,
      );
    });
    await flushSceneQueue();

    expect(apiMocks.createTheatreSceneImage).toHaveBeenCalledTimes(6);
    expect(new Set(apiMocks.createTheatreSceneImage.mock.calls.map((call) => call[2]))).toEqual(
      new Set(script.acts.map((item) => item.id)),
    );
    expect(container.textContent).toContain("序幕 · 转身");
    expect(container.textContent).toContain("灯火在远山旁亮起");
    expect(container.textContent).not.toContain("幕布緩緩拉開");

    const secondActButton = container.querySelectorAll<HTMLButtonElement>(".act-switcher button")[1];
    act(() => secondActButton.click());
    expect(container.textContent).toContain("本幕画面暂未生成");

    const retry = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent?.includes("重试本幕画面"));
    expect(retry).toBeInstanceOf(HTMLButtonElement);
    await act(async () => retry?.click());
    await flushSceneQueue();

    expect(attempts.get("act-2")).toBe(2);
    expect(container.querySelector<HTMLImageElement>('.generated-scene-image')?.src)
      .toContain("act-2-2.png");
  });

  it("chooses seven local motifs from node, title, and mood semantics instead of act position", async () => {
    await act(async () => {
      root.render(
        <RitualizationStage
          story={story}
          script={semanticScript}
          busy={false}
          error={null}
          notice={null}
          onComplete={async () => undefined}
        />,
      );
    });
    await flushSceneQueue();

    const expectedMotifs = ["opening", "crack", "threshold", "allies", "turning", "return", "closing"];
    const observedMotifs: string[] = [];
    const actButtons = container.querySelectorAll<HTMLButtonElement>(".act-switcher button");

    for (let index = 0; index < actButtons.length; index += 1) {
      if (index > 0) act(() => actButtons[index].click());
      observedMotifs.push(container.querySelector<HTMLElement>("[data-scene-motif]")?.dataset.sceneMotif ?? "");
    }

    expect(observedMotifs).toEqual(expectedMotifs);
    expect(new Set(observedMotifs).size).toBe(7);
    expect(observedMotifs.slice(-2)).toEqual(["return", "closing"]);
    expect(apiMocks.createTheatreSceneImage).not.toHaveBeenCalled();
  });
});
