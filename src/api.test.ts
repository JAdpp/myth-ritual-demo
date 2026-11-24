import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  MODEL_REQUEST_TIMEOUT_MS,
  STORY_OFFER_REQUEST_TIMEOUT_MS,
  createConversationTurn,
  createExperienceBrief,
  getHealth,
  getStoryOffers,
  requestNodeSuggestions,
} from "./api";
import type { UserBranchVersion } from "./types";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("branch assistant contract", () => {
  it("sends the free-form assistant message in the branch suggestion request", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      branch: undefined,
      suggestions: [],
      nodeUpdates: [{ nodeId: "world_crack", value: "修改后的节点", rationale: "按用户说明修改" }],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const branch: UserBranchVersion = {
      id: "branch-1",
      version: 1,
      selectedStoryVersionId: "story-1",
      nodes: [],
      preview: "",
      status: "draft",
      etag: '"branch-etag"',
    };

    await requestNodeSuggestions(
      "session-1",
      branch,
      "world_crack",
      [],
      undefined,
      "第一处不是长期任务，是突然转学。",
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/sessions\/session-1\/branches$/);
    expect(init?.method).toBe("PATCH");
    expect(init?.headers).toEqual(expect.objectContaining({ "If-Match": '"branch-etag"' }));
    expect(JSON.parse(String(init?.body))).toEqual(expect.objectContaining({
      action: "suggest",
      branchVersionId: "branch-1",
      nodeId: "world_crack",
      assistantMessage: "第一处不是长期任务，是突然转学。",
    }));
  });

  it("does not expose an English backend error in the Chinese interface", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      code: "branch_version_conflict",
      message: "branchVersionId is stale",
    }), { status: 409, headers: { "Content-Type": "application/json" } })));

    await expect(getHealth()).rejects.toMatchObject({
      message: "画布已经更新，请刷新后再试。",
      code: "branch_version_conflict",
    });
  });
});

describe("write request reliability", () => {
  it("reuses an idempotency key after a connection failure, then rotates it after success", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValue(new Response(JSON.stringify({ id: "brief-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    const submit = () => createExperienceBrief("session-1", {
      mode: "text",
      text: "我接下任务后有些不安。",
    });

    await expect(submit()).rejects.toMatchObject({ code: "API_UNAVAILABLE" });
    await submit();
    await submit();

    const requestHeaders = fetchMock.mock.calls.map(([, init]) => (
      init?.headers as Record<string, string>
    ));
    expect(requestHeaders[0]["Idempotency-Key"]).toBeTruthy();
    expect(requestHeaders[1]["Idempotency-Key"]).toBe(requestHeaders[0]["Idempotency-Key"]);
    expect(requestHeaders[2]["Idempotency-Key"]).not.toBe(requestHeaders[1]["Idempotency-Key"]);
  });
});

describe("story offer timeout budget", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("covers sequential plan and rerank plus a safety margin, without changing other model timeouts", () => {
    expect(STORY_OFFER_REQUEST_TIMEOUT_MS).toBe(60_000 * 2 + 15_000);
    expect(STORY_OFFER_REQUEST_TIMEOUT_MS).toBeGreaterThan(MODEL_REQUEST_TIMEOUT_MS);
    expect(MODEL_REQUEST_TIMEOUT_MS).toBe(90_000);
  });

  function stubAbortableFetch() {
    const scheduled: Array<{ callback: () => void; timeoutMs: number }> = [];
    vi.spyOn(globalThis, "setTimeout").mockImplementation(((callback: TimerHandler, timeoutMs?: number) => {
      if (typeof callback === "function") {
        scheduled.push({ callback: () => { callback(); }, timeoutMs: Number(timeoutMs) });
      }
      return 0;
    }) as unknown as typeof setTimeout);
    vi.spyOn(globalThis, "clearTimeout").mockImplementation(() => undefined);
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        reject(new DOMException("The operation was aborted.", "AbortError"));
      }, { once: true });
    }));
    vi.stubGlobal("fetch", fetchMock);
    return { scheduled, fetchMock };
  }

  it("aborts story offers at the combined budget and keeps the idempotency key for retry", async () => {
    const { scheduled, fetchMock } = stubAbortableFetch();

    const first = getStoryOffers("session-1", ["story-a"], false);
    expect(scheduled[0]?.timeoutMs).toBe(STORY_OFFER_REQUEST_TIMEOUT_MS);
    scheduled[0].callback();
    await expect(first).rejects.toMatchObject({ name: "ApiError", code: "REQUEST_TIMEOUT" });

    const second = getStoryOffers("session-1", ["story-a"], false);
    expect(scheduled[1]?.timeoutMs).toBe(STORY_OFFER_REQUEST_TIMEOUT_MS);
    scheduled[1].callback();
    await expect(second).rejects.toMatchObject({ code: "REQUEST_TIMEOUT" });

    const keys = fetchMock.mock.calls.map(([, init]) => (
      init?.headers as Record<string, string>
    )["Idempotency-Key"]);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });

  it("still aborts conversation turns at the original 90 second model timeout", async () => {
    const { scheduled } = stubAbortableFetch();
    const pending = createConversationTurn("session-1", "安排改了。", []);
    expect(scheduled[0]?.timeoutMs).toBe(MODEL_REQUEST_TIMEOUT_MS);
    scheduled[0].callback();
    await expect(pending).rejects.toMatchObject({ name: "ApiError", code: "REQUEST_TIMEOUT" });
  });
});
