import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  BranchNodeDraft,
  ExperienceBrief,
  ExperienceSession,
  HopeAnchor,
  SessionConsent,
  StoryCandidate,
  StoryCard,
  StoryOffer,
  TheatreScript,
  UserBranchVersion,
} from "./types";

const apiMocks = vi.hoisted(() => ({
  approveBranch: vi.fn(),
  completeRitual: vi.fn(),
  confirmExperienceBrief: vi.fn(),
  createBranch: vi.fn(),
  createConversationTurn: vi.fn(),
  createExperienceBrief: vi.fn(),
  createSession: vi.fn(),
  createTheatreScript: vi.fn(),
  deleteSession: vi.fn(),
  getProvenance: vi.fn(),
  getHealth: vi.fn(),
  getStoryOffers: vi.fn(),
  requestNodeSuggestions: vi.fn(),
  saveBranch: vi.fn(),
  updateStorySelection: vi.fn(),
}));

vi.mock("./api", () => ({
  ApiError: class ApiError extends Error {},
  ...apiMocks,
}));

vi.mock("./components/ConsentScreen", () => ({
  ConsentScreen: ({ onBegin }: { onBegin: (consent: SessionConsent) => Promise<void> }) => (
    <main id="main">
      <h1>Welcome mock</h1>
      <button type="button" onClick={() => void onBegin({
        adultConfirmed: true,
        adultContentOptIn: false,
        nonClinicalAcknowledged: true,
        cloudProcessingAccepted: false,
      })}>begin</button>
    </main>
  ),
}));

vi.mock("./components/EncounterStage", () => ({
  EncounterStage: ({
    offer,
    onCreateBrief,
    onConfirmBrief,
    onSelect,
  }: {
    offer: StoryOffer | null;
    onCreateBrief: (input: { mode: "preset"; presetId: string }) => Promise<void>;
    onConfirmBrief: (summary: string) => Promise<void>;
    onSelect: (story: StoryCandidate) => Promise<void>;
  }) => (
    <main id="main">
      <h1>Encounter mock</h1>
      <button type="button" onClick={() => void onCreateBrief({ mode: "preset", presetId: "new-beginning" })}>create brief</button>
      <button type="button" onClick={() => void onConfirmBrief("confirmed summary")}>confirm brief</button>
      <button type="button" disabled={!offer} onClick={() => offer && void onSelect(offer.cards[0])}>select story</button>
    </main>
  ),
}));

vi.mock("./components/ArticulationStage", () => ({
  ArticulationStage: ({
    story,
    branch,
    error,
    onApprove,
  }: {
    story: StoryCard;
    branch: UserBranchVersion;
    error: string | null;
    onApprove: (nodes: BranchNodeDraft[], hopeAnchor: HopeAnchor) => Promise<void>;
  }) => (
    <main id="main">
      <h1>Articulation mock</h1>
      <span data-testid="selected-story">{story.title} · {story.experienceMode}</span>
      <span data-testid="branch-status">{branch.status}</span>
      {error && <p>{error}</p>}
      <button type="button" onClick={() => void onApprove(
        branch.nodes,
        branch.hopeAnchor ?? { type: "open", detail: "keep one possibility" },
      )}>approve and open theatre</button>
    </main>
  ),
}));

vi.mock("./components/RitualizationStage", () => ({
  RitualizationStage: ({
    error,
    notice,
    onComplete,
  }: {
    error: string | null;
    notice: string | null;
    onComplete: (payload: { title: string; finalLine: string; gesture: "light"; save: boolean }) => Promise<void>;
  }) => <main id="main">
    <h1>Ritualization mock</h1><p>theatre opened</p>
    {error && <p>{error}</p>}{notice && <p>{notice}</p>}
    <button type="button" onClick={() => void onComplete({
      title: "测试纪念卡",
      finalLine: "这是确认后的末句。",
      gesture: "light",
      save: false,
    })}>complete ritual</button>
  </main>,
  ArtifactView: () => <main id="main"><h1>Artifact mock</h1></main>,
}));

vi.mock("./components/StoryLoom", () => ({
  SiteHeader: () => null,
  StoryLoom: () => null,
}));

