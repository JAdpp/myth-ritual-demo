import { afterEach, describe, expect, it, vi } from "vitest";

import { getHealth, requestNodeSuggestions } from "./api";
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
