import { existsSync, statSync } from "node:fs";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ConversationTurnResponse,
  ExperienceBrief,
  HealthStatus,
  ProvenanceLedger,
  RitualArtifact,
  StoryCandidate,
  StoryCard,
  TheatreScript,
} from "../types";
import { EncounterStage } from "./EncounterStage";
import { ConsentScreen } from "./ConsentScreen";
import { ArtifactView, RitualizationStage } from "./RitualizationStage";
import { SiteHeader, StoryLoom } from "./StoryLoom";

const actEnvironment = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean };
actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;

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
  vi.unstubAllGlobals();
});

function render(element: React.ReactNode) {
  act(() => root.render(element));
}

function buttonNamed(name: string): HTMLButtonElement {
  const button = [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(name));
  if (!(button instanceof HTMLButtonElement)) throw new Error(`Missing button: ${name}`);
  return button;
}

function brief(overrides: Partial<ExperienceBrief> = {}): ExperienceBrief {
  return {
    id: "brief-1",
    version: 1,
    inputMode: "preset",
    presetId: "new-beginning",
    neutralSummary: "一次普通生活变化。",
    confirmed: false,
    safetyRoute: "preset_only",
    ...overrides,
  };
}

const noop = async () => undefined;

function encounterProps(overrides: Partial<React.ComponentProps<typeof EncounterStage>> = {}) {
  return {
    allowPrivateText: true,
    brief: null,
    offer: null,
    busy: false,
    error: null,
    notice: null,
    rejectedAll: false,
    offersExhausted: false,
    onCreateBrief: noop,
    onConfirmBrief: noop,
    onRetryOffers: noop,
    onRefresh: noop,
    onRejectAll: noop,
    onSelect: noop,
    onExit: noop,
    ...overrides,
  } satisfies React.ComponentProps<typeof EncounterStage>;
}

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
  possibleResonance: "普通生活变化",
  mayNotFit: "不想触及变化时可换卡",
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
    prohibitedChanges: ["冒充原典"],
    references: [{ id: "ref-1", title: "测试古籍", locator: "卷一" }],
  },
};

const script: TheatreScript = {
  id: "theatre-v1",
  version: 1,
  branchVersionId: "branch-v2",
  title: "测试三幕剧场",
  acts: [
    {
      id: "act-1",
      title: "第一幕",
      narration: "第一幕旁白",
      stageDirection: "灯光亮起",
      durationSeconds: 25,
      mood: "threshold",
    },
  ],
  totalDurationSeconds: 25,
  finalLineSuggestions: ["留下一点可能。"],
};

