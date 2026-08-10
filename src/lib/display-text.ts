import OpenCC from "opencc-js/t2cn";

const convertTraditionalToSimplified = OpenCC.Converter({ from: "t", to: "cn" });

/**
 * Convert source text only at the rendering boundary. Callers must keep the
 * original value for source links, hashes, persistence, and API payloads.
 */
export function toSimplifiedDisplay(value: string | null | undefined): string {
  if (!value) return "";
  return convertTraditionalToSimplified(value);
}
