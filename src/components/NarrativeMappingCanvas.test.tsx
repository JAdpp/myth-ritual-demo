import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeEmptyBranchNodes } from "../lib/story";
import type { BranchNodeDraft, StoryCard, UserBranchVersion } from "../types";
import { ArticulationStage } from "./ArticulationStage";
import { NarrativeMappingCanvas, deriveSourceBeats } from "./NarrativeMappingCanvas";

const actEnvironment = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean };
actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;

const story: StoryCard = {
  storyVersionId: "yugong-v1",
  storyFamilyId: "yugong",
  title: "大禹治水",
  summary: "洪水漫溢，禹承接治水使命，以疏导水势回应灾患。",
  characters: ["禹", "众人"],
  conflict: "洪水长期危害生活，堵塞的办法未能解决问题。",
  motifs: ["疏导", "协作", "长期行动"],
  imagery: ["洪水", "河道"],
  emotionalArc: "受困—尝试—疏通",
  possibleResonance: "面对长期、复杂而不能一次解决的处境",
  mayNotFit: "不想把处境理解为攻坚时可以换卡",
  contentWarnings: [],
  sourceCanon: {
    storyVersionId: "yugong-v1",
    title: "大禹治水",
    sourceTitle: "《山海经·海内经》",
    excerpt: "洪水滔天。鲧窃帝之息壤以堙洪水。",
    originalEnding: "禹继续治水，疏导洪流并划定九州。",
    motifs: ["洪水", "疏导", "重建秩序"],
    mustKeep: ["治水并非一蹴而就"],
    allowedTransformations: ["将洪水转化为长期困境"],
    prohibitedChanges: ["把用户经历说成原典事实"],
    references: [{ id: "ref-1", title: "山海经", locator: "海内经" }],
  },
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function buttonNamed(name: string): HTMLButtonElement {
  const button = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(name));
  if (!(button instanceof HTMLButtonElement)) throw new Error(`Missing button: ${name}`);
  return button;
}

