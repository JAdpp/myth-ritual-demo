import { describe, expect, it } from "vitest";

import {
  boundedActIndex,
  branchReadyForApproval,
  buildBranchPreview,
  formatSourceReference,
  makeEmptyBranchNodes,
} from "./story";

describe("story branch rules", () => {
  it("keeps skipped nodes out of the preview and appends the hope anchor", () => {
    const nodes = makeEmptyBranchNodes();
    nodes[0].value = "主角发现熟悉的道路突然中断。";
    nodes[1].skipped = true;
    nodes[2].value = "一盏旧灯提醒她可以慢一点。";

    expect(buildBranchPreview(nodes, { type: "open", detail: "路还没有走完。" })).toBe(
      "主角发现熟悉的道路突然中断。\n\n一盏旧灯提醒她可以慢一点。\n\n路还没有走完。",
    );
  });

  it("requires every node to be addressed, two written nodes, and a hope anchor", () => {
    const nodes = makeEmptyBranchNodes();
    nodes.forEach((node) => {
      node.skipped = true;
    });
    nodes[0].skipped = false;
    nodes[0].value = "第一段";
    expect(branchReadyForApproval(nodes, { type: "action", detail: "走一步" })).toBe(false);

    nodes[1].skipped = false;
    nodes[1].value = "第二段";
    expect(branchReadyForApproval(nodes, { type: "action", detail: "走一步" })).toBe(true);
  });

  it("formats source locations without inventing absent fields", () => {
    expect(
      formatSourceReference({
        id: "src-1",
        title: "山海经",
        era: "先秦",
        edition: "公开校本",
        locator: "卷三",
      }),
    ).toBe("先秦，山海经，公开校本，卷三");
  });

  it("bounds theatre act navigation", () => {
    expect(boundedActIndex(-1, 3)).toBe(0);
    expect(boundedActIndex(8, 3)).toBe(2);
    expect(boundedActIndex(1, 3)).toBe(1);
  });
});

