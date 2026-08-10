import type {
  BranchNodeDraft,
  BranchNodeId,
  HopeAnchor,
  SourceReference,
  StoryCandidate,
  StoryCard,
} from "../types";

export const BRANCH_NODE_DEFINITIONS: ReadonlyArray<{
  id: BranchNodeId;
  title: string;
  prompt: string;
  shortLabel: string;
}> = [
  {
    id: "world_crack",
    title: "原来的世界与裂缝",
    prompt: "什么发生了变化？主角现在面对什么？",
    shortLabel: "裂缝",
  },
  {
    id: "cross_threshold",
    title: "跨过门槛",
    prompt: "主角愿意尝试、拒绝或暂缓什么？",
    shortLabel: "门槛",
  },
  {
    id: "allies_resources",
    title: "考验、盟友与资源",
    prompt: "谁、什么经验或文化意象能够提供帮助？",
    shortLabel: "同行",
  },
  {
    id: "new_understanding",
    title: "新的理解或行动方式",
    prompt: "主角如何重新理解或处理处境？",
    shortLabel: "转向",
  },
  {
    id: "bring_back",
    title: "带着什么回来",
    prompt: "故事希望保留什么可能性？",
    shortLabel: "回返",
  },
] as const;

export const HOPE_ANCHORS: ReadonlyArray<{
  type: HopeAnchor["type"];
  label: string;
  description: string;
}> = [
  { type: "action", label: "一个下一步", description: "困难仍在，但主角看见一件可以尝试的小事。" },
  { type: "relationship", label: "一段可依靠的关系", description: "同行、求助，或重新划定一条边界。" },
  { type: "meaning", label: "一种新的理解", description: "把这段经历放进你自己的说法里，损失照实留着。" },
  { type: "open", label: "一扇尚未关上的门", description: "答案可以留到以后，门先开着。" },
] as const;

export function makeEmptyBranchNodes(): BranchNodeDraft[] {
  return BRANCH_NODE_DEFINITIONS.map((definition) => ({
    id: definition.id,
    title: definition.title,
    prompt: definition.prompt,
    value: "",
    skipped: false,
    suggestions: [],
    expressionOrigin: "user",
  }));
}

export function buildBranchPreview(nodes: BranchNodeDraft[], hopeAnchor?: HopeAnchor): string {
  const passages = nodes
    .filter((node) => !node.skipped && node.value.trim().length > 0)
    .map((node) => node.value.trim());

  if (hopeAnchor?.detail.trim()) passages.push(hopeAnchor.detail.trim());
  return passages.join("\n\n");
}

export function branchReadyForApproval(
  nodes: BranchNodeDraft[],
  hopeAnchor?: HopeAnchor,
): boolean {
  const addressedNodes = nodes.filter((node) => node.skipped || node.value.trim().length > 0);
  const writtenNodes = nodes.filter((node) => !node.skipped && node.value.trim().length > 0);
  return (
    addressedNodes.length === nodes.length &&
    writtenNodes.length >= 2 &&
    Boolean(hopeAnchor?.type && hopeAnchor.detail.trim())
  );
}

export function formatSourceReference(reference: SourceReference): string {
  const creator = [reference.era, reference.author].filter(Boolean).join(" · ");
  const edition = reference.edition ? `，${reference.edition}` : "";
  const locator = reference.locator ? `，${reference.locator}` : "";
  return `${creator ? `${creator}，` : ""}${reference.title}${edition}${locator}`;
}

const ON_DEMAND_MODES = new Set(["on_demand", "generated", "source_only", "lightweight"]);
const PREPARED_MODES = new Set(["prepared", "curated", "deep"]);

export function storyNeedsOnDemandPreparation(candidate: StoryCandidate): boolean {
  if (candidate.experienceMode && ON_DEMAND_MODES.has(candidate.experienceMode)) return true;
  if (candidate.experienceMode && PREPARED_MODES.has(candidate.experienceMode)) return false;
  return !(
    candidate.explanation?.plotBeats?.length
    && candidate.sourceCanon?.excerpt?.trim()
    && candidate.sourceCanon?.originalEnding?.trim()
  );
}

export function storyRecommendationText(candidate: StoryCandidate): string {
  return candidate.recommendationReason?.trim()
    || candidate.possibleResonance?.trim()
    || "它与摘要中提到的变化或选择形成了可以比较的情节线索。";
}

export function storySourceLabel(candidate: StoryCandidate): string {
  return candidate.sourceCanon?.sourceTitle?.trim()
    || candidate.sourceCanon?.references?.[0]?.workTitle?.trim()
    || candidate.sourceCanon?.references?.[0]?.title?.trim()
    || candidate.subtitle?.trim()
    || "来源古籍";
}

/**
 * Turn a source-library candidate into a non-crashing experience story. The
 * service can replace these conservative placeholders with generated details
 * when selection succeeds; no placeholder is presented as an original fact.
 */
export function prepareStoryForExperience(candidate: StoryCandidate): StoryCard {
  const source = candidate.sourceCanon ?? {};
  const sourceTitle = storySourceLabel(candidate);
  const rawExcerpt = source.excerpt?.trim() ?? "";
  const excerptPreview = rawExcerpt.length > 180 ? `${rawExcerpt.slice(0, 180)}…` : rawExcerpt;
  const summary = candidate.summary?.trim()
    || excerptPreview
    || `《${candidate.title}》收录于${sourceTitle}；选择后将根据原文整理情节线。`;
  const motifs = candidate.motifs?.length
    ? candidate.motifs
    : source.motifs?.filter(Boolean) ?? [];
  const onDemand = storyNeedsOnDemandPreparation(candidate);
  const references = source.references?.filter((reference) => Boolean(reference?.title)) ?? [];

  return {
    ...candidate,
    summary,
    characters: candidate.characters?.filter(Boolean) ?? [],
    conflict: candidate.conflict?.trim() || "原文的核心转折将在故事讲解中整理。",
    motifs,
    imagery: candidate.imagery?.filter(Boolean) ?? [],
    emotionalArc: candidate.emotionalArc?.trim() ?? "",
    possibleResonance: storyRecommendationText(candidate),
    mayNotFit: candidate.mayNotFit?.trim() || "如果这则故事与你想谈的方向不合，可以返回并换一则。",
    contentWarnings: candidate.contentWarnings ?? [],
    recommendationReason: storyRecommendationText(candidate),
    experienceMode: onDemand ? "on_demand" : "prepared",
    sourceCanon: {
      storyVersionId: source.storyVersionId ?? candidate.storyVersionId,
      title: source.title?.trim() || candidate.title,
      sourceTitle,
      excerpt: rawExcerpt || summary,
      originalEnding: source.originalEnding?.trim() || "原文尚未拆出单独结局，确认后将按全文整理。",
      motifs,
      mustKeep: source.mustKeep?.filter(Boolean) ?? [`题名“${candidate.title}”及其原文出处`],
      allowedTransformations: source.allowedTransformations?.filter(Boolean) ?? ["现代支线的场景与表达"],
      prohibitedChanges: source.prohibitedChanges?.filter(Boolean) ?? ["把自动整理的内容冒充原文"],
      references: references.length > 0 ? references : [{
        id: `source-${candidate.storyVersionId}`,
        title: sourceTitle,
        locator: "",
      }],
    },
  };
}

export function boundedActIndex(index: number, actCount: number): number {
  if (actCount <= 0) return 0;
  return Math.min(Math.max(0, index), actCount - 1);
}
