import { describe, expect, it } from "vitest";

import { toSimplifiedDisplay } from "./display-text";

describe("繁体古籍的简体显示层", () => {
  it("转换常见古籍题名与卷次文字", () => {
    expect(toSimplifiedDisplay("太平廣記・夷堅志・閱微草堂筆記・聊齋志異・子不語・續子不語"))
      .toBe("太平广记・夷坚志・阅微草堂笔记・聊斋志异・子不语・续子不语");
    expect(toSimplifiedDisplay("卷第一・異聞錄"))
      .toBe("卷第一・异闻录");
  });

  it("不改写空值", () => {
    expect(toSimplifiedDisplay(undefined)).toBe("");
    expect(toSimplifiedDisplay(null)).toBe("");
  });
});