function typeInto(textarea: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  setter?.call(textarea, value);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("source-backed narrative mapping", () => {
  it("derives each left-lane beat from actual story and source_canon fields", () => {
    const beats = deriveSourceBeats(story);
    expect(beats).toHaveLength(5);
    expect(beats.map((beat) => beat.content)).toEqual([
      story.sourceCanon.excerpt,
      story.conflict,
      story.characters.join("、"),
      story.sourceCanon.originalEnding,
      story.sourceCanon.motifs.join(" · "),
    ]);
  });

  it("keeps source nodes read-only while exposing user-branch editing and gap state", async () => {
    const nodes = makeEmptyBranchNodes().map((node, index) => index === 1
      ? { ...node, value: "我准备先尝试一件小事。" }
      : node);
    const onChange = vi.fn();
    const onRequestSuggestion = vi.fn();
    const onMoveContent = vi.fn();
    const onChooseSuggestion = vi.fn();

    act(() => root.render(
      <NarrativeMappingCanvas
        story={story}
        nodes={nodes}
        activeNodeId="world_crack"
        disabled={false}
        suggestingNode={null}
        onActivate={vi.fn()}
        onChange={onChange}
        onMoveContent={onMoveContent}
        onRequestSuggestion={onRequestSuggestion}
        onChooseSuggestion={onChooseSuggestion}
      />,
    ));

    const sourceNodes = container.querySelectorAll('[aria-label$="原典只读节点"]');
    expect(sourceNodes).toHaveLength(5);
    expect([...sourceNodes].every((node) => node.querySelector("textarea") === null)).toBe(true);
    expect(container.textContent).toContain("洪水长期危害生活");
    expect(container.querySelectorAll(".mapping-state--gap")).toHaveLength(4);
    expect(container.querySelector(".mapping-state--mapped")).toBeNull();
    expect(container.querySelector(".mapping-source-node__lock")).toBeNull();
    expect(container.textContent).not.toContain("已映照");

    await act(async () => buttonNamed("标为不适用").click());
    expect(onChange).toHaveBeenCalledWith("world_crack", { skipped: true });
  });
});

describe("semi-structured mapping assistant", () => {
  it("sends free-form corrections and applies returned updates only after preview", async () => {
    const nodes = makeEmptyBranchNodes();
    const branch: UserBranchVersion = {
      id: "branch-1",
      version: 1,
      selectedStoryVersionId: story.storyVersionId,
      nodes,
      preview: "",
      status: "draft",
    };
    const onSuggest = vi.fn(async () => ({
      nodeId: "world_crack" as const,
      suggestions: [],
      nodeUpdates: [{
        nodeId: "world_crack" as const,
        value: "我面对的是一次突然转学，而不是长期任务。",
        rationale: "按用户补充改正变化类型",
      }],
    }));

    act(() => root.render(
      <ArticulationStage
        story={story}
        branch={branch}
        busy={false}
        error={null}
        notice={null}
        onSuggest={onSuggest}
        onSave={vi.fn(async () => undefined)}
        onApprove={vi.fn(async () => undefined)}
      />,
    ));

    expect(container.textContent).toContain("画布已按对话自动生成");
    expect(container.textContent).toContain("告诉栖蝶想改哪一处");
    expect(container.textContent).toContain("由你决定是否应用");
    expect(container.querySelector(".mapping-assistant__mode")).toBeNull();
    expect(container.textContent).not.toContain("自由对话模型尚未接入");
    expect(container.querySelector<HTMLImageElement>('.mapping-assistant__avatar')?.src).toContain("/assets/qidie-guide-avatar-chibi-v1.webp");
    expect(container.querySelector<HTMLImageElement>('.mapping-message--assistant .mapping-message__avatar')?.src).toContain("/assets/qidie-guide-avatar-chibi-v1.webp");
    expect(container.querySelector('.mapping-message--user .mapping-message__avatar')).toBeNull();

    const composer = container.querySelector<HTMLTextAreaElement>("#mapping-chat-input");
    expect(composer).toBeInstanceOf(HTMLTextAreaElement);
    act(() => typeInto(composer!, "第一处不是长期任务，是突然转学。"));
    await act(async () => {
      buttonNamed("发送").click();
      await Promise.resolve();
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    expect(onSuggest).toHaveBeenCalledWith(
      "world_crack",
      expect.any(Array),
      undefined,
      "第一处不是长期任务，是突然转学。",
    );
    expect(container.textContent).toContain("按用户补充改正变化类型");
    expect(container.textContent).toContain("待确认的修改");

    act(() => buttonNamed("应用到画布").click());
    expect(container.querySelector<HTMLTextAreaElement>('#mapping-node-world_crack textarea')?.value)
      .toBe("我面对的是一次突然转学，而不是长期任务。");
  });

  it("preserves approval readiness with two written nodes, explicit skips and a hope anchor", async () => {
    const nodes: BranchNodeDraft[] = makeEmptyBranchNodes().map((node, index) => ({
      ...node,
      value: index < 2 ? `用户叙事 ${index + 1}` : "",
      skipped: index >= 2,
    }));
    const branch: UserBranchVersion = {
      id: "branch-ready",
      version: 2,
      selectedStoryVersionId: story.storyVersionId,
      nodes,
      hopeAnchor: { type: "open", detail: "先保留一种可能。" },
      preview: "用户叙事",
      status: "draft",
    };
    const onApprove = vi.fn(async () => undefined);

    act(() => root.render(
      <ArticulationStage
        story={story}
        branch={branch}
        busy={false}
        error={null}
        notice={null}
        onSuggest={vi.fn()}
        onSave={vi.fn(async () => undefined)}
        onApprove={onApprove}
      />,
    ));

    const approve = buttonNamed("确认共谱并进入再演");
    expect(approve.disabled).toBe(false);
    await act(async () => approve.click());
    expect(onApprove).toHaveBeenCalledWith(nodes, branch.hopeAnchor);
  });
});
