import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StoryIllustration } from "./StoryIllustration";

function renderIllustration(family: string, title: string) {
  const container = document.createElement("div");
  container.innerHTML = renderToStaticMarkup(<StoryIllustration family={family} title={title} />);
  return container;
}

describe("StoryIllustration", () => {
  it("keeps a known story's narrative line drawing without generic decoration", () => {
    const container = renderIllustration("jingwei_fills_sea", "精卫填海");

    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.querySelector(".story-illustration-glyph")).not.toBeNull();
    expect(container.querySelector(".story-illustration-orb")).toBeNull();
    expect(container.querySelector(".story-illustration-ground")).toBeNull();
    expect(container.querySelector(".story-illustration-seal")).toBeNull();
    expect(container.textContent).toContain("精卫填海象征插画");
    expect(container.textContent).toContain("叙事线描");
  });

  it("uses an honest title leaf instead of a generic SVG for an unknown corpus family", () => {
    const container = renderIllustration("c1ws_2a5966cedd819c83c80f452a", "廣成子");
    const leaf = container.querySelector<HTMLElement>('[data-illustration-kind="title-leaf"]');

    expect(container.querySelector("svg")).toBeNull();
    expect(leaf).not.toBeNull();
    expect(container.textContent).toContain("广成子");
    expect(container.textContent).toContain("依据题名排版，非古籍原图");
    expect(container.textContent).toContain("题名叶签 · 非古籍原图");

    const labelledBy = leaf?.getAttribute("aria-labelledby")?.split(" ") ?? [];
    expect(labelledBy).toHaveLength(2);
    expect(labelledBy.every((labelId) => container.querySelector(`[id="${labelId}"]`))).toBe(true);
  });

  it("uses a visible fallback title when a source record has no usable title", () => {
    const container = renderIllustration("c1ws_untitled", "   ");

    expect(container.querySelector("svg")).toBeNull();
    expect(container.textContent).toContain("无题");
  });

  it("keeps a distinct, labelled plate while the z-image white drawing is being prepared", () => {
    const container = document.createElement("div");
    container.innerHTML = renderToStaticMarkup(
      <StoryIllustration family="jingwei_fills_sea" title="精卫填海" isLoading />,
    );

    const plate = container.querySelector<HTMLElement>('[data-illustration-state="loading"]');
    expect(plate).not.toBeNull();
    expect(plate?.getAttribute("aria-busy")).toBe("true");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("正在绘制白描题图");
  });
});