describe("front-end recovery and safety states", () => {
  it("shows a dedicated crisis stop with support and only a delete-and-exit action", async () => {
    const onExit = vi.fn(async () => undefined);
    render(
      <EncounterStage
        {...encounterProps({
          brief: brief({
            inputMode: "text",
            neutralSummary: "",
            safetyRoute: {
              blocked: true,
              route: "crisis_stop",
              categories: ["self_harm"],
              support: {
                chinaMentalHealthHotline: "12356",
                emergency: ["110", "120"],
                realTimeMonitoring: false,
              },
            },
          }),
          onExit,
        })}
      />,
    );

    expect(container.textContent).toContain("不把危机写成故事");
    expect(container.querySelector('a[href="tel:12356"]')).not.toBeNull();
    expect(container.querySelector('a[href="tel:110"]')).not.toBeNull();
    expect(container.textContent).not.toContain("看看故事");

    await act(async () => buttonNamed("删除本次会话并退出").click());
    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it("keeps a confirmed brief recoverable when story offers fail", async () => {
    const onRetryOffers = vi.fn(async () => undefined);
    render(
      <EncounterStage
        {...encounterProps({
          brief: brief({ confirmed: true, safetyRoute: "standard" }),
          error: "故事服务暂时不可用",
          onRetryOffers,
        })}
      />,
    );

    expect(container.textContent).toContain("摘要已确认，故事卡还没有取到");
    await act(async () => buttonNamed("重新取得故事卡").click());
    expect(onRetryOffers).toHaveBeenCalledTimes(1);
  });

  it("announces the corpus boundary instead of offering another refresh", () => {
    render(
      <EncounterStage
        {...encounterProps({
          brief: brief({ confirmed: true }),
          rejectedAll: true,
          offersExhausted: true,
        })}
      />,
    );

    expect(container.textContent).toContain("本次可浏览的故事已经看完");
    expect([...container.querySelectorAll("button")].some((item) => item.textContent?.includes("再看一批"))).toBe(false);
  });
});

describe("landing and conversational encounter", () => {
  it("renders a static Chinese hero heading without rotating category fragments", () => {
    render(<ConsentScreen busy={false} error={null} health={null} onBegin={noop} />);

    const heading = container.querySelector<HTMLHeadingElement>("#welcome-title");
    expect(heading?.textContent).toContain("中国古典神话传说");
    expect(heading?.textContent).toContain("在你的此刻生出一条新支线");
    expect(container.querySelector(".hero-word-slot")).toBeNull();
    expect(container.querySelector(".hero-rotating-word")).toBeNull();
  });

  it("keeps the static hero intact when reduced motion is requested", () => {
    const originalMatchMedia = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({
        matches: true,
        media: "(prefers-reduced-motion: reduce)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    try {
      render(<ConsentScreen busy={false} error={null} health={null} onBegin={noop} />);
      expect(container.querySelector("#welcome-title")?.textContent).toContain("中国古典神话传说");
      expect(container.querySelector(".hero-rotating-word")).toBeNull();
    } finally {
      if (typeof originalMatchMedia === "function") {
        Object.defineProperty(window, "matchMedia", { configurable: true, value: originalMatchMedia });
      } else {
        Reflect.deleteProperty(window, "matchMedia");
      }
    }
  });

  it("shows a real five-act lianhuanhua example instead of an empty theatre", () => {
    render(<ConsentScreen busy={false} error={null} health={null} onBegin={noop} />);

    const image = container.querySelector<HTMLImageElement>(".theatre-spread-image");
    expect(image?.getAttribute("src")).toBe("/assets/landing/theatre-dayu-five-act-v1.webp");
    expect(image?.getAttribute("alt")).toContain("五幕连环画示例");
    const assetPath = "public/assets/landing/theatre-dayu-five-act-v1.webp";
    expect(existsSync(assetPath)).toBe(true);
    expect(statSync(assetPath).size).toBeGreaterThan(100_000);
    expect(container.querySelectorAll(".theatre-act-strip li")).toHaveLength(5);
    expect(container.querySelector('.theatre-act-strip [aria-current="step"]')?.textContent).toContain("沿山寻水");
    expect(container.textContent).toContain("示例连环画 · AI 生成");
    expect(container.textContent).toContain("实际体验会根据你确认的支线生成 4–7 幕画面与旁白");
    expect(container.querySelector(".theatre-curtain")).toBeNull();
  });

  it("keeps consent in an entry-triggered modal and renders the live corpus overview", async () => {
    const onBegin = vi.fn(async () => undefined);
    const health: HealthStatus = {
      status: "ok",
      service: "myth-ritual-demo",
      corpusVersion: "corpus-v-test",
      eligibleC3Records: 30,
      corpusOverview: {
        recommendationPoolStories: 12_353,
        curatedExperienceStories: 30,
        productStories: 30,
        sourceWitnesses: 60,
        storyFamilies: 30,
        storyVersions: 30,
        catalogEntries: 12_353,
        catalogUniqueStories: 12_353,
        catalogRawSegments: 12_374,
        catalogRejectedSegments: 20,
        catalogFullTextStories: 12_353,
        catalogSourceWorks: 6,
        catalogDedupeScope: "来源定位与精确文本哈希；近似异文待复核",
        catalogWorks: [
          { sourceWorkId: "taiping_guangji", title: "《太平广记》", count: 6_995 },
          { sourceWorkId: "yijianzhi", title: "《夷坚志》", count: 2_646 },
          { sourceWorkId: "yuewei", title: "《阅微草堂笔记》", count: 1_198 },
          { sourceWorkId: "zibuyu", title: "《子不语》", count: 745 },
          { sourceWorkId: "xu_zibuyu", title: "《续子不语》", count: 277 },
          { sourceWorkId: "liaozhai_zhiyi", title: "《聊斋志异》", count: 492 },
        ],
        catalogSnapshotDate: "2026-08-09",
        familyIds: ["gun_yu_flood_control", "change_flight_to_moon"],
        genres: [{ name: "神话", count: 30 }],
        storyTypes: Array.from({ length: 8 }, (_, index) => ({ name: `类型${index + 1}`, count: 3 })),
        catalogGroups: Array.from({ length: 12 }, (_, index) => ({ name: `分区${index + 1}`, count: 2 })),
        eraLabels: ["先秦"],
        reviewStatus: "development_dual_review_pending",
        productionEligibleVersions: 0,
      },
      model: "deepseek-v4-flash",
      liveModelEnabled: false,
    };
    render(<ConsentScreen busy={false} error={null} health={health} onBegin={onBegin} />);

    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(container.textContent).toContain("梦蝶记");
    expect(container.textContent).not.toContain("梦蝶录");
    expect(container.textContent).toContain("12,353 条可推荐候选");
    expect(container.textContent).toContain("目前汇集 6 部古籍");
    expect(container.textContent).toContain("《太平广记》6,995 条");
    expect(container.textContent).toContain("《夷坚志》2,646 条");
    expect(container.textContent).toContain("《阅微草堂笔记》1,198 条");
    expect(container.textContent).toContain("《子不语》745 条");
    expect(container.textContent).toContain("《续子不语》277 条");
    expect(container.textContent).toContain("《聊斋志异》492 条");
    expect(container.textContent).toContain("不等同于同等数量的独立故事");
    expect(container.textContent).toContain("栖蝶沿着你分享的那件事整理时间、人物与转折");
    expect(container.querySelector(".assistant-role-list")).toBeNull();
    expect(container.textContent).toContain("栖蝶——梦蝶记中的故事向导。");
    expect(container.textContent?.indexOf("栖蝶")).toBe(container.textContent?.indexOf("栖蝶——梦蝶记中的故事向导。"));
    expect(container.querySelector("#case-showcase")).not.toBeNull();
    expect(container.querySelector("#how-it-works")).not.toBeNull();
    expect(container.querySelector("#technology")).not.toBeNull();
    expect(container.textContent).not.toContain("30 则重点整理主文本");
    expect(container.querySelectorAll(".corpus-story-card")).toHaveLength(12);
    expect(container.querySelectorAll(".corpus-story-card img")).toHaveLength(12);
    expect(container.textContent).not.toContain("z-image");
    expect(container.textContent).not.toContain("DeepSeek");
    expect(container.textContent).not.toContain("SQLite");
    expect(container.textContent).not.toContain("BM25");
    expect(container.textContent).not.toContain("不想继续？");
    expect(container.textContent).not.toContain("已备讲解");
    expect(container.textContent).not.toContain("预制");
    expect(container.textContent).not.toContain("60条后台来源见证");
    expect(container.textContent).not.toContain("个固定来源版本");
    expect(container.textContent).not.toContain("故事家族");
    expect(container.textContent).toContain("大禹治水");

    act(() => buttonNamed("计划忽然落空").click());
    expect(container.textContent).toContain("塞翁失马");
    expect(container.textContent).toContain("采用《淮南子·人间训》");

    act(() => buttonNamed("进入体验").click());
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    expect(container.textContent).toContain("请先了解这次体验");
    expect(container.textContent).not.toContain("确认三条边界");
    const checkboxes = [...container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
    expect(checkboxes).toHaveLength(1);
    act(() => checkboxes[0].click());
    await act(async () => buttonNamed("确认并进入相遇").click());
    expect(onBegin).toHaveBeenCalledTimes(1);
    expect(onBegin).toHaveBeenCalledWith({
      adultConfirmed: true,
      nonClinicalAcknowledged: true,
      adultContentOptIn: true,
      cloudProcessingAccepted: true,
    });
  });

  it("bundles the required disclosures into one explicit consent", async () => {
    const onBegin = vi.fn(async () => undefined);
    render(<ConsentScreen busy={false} error={null} health={null} onBegin={onBegin} />);

    act(() => buttonNamed("进入体验").click());
    const checkboxes = [...container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
    expect(checkboxes).toHaveLength(1);
    expect(container.textContent).toContain("深度求索处理");
    expect(container.textContent).toContain("阿里云处理");
    expect(container.textContent).toContain("旁白目前由你的设备朗读");
    expect(container.textContent).toContain("这不代表第三方服务的留存期限");
    expect(container.textContent).toContain("敏感材料");
    expect(container.textContent).toContain("最迟二十四小时自动清除");
    expect(container.textContent).toContain("结束页可立即删除");
    act(() => checkboxes[0].click());
    await act(async () => buttonNamed("确认并进入相遇").click());

    expect(onBegin).toHaveBeenCalledWith(expect.objectContaining({
      adultConfirmed: true,
      nonClinicalAcknowledged: true,
      adultContentOptIn: true,
      cloudProcessingAccepted: true,
    }));
  });

  it("turns a starter into a chat turn and produces editable shared notes", async () => {
    const onCreateBrief = vi.fn(async () => brief({
      inputMode: "text",
      neutralSummary: "你提到：离开熟悉环境后，正在重新寻找自己的步调。",
      safetyRoute: "standard",
    }));
    render(<EncounterStage {...encounterProps({ onCreateBrief })} />);

    act(() => buttonNamed("事情发生在").click());
    await act(async () => buttonNamed("发送").click());
    expect(onCreateBrief).toHaveBeenCalledTimes(1);
    expect(container.querySelector(".chat-acknowledgement")?.textContent).toContain("这件事的起点我听见了");
    expect(container.querySelector(".chat-follow-up-question")?.textContent).toContain("后来最先发生了什么");
    expect(container.textContent).toContain("栖蝶听到的线索");
    expect([...container.querySelectorAll("button")].some((button) => button.textContent?.includes("原本……，后来……"))).toBe(false);
  });

  it("uses the 栖蝶 avatar with an accessible name and a text fallback", () => {
    render(<EncounterStage {...encounterProps()} />);

    expect(container.textContent).toContain("请讲一件最近真实发生、你愿意分享的小事");
    expect(container.textContent).toContain("和栖蝶聊一段");
    expect(container.textContent).not.toContain("智能助手");
    const avatars = [...container.querySelectorAll<HTMLImageElement>('img[src="/assets/qidie-guide-avatar-chibi-v1.webp"]')];
    expect(avatars.length).toBeGreaterThanOrEqual(2);
    expect(avatars.every((avatar) => avatar.alt === "栖蝶头像")).toBe(true);

    act(() => avatars[0].dispatchEvent(new Event("error", { bubbles: true })));
    const fallback = container.querySelector('[role="img"][aria-label="栖蝶头像"]');
    expect(fallback?.textContent).toBe("栖蝶");
  });

  it("replaces static sentence starters with returned story-specific follow-up options", async () => {
    const onCreateBrief = vi.fn(async () => brief({
      inputMode: "text",
      neutralSummary: "你讲到一件临时改变安排的小事。",
      safetyRoute: "standard",
    }));
    const onConversationReply = vi.fn(async () => ({
      phase: "encounter" as const,
      reply: "你提到临时改变了原来的安排。\n\n那一刻，是什么让你决定换一种做法？",
      acknowledgement: "你提到临时改变了原来的安排。",
      followUpQuestion: "那一刻，是什么让你决定换一种做法？",
      source: "model_adapter" as const,
      followUpOptions: ["沿着“沿着“临时改变安排”：先说转折”：后来我最先做了……", "让我改变想法的是……", "当时我最舍不得的是……"],
    }));
    render(<EncounterStage {...encounterProps({ onCreateBrief, onConversationReply })} />);

    expect(container.querySelector('[aria-label="讲述这件事的句式起点"]')).not.toBeNull();
    act(() => buttonNamed("原本……，后来……").click());
    await act(async () => buttonNamed("发送").click());

    expect(container.querySelector('[aria-label="讲述这件事的句式起点"]')).toBeNull();
    const followUps = container.querySelector('[aria-label="栖蝶根据这件事给出的继续讲述方向"]');
    expect(followUps?.textContent).toContain("沿着“临时改变安排”：后来我最先做了……");
    expect(followUps?.textContent).not.toContain("沿着“沿着");
    expect(followUps?.textContent).toContain("让我改变想法的是……");
    expect(container.querySelector(".chat-acknowledgement")?.textContent).toBe("你提到临时改变了原来的安排。");
    expect(container.querySelector(".chat-follow-up-question")?.textContent).toBe("那一刻，是什么让你决定换一种做法？");
    expect(container.querySelector(".chat-acknowledgement")).not.toBe(container.querySelector(".chat-follow-up-question"));

    act(() => buttonNamed("让我改变想法的是……").click());
    expect(container.querySelector<HTMLTextAreaElement>("#story-chat-input")?.value).toBe("让我改变想法的是……");
  });

  it("keeps one conversation request locked until the delayed assistant response finishes", async () => {
    let resolveReply!: (response: ConversationTurnResponse) => void;
    const onCreateBrief = vi.fn(async () => brief({
      inputMode: "text",
      neutralSummary: "你讲到临时改变了安排。",
      safetyRoute: "standard",
    }));
    const onConversationReply = vi.fn(() => new Promise<ConversationTurnResponse>((resolve) => {
      resolveReply = resolve;
    }));
    render(<EncounterStage {...encounterProps({
      brief: brief({ inputMode: "text", safetyRoute: "standard" }),
      onCreateBrief,
      onConversationReply,
    })} />);

    act(() => buttonNamed("原本……，后来……").click());
    const form = container.querySelector<HTMLFormElement>(".chat-composer");
    act(() => buttonNamed("发送").click());
    await act(async () => { await Promise.resolve(); });

    expect(container.querySelector(".chat-transcript")?.getAttribute("aria-busy")).toBe("true");
    expect(container.querySelector(".chat-thinking")?.textContent).toContain("正在回应");
    expect(container.querySelector<HTMLTextAreaElement>("#story-chat-input")?.disabled).toBe(true);
    expect(buttonNamed("等待栖蝶回应").disabled).toBe(true);

    act(() => form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(onCreateBrief).toHaveBeenCalledTimes(1);
    expect(onConversationReply).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveReply({
        phase: "encounter",
        reply: "这些线索已经够用了。",
        acknowledgement: "这些线索已经够用了。",
        source: "model_adapter",
        guidanceComplete: true,
        summary: "你在安排突然改变后，重新衡量了当时最在意的事。",
        turnsUsed: 4,
        turnBudget: 4,
      });
      await Promise.resolve();
    });

    expect(container.querySelector(".chat-transcript")?.getAttribute("aria-busy")).toBe("false");
    expect(container.querySelector(".chat-thinking")).toBeNull();
    expect(container.querySelector(".conversation-closed")?.textContent).toContain("这份摘要可以直接改");
    expect(container.textContent).not.toContain("右边的摘要");
  });

  it("describes recommendation retrieval as unreviewed development material", () => {
    render(<EncounterStage {...encounterProps({
      brief: brief({ confirmed: true, safetyRoute: "standard" }),
      busy: true,
    })} />);

    expect(container.textContent).toContain("机器整理、未人工复核的开发语料");
    expect(container.textContent).not.toContain("已审核语料");
    expect(container.textContent).not.toContain("核对出处与权利");
  });

  it("shows traditional assistant copy as simplified text without changing the response shape", async () => {
    const onCreateBrief = vi.fn(async () => brief({
      inputMode: "text",
      neutralSummary: "你讲到一件临时改变安排的小事。",
      safetyRoute: "standard",
    }));
    const onConversationReply = vi.fn(async () => ({
      phase: "encounter" as const,
      reply: "你提到臨時改變了原來的安排。\n\n那一刻，是什麼讓你決定換一種做法？",
      acknowledgement: "你提到臨時改變了原來的安排。",
      followUpQuestion: "那一刻，是什麼讓你決定換一種做法？",
      source: "model_adapter" as const,
    }));
    render(<EncounterStage {...encounterProps({ onCreateBrief, onConversationReply })} />);

    act(() => buttonNamed("事情发生在").click());
    await act(async () => buttonNamed("发送").click());

    expect(container.querySelector(".chat-acknowledgement")?.textContent).toBe("你提到临时改变了原来的安排。");
    expect(container.querySelector(".chat-follow-up-question")?.textContent).toBe("那一刻，是什么让你决定换一种做法？");
    expect(container.textContent).not.toContain("臨時");
    expect(container.textContent).not.toContain("什麼");
  });

  it("presents one familiar story and keeps its adopted source in the detail evidence", () => {
    const recommendedStory: StoryCard = {
      ...story,
      title: "大禹治水",
      subtitle: "《吴越春秋》 · 东汉",
      experienceMode: "prepared",
      recommendationReason: "它把长期压力与改变应对方式放在一起，适合拿来比较。",
      explanation: {
        overview: story.summary,
        plotBeats: [story.summary],
        sourceVersion: "《吴越春秋》 · 东汉",
        editorialStatus: "开发运行集",
      },
      sourceCanon: {
        ...story.sourceCanon,
        sourceTitle: "《吴越春秋》",
        references: [{
          id: "ref-yue",
          title: "《吴越春秋》固定修订",
          locator: "越王无余外传",
          url: "https://zh.wikisource.org/wiki/吴越春秋",
          rights: "CC BY-SA 4.0 transcription; underlying ancient text public-domain-believed",
        }],
      },
    };
    render(
      <EncounterStage
        {...encounterProps({
          brief: brief({ confirmed: true, safetyRoute: "standard" }),
          offer: { id: "offer-1", corpusVersion: "corpus-test", cards: [recommendedStory] },
        })}
      />,
    );

    expect(container.textContent).toContain("大禹治水");
    expect(container.textContent).toContain("推荐理由");
    const sourceDetails = container.querySelector<HTMLDetailsElement>(".story-card-source");
    expect(sourceDetails?.open).toBe(false);
    expect(sourceDetails?.querySelector("summary")?.textContent).toContain("查看梗概与出处");
    expect(sourceDetails?.textContent).toContain("测试梗概");
    expect(sourceDetails?.querySelector(".motif-chips")?.textContent).toContain("选择");
    expect(sourceDetails?.textContent).toContain("《吴越春秋》 · 东汉");
    expect(sourceDetails?.textContent).toContain("来源边界");
    expect(sourceDetails?.textContent).toContain("机器切分的开发候选");
    expect(sourceDetails?.textContent).toContain("知识共享署名—相同方式共享 4.0");
    expect(sourceDetails?.textContent).toContain("可更新页面");
    expect(sourceDetails?.textContent).not.toContain("CC BY-SA");
    expect(sourceDetails?.textContent).not.toContain("固定修订");
    expect(container.textContent).not.toContain("选择版本");

    act(() => container.querySelector<HTMLInputElement>('input[name="story"]')?.click());
    act(() => buttonNamed("查看选中故事").click());

    expect(container.textContent).toContain("查看原文出处与说明");
    expect(container.textContent).toContain("《吴越春秋》");
    expect(container.textContent).toContain("依据原文整理");
    expect(container.textContent).not.toContain("已备讲解");
    expect(container.textContent).not.toContain("异文选择");
  });

  it("uses one radio per card while the drawing, title, and recommendation all select it", () => {
    const secondStory: StoryCard = {
      ...story,
      storyVersionId: "story-v2",
      storyFamilyId: "story-two",
      title: "第二则故事",
      recommendationReason: "它提供了另一条可以对照的行动线索。",
      sourceCanon: {
        ...story.sourceCanon,
        storyVersionId: "story-v2",
        title: "第二则故事",
      },
    };
    render(<EncounterStage {...encounterProps({
      brief: brief({ confirmed: true, safetyRoute: "standard" }),
      offer: { id: "offer-radio-cards", corpusVersion: "corpus-test", cards: [story, secondStory] },
    })} />);

    const cards = [...container.querySelectorAll<HTMLElement>(".illustrated-story-card")];
    const radios = [...container.querySelectorAll<HTMLInputElement>('input[type="radio"][name="story"]')];
    const cta = buttonNamed("查看选中故事");
    expect(cards).toHaveLength(2);
    expect(radios).toHaveLength(2);
    expect(container.querySelector(".story-card-hit")).toBeNull();
    expect(container.querySelectorAll('button[aria-label^="选择《"]')).toHaveLength(0);
    expect(cta.disabled).toBe(true);
    expect(radios[0].getAttribute("aria-labelledby")).toBe("story-choice-story-v1-title");
    expect(radios[0].getAttribute("aria-describedby")).toContain("story-choice-story-v1-reason");

    act(() => cards[0].querySelector<HTMLElement>(".story-illustration")?.click());
    expect(radios[0].checked).toBe(true);
    expect(cta.disabled).toBe(false);
    expect(cards[0].querySelector(".story-card-selected-mark")?.textContent).toBe("已选");

    act(() => cards[1].querySelector<HTMLElement>("h3")?.click());
    expect(radios[1].checked).toBe(true);
    expect(radios[0].checked).toBe(false);

    act(() => cards[0].querySelector<HTMLElement>(".recommendation-reason p")?.click());
    expect(radios[0].checked).toBe(true);
    expect(radios[1].checked).toBe(false);

    const secondDetails = cards[1].querySelector<HTMLDetailsElement>("details");
    act(() => cards[1].querySelector<HTMLElement>("summary")?.click());
    expect(secondDetails?.open).toBe(true);
    expect(radios[0].checked).toBe(true);
    expect(radios[1].checked).toBe(false);

    act(() => radios[0].focus());
    expect(document.activeElement).toBe(radios[0]);
  });

  it("loads a new offer's white-drawing covers in sequence without cancelling itself", async () => {
    let activeRequests = 0;
    let maximumConcurrentRequests = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      activeRequests += 1;
      maximumConcurrentRequests = Math.max(maximumConcurrentRequests, activeRequests);
      await new Promise((resolve) => setTimeout(resolve, 8));
      activeRequests -= 1;
      const storyVersionId = decodeURIComponent(
        String(input).split("/stories/")[1]?.split("/cover")[0] ?? "unknown",
      );
      return new Response(JSON.stringify({
        storyVersionId,
        status: "ready",
        imageUrl: `https://example.invalid/${storyVersionId}.png`,
        altText: `${storyVersionId}白描题图`,
        message: null,
        retryable: true,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const cards = ["one", "two", "three"].map((suffix) => ({
      ...story,
      storyVersionId: `story-${suffix}`,
      storyFamilyId: `family-${suffix}`,
      title: `测试故事${suffix}`,
      sourceCanon: {
        ...story.sourceCanon,
        storyVersionId: `story-${suffix}`,
        title: `测试故事${suffix}`,
      },
    }));

    render(
      <EncounterStage
        {...encounterProps({
          sessionId: "session-cover-sequence",
          brief: brief({ confirmed: true, safetyRoute: "standard" }),
          offer: { id: "offer-cover-sequence", corpusVersion: "corpus-test", cards },
        })}
      />,
    );

    expect(container.querySelectorAll('[data-illustration-state="loading"]')).toHaveLength(3);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 80)); });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(maximumConcurrentRequests).toBe(1);
    expect(container.querySelectorAll('[data-illustration-state="ready"]')).toHaveLength(3);
    expect(container.querySelectorAll(".story-illustration-generated img")).toHaveLength(3);
  });

  it("keeps a lightweight source-library story selectable without exposing its preparation mode", async () => {
    const onSelect = vi.fn(async (_card: StoryCard) => undefined);
    const candidate: StoryCandidate = {
      storyVersionId: "c1ws-wolf-adviser",
      storyFamilyId: "c1ws-wolf-adviser",
      title: "狼军师",
      summary: "群狼请来一只异兽设法取人，局势最终被赶来的樵夫打断。",
      recommendationReason: "它把受困与寻找外援放在同一条情节线上，可与你提到的处境作比较。",
      experienceMode: "generated",
      sourceCanon: {
        sourceTitle: "《续子不语》",
        excerpt: "有钱某者，赴市归晚，行山麓间。",
        references: [{ id: "ref-wolf", title: "《续子不语》", locator: "卷一 · 狼军师" }],
      },
    };

    render(
      <EncounterStage
        {...encounterProps({
          brief: brief({ confirmed: true, safetyRoute: "standard" }),
          offer: { id: "offer-c1", corpusVersion: "c1-source-library", cards: [candidate] },
          onSelect,
        })}
      />,
    );

    expect(container.textContent).toContain("狼军师");
    expect(container.querySelector(".story-card-origin")?.textContent).toBe("原典出处《续子不语》");
    expect(container.querySelector(".story-card-source")?.textContent).toContain("卷一 · 狼军师");
    expect(container.textContent).toContain("推荐理由");
    expect(container.textContent).not.toContain("选择后自动整理");
    expect(container.textContent).not.toContain("已备讲解");

    act(() => container.querySelector<HTMLInputElement>('input[name="story"]')?.click());
    act(() => buttonNamed("查看选中故事").click());
    expect(container.textContent).toContain("依据原文整理");
    expect(container.textContent).toContain("确认这则故事，进入共谱");

    await act(async () => buttonNamed("确认这则故事，进入共谱").click());
    expect(onSelect).toHaveBeenCalledTimes(1);
    const selected = onSelect.mock.calls[0][0];
    expect(selected.characters).toEqual([]);
    expect(selected.sourceCanon.originalEnding).toContain("尚未拆出单独结局");
    expect(selected.experienceMode).toBe("on_demand");
  });

  it("labels an explicitly consented live-model conversation reply", async () => {
    const onCreateBrief = vi.fn(async () => brief({
      inputMode: "text",
      neutralSummary: "你提到：正在适应新环境。",
      safetyRoute: "standard",
    }));
    const onConversationReply = vi.fn(async () => ({
      phase: "encounter" as const,
      reply: "你正在比較舊節奏與新環境；哪一部分最想先保留？",
      acknowledgement: "你正在比較舊節奏與新環境。",
      source: "deepseek" as const,
      modelVersion: "deepseek-v4-flash",
    }));
    render(<EncounterStage {...encounterProps({
      allowPrivateText: true,
      onCreateBrief,
      onConversationReply,
    })} />);

    act(() => buttonNamed("当时我最在意的是").click());
    await act(async () => buttonNamed("发送").click());
    expect(onConversationReply).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("栖蝶");
    expect(container.textContent).not.toContain("智能助手");
    expect(container.textContent).not.toContain("DeepSeek");
    expect(container.textContent).toContain("正在比较旧节奏与新环境");
    expect(container.textContent).not.toContain("正在比較舊節奏與新環境");
    expect(container.textContent).toContain("哪一部分最想先保留");
    expect(container.querySelectorAll(".chat-assistant-turn")).toHaveLength(0);
  });
});

describe("accessible state semantics", () => {
  it("exposes the conversational composer and the current loom step", () => {
    render(<EncounterStage {...encounterProps()} />);
    expect(container.querySelector('[role="log"]')?.textContent).toContain("栖蝶");
    const composer = container.querySelector("#story-chat-input");
    expect(composer).toBeInstanceOf(HTMLTextAreaElement);
    expect(container.querySelector('label[for="story-chat-input"]')?.textContent).toBe("回复栖蝶");
    expect(container.textContent).toContain("栖蝶会先回应你刚说的");

    render(<StoryLoom stage="articulation" />);
    expect(container.querySelector('[aria-current="step"]')?.textContent).toContain("共谱");
    expect(container.textContent).not.toContain("原文不改，你的版本由你决定");

    render(<SiteHeader stage="encounter" />);
    expect(container.textContent).not.toContain("不是诊疗，可随时删除本次内容");
  });

  it("uses progressbar semantics and tolerates a null provenance source", () => {
    render(
      <RitualizationStage
        story={story}
        script={script}
        busy={false}
        error={null}
        notice={null}
        onComplete={noop}
      />,
    );
    const progress = container.querySelector('[role="progressbar"]');
    expect(progress?.getAttribute("aria-valuemin")).toBe("0");
    expect(progress?.getAttribute("aria-valuemax")).toBe("100");
    expect(progress?.getAttribute("aria-valuenow")).toBe("0");

    const artifact: RitualArtifact = {
      id: "artifact-1",
      title: "现代支线",
      finalLine: "留下一点可能。",
      ritualGesture: "light",
      saved: false,
      createdAt: "2026-08-07T00:00:00Z",
    };
    const ledger: ProvenanceLedger = {
      sessionId: "session-1",
      corpusVersion: "corpus-test",
      sourceCanon: null,
      entries: [],
    };
    render(
      <ArtifactView
        artifact={artifact}
        ledger={ledger}
        busy={false}
        error={null}
        onDelete={noop}
      />,
    );
    expect(container.textContent).toContain("暂时无法显示原典条目");
  });

  it("renders provenance node labels in Chinese instead of internal identifiers", () => {
    const artifact: RitualArtifact = {
      id: "artifact-2",
      title: "现代支线",
      finalLine: "留下一点可能。",
      ritualGesture: "light",
      saved: true,
      createdAt: "2026-08-07T00:00:00Z",
    };
    const ledger: ProvenanceLedger = {
      sessionId: "session-2",
      corpusVersion: "corpus-test",
      sourceCanon: null,
      entries: [
        { id: "source", segment: "source_canon", text: "原文", origin: "source_canon" },
        { id: "branch", segment: "world_crack", text: "变化", origin: "model_expression" },
      ],
    };

    render(
      <ArtifactView
        artifact={artifact}
        ledger={ledger}
        busy={false}
        error={null}
        onDelete={noop}
      />,
    );

    expect(container.textContent).toContain("原典摘录");
    expect(container.textContent).toContain("原来的世界与裂缝");
    expect(container.textContent).not.toContain("source_canon");
    expect(container.textContent).not.toContain("world_crack");
  });
});