import { App } from "./App";

const actEnvironment = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean };
actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;

const nodes: BranchNodeDraft[] = [
  "world_crack",
  "cross_threshold",
  "allies_resources",
  "new_understanding",
  "bring_back",
].map((id, index) => ({
  id: id as BranchNodeDraft["id"],
  title: id,
  prompt: id,
  value: index < 2 ? `user text ${index}` : "",
  skipped: index >= 2,
  suggestions: [],
  expressionOrigin: "user",
}));

const hopeAnchor: HopeAnchor = { type: "open", detail: "keep one possibility" };

const story: StoryCard = {
  storyVersionId: "story-v1",
  storyFamilyId: "story",
  title: "测试故事",
  summary: "测试梗概",
  characters: ["主角"],
  conflict: "变化",
  motifs: ["选择"],
  imagery: ["灯"],
  emotionalArc: "变化—回应",
  possibleResonance: "普通变化",
  mayNotFit: "可换卡",
  contentWarnings: [],
  sourceCanon: {
    storyVersionId: "story-v1",
    title: "测试故事",
    sourceTitle: "测试古籍",
    excerpt: "原典摘句",
    originalEnding: "原典结局",
    motifs: ["选择"],
    mustKeep: ["原结局"],
    allowedTransformations: ["场景"],
    prohibitedChanges: [],
    references: [{ id: "ref-1", title: "测试古籍", locator: "卷一" }],
  },
};

const offer: StoryOffer = {
  id: "offer-1",
  cards: [story],
  corpusVersion: "corpus-test",
  exhausted: false,
};

const branchV1: UserBranchVersion = {
  id: "branch-v1",
  version: 1,
  selectedStoryVersionId: story.storyVersionId,
  nodes,
  hopeAnchor,
  preview: "preview",
  status: "draft",
  etag: '"etag-1"',
};

const branchV2: UserBranchVersion = {
  ...branchV1,
  id: "branch-v2",
  version: 2,
  parentVersionId: branchV1.id,
  etag: '"etag-2"',
};

const approvedBranch: UserBranchVersion = { ...branchV2, status: "approved" };

