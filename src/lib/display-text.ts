import OpenCC from "opencc-js/t2cn";

const convertTraditionalToSimplified = OpenCC.Converter({ from: "t", to: "cn" });

/**
 * Convert source text only at the rendering boundary. Callers must keep the
 * original value for source links, hashes, persistence, and API payloads.
 */
export function toSimplifiedDisplay(value: string | null | undefined): string {
  if (!value) return "";
  return convertTraditionalToSimplified(value)
    .replace(/《\s*《+/g, "《")
    .replace(/》+\s*》/g, "》")
    .replace(/([。！？；，、：])\1+/g, "$1")
    .replace(/([！？])。/g, "$1")
    .replace(/；。/g, "。")
    .replace(/\s+([，。；：！？、》])/g, "$1")
    .replace(/《\s+/g, "《");
}