const theatre: TheatreScript = {
  id: "theatre-v1",
  version: 1,
  branchVersionId: approvedBranch.id,
  title: "测试剧场",
  acts: [{
    id: "act-1",
    title: "第一幕",
    narration: "旁白",
    stageDirection: "灯光亮起",
    durationSeconds: 25,
  }],
  totalDurationSeconds: 25,
  finalLineSuggestions: ["留下一点可能。"],
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.resetAllMocks();
  const session: ExperienceSession = {
    id: "session-1",
    status: "active",
    corpusVersion: "corpus-test",
  };
  const draftBrief: ExperienceBrief = {
    id: "brief-1",
    version: 1,
    inputMode: "preset",
    presetId: "new-beginning",
    neutralSummary: "draft summary",
    confirmed: false,
    safetyRoute: "preset_only",
  };

  apiMocks.createSession.mockResolvedValue(session);
  apiMocks.createConversationTurn.mockResolvedValue({
    phase: "encounter",
    reply: "继续说一说这次变化。",
    source: "deterministic_fallback",
    modelVersion: null,
  });
  apiMocks.getHealth.mockResolvedValue({
    status: "ok",
    service: "myth-ritual-demo",
    corpusVersion: "corpus-test",
    eligibleC3Records: 12,
    corpusOverview: {
      productStories: 12,
      sourceWitnesses: 24,
      storyFamilies: 12,
      storyVersions: 12,
      familyIds: [],
      genres: [],
      eraLabels: [],
      reviewStatus: "development_dual_review_pending",
      productionEligibleVersions: 0,
    },
    liveModelEnabled: false,
  });
  apiMocks.createExperienceBrief.mockResolvedValue(draftBrief);
  apiMocks.confirmExperienceBrief.mockResolvedValue({ ...draftBrief, version: 2, confirmed: true });
  apiMocks.getStoryOffers.mockResolvedValue(offer);
  apiMocks.updateStorySelection.mockResolvedValue({ status: "selected", selectedStory: story });
  apiMocks.createBranch.mockResolvedValue(branchV1);
  apiMocks.saveBranch.mockResolvedValue(branchV2);
  apiMocks.approveBranch.mockResolvedValue(approvedBranch);
  apiMocks.createTheatreScript
    .mockRejectedValueOnce(new Error("temporary theatre failure"))
    .mockResolvedValue(theatre);

  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  act(() => root.render(<App />));
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

async function click(name: string) {
  await act(async () => {
    buttonNamed(name).click();
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe("App partial-failure recovery", () => {
  it("uses the story details returned after selecting a lightweight candidate", async () => {
    const lightweight: StoryCandidate = {
      storyVersionId: story.storyVersionId,
      storyFamilyId: story.storyFamilyId,
      title: "原题名",
      recommendationReason: "与用户摘要中的转折相呼应。",
      experienceMode: "generated",
      sourceCanon: { sourceTitle: "《测试古籍》" },
    };
    apiMocks.getStoryOffers.mockResolvedValue({ ...offer, cards: [lightweight] });
    apiMocks.updateStorySelection.mockResolvedValue({
      status: "selected",
      selectedStory: {
        ...story,
        title: "原题名",
        experienceMode: "generated",
        explanation: {
          overview: story.summary,
          plotBeats: [story.summary, story.sourceCanon.originalEnding],
          sourceVersion: "《测试古籍》",
          editorialStatus: "自动整理",
        },
      },
    });

    await click("begin");
    await click("create brief");
    await click("confirm brief");
    await click("select story");

    expect(container.querySelector('[data-testid="selected-story"]')?.textContent).toBe("原题名 · on_demand");
    expect(apiMocks.createBranch).toHaveBeenCalledWith("session-1", story.storyVersionId, []);
  });

  it("retries theatre compilation from the approved branch without saving or approving again", async () => {
    await click("begin");
    expect(document.activeElement?.textContent).toBe("Encounter mock");
    await click("create brief");
    await click("confirm brief");
    await click("select story");

    await click("approve and open theatre");
    expect(container.querySelector('[data-testid="branch-status"]')?.textContent).toBe("approved");
    expect(container.textContent).toContain("共谱已确认，但再演暂时没有打开");
    expect(apiMocks.saveBranch).toHaveBeenCalledTimes(1);
    expect(apiMocks.approveBranch).toHaveBeenCalledTimes(1);
    expect(apiMocks.createTheatreScript).toHaveBeenCalledTimes(1);

    await click("approve and open theatre");
    expect(container.textContent).toContain("theatre opened");
    expect(apiMocks.saveBranch).toHaveBeenCalledTimes(1);
    expect(apiMocks.approveBranch).toHaveBeenCalledTimes(1);
    expect(apiMocks.createTheatreScript).toHaveBeenCalledTimes(2);
  });

  it("retries provenance after completion without creating a second ritual artifact", async () => {
    apiMocks.createTheatreScript.mockReset().mockResolvedValue(theatre);
    apiMocks.completeRitual.mockResolvedValue({
      id: "artifact-1",
      title: "测试纪念卡",
      finalLine: "这是确认后的末句。",
      ritualGesture: "light",
      saved: false,
      createdAt: "2026-08-27T00:00:00Z",
    });
    apiMocks.getProvenance
      .mockRejectedValueOnce(new Error("temporary provenance failure"))
      .mockResolvedValue({
        sessionId: "session-1",
        corpusVersion: "corpus-test",
        entries: [],
      });

    await click("begin");
    await click("create brief");
    await click("confirm brief");
    await click("select story");
    await click("approve and open theatre");
    await click("complete ritual");

    expect(container.textContent).toContain("终幕已经完成，但来源记录暂时没有载入");
    expect(apiMocks.completeRitual).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProvenance).toHaveBeenCalledTimes(1);

    await click("complete ritual");
    expect(container.textContent).toContain("Artifact mock");
    expect(apiMocks.completeRitual).toHaveBeenCalledTimes(1);
    expect(apiMocks.getProvenance).toHaveBeenCalledTimes(2);
  });
});
