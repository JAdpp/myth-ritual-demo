import type { BranchNodeDraft, BranchNodeId, StoryCard } from "../types";
import { BRANCH_NODE_DEFINITIONS } from "../lib/story";
import { toSimplifiedDisplay } from "../lib/display-text";

export interface SourceBeat {
  id: BranchNodeId;
  label: string;
  content: string;
  evidenceLabel: string;
}

const SOURCE_BEAT_LABELS: Record<BranchNodeId, string> = {
  world_crack: "故事起势",
  cross_threshold: "核心冲突",
  allies_resources: "角色与力量",
  new_understanding: "原典走向",
  bring_back: "可迁移母题",
};

/**
 * Build only from fields already present in source_canon/story metadata. This is
 * deliberately conservative: the canvas must not invent a missing source beat.
 */
export function deriveSourceBeats(story: StoryCard): SourceBeat[] {
  const canon = story.sourceCanon;
  const content: Record<BranchNodeId, string> = {
    world_crack: canon.excerpt,
    cross_threshold: story.conflict,
    allies_resources: story.characters.length > 0 ? story.characters.join("、") : "原典未单列角色",
    new_understanding: canon.originalEnding,
    bring_back: canon.motifs.length > 0 ? canon.motifs.join(" · ") : "原典未单列母题",
  };
  const evidence: Record<BranchNodeId, string> = {
    world_crack: "原典摘录",
    cross_threshold: "冲突标注",
    allies_resources: "角色标注",
    new_understanding: "原典结局",
    bring_back: "母题标注",
  };

  return BRANCH_NODE_DEFINITIONS.map((definition) => ({
    id: definition.id,
    label: SOURCE_BEAT_LABELS[definition.id],
    content: content[definition.id],
    evidenceLabel: evidence[definition.id],
  }));
}

function nodeState(node: BranchNodeDraft): "mapped" | "gap" | "not_applicable" {
  if (node.skipped) return "not_applicable";
  return node.value.trim() ? "mapped" : "gap";
}

export function NarrativeMappingCanvas({
  story,
  nodes,
  activeNodeId,
  disabled,
  suggestingNode,
  reviewedNodeIds,
  onActivate,
  onReview,
  onChange,
  onMoveContent,
  onRequestSuggestion,
  onChooseSuggestion,
}: {
  story: StoryCard;
  nodes: BranchNodeDraft[];
  activeNodeId: BranchNodeId;
  disabled: boolean;
  suggestingNode: BranchNodeId | null;
  reviewedNodeIds: ReadonlySet<BranchNodeId>;
  onActivate: (nodeId: BranchNodeId) => void;
  onReview: (nodeId: BranchNodeId) => void;
  onChange: (nodeId: BranchNodeId, patch: Partial<BranchNodeDraft>) => void;
  onMoveContent: (nodeId: BranchNodeId, delta: -1 | 1) => void;
  onRequestSuggestion: (nodeId: BranchNodeId) => void;
  onChooseSuggestion: (nodeId: BranchNodeId, suggestion: string) => void;
}) {
  const sourceBeats = deriveSourceBeats(story);

  return (
    <section className="mapping-canvas" aria-labelledby="mapping-canvas-title">
      <header className="mapping-canvas__header">
        <div>
          <p className="section-label">映照初稿</p>
          <h2 id="mapping-canvas-title">原典情节与你经历的五处映照</h2>
          <p>五处都有初稿；展开即算看过，只需修改不准确的部分。</p>
        </div>
        <div className="mapping-canvas__legend" aria-label="画布图例">
          <span><i className="legend-source" />古籍依据</span>
          <span><i className="legend-branch" />你的经历</span>
          <span><i className="legend-gap" />内容待补</span>
        </div>
      </header>

      <div className="mapping-canvas__lane-heads" aria-hidden="true">
        <div><strong>原典情节</strong><span>只读 · {toSimplifiedDisplay(story.sourceCanon.sourceTitle)}</span></div>
        <span />
        <div><strong>你的经历</strong><span>可编辑 · 由你确认</span></div>
      </div>

      <ol className="mapping-canvas__rows" aria-label={`${toSimplifiedDisplay(story.title)}的叙事映照节点`}>
        {sourceBeats.map((beat, index) => {
          const node = nodes.find((item) => item.id === beat.id);
          if (!node) return null;
          const state = nodeState(node);
          const isActive = activeNodeId === node.id;
          const isReviewed = reviewedNodeIds.has(node.id);
          const editorId = `mapping-node-${node.id}`;

          return (
            <li
              key={node.id}
              className={`mapping-pair mapping-pair--${state}${isActive ? " is-active" : ""}${isReviewed ? " is-reviewed" : ""}`}
              data-node-id={node.id}
            >
              <article className="mapping-source-node" aria-label={`${beat.label}，原典只读节点`}>
                <div className="mapping-node__meta">
                  <strong>{beat.label}</strong>
                  <small>{beat.evidenceLabel}</small>
                </div>
                <p>{toSimplifiedDisplay(beat.content)}</p>
              </article>

              <div className="mapping-thread" aria-hidden="true">
                <span />
                <i />
                <span />
              </div>

              {/* Collapsed rows activate anywhere on the card. The title button
                  alone was a borderless transparent control that read as a
                  heading, so nodes past the first looked unselectable. */}
              <article
                className={`mapping-user-node${isActive ? "" : " is-collapsed"}`}
                onClick={isActive ? undefined : () => onActivate(node.id)}
              >
                <header>
                  <button
                    type="button"
                    className="mapping-user-node__title"
                    aria-expanded={isActive}
                    aria-controls={editorId}
                    onClick={() => onActivate(node.id)}
                  >
                    <strong>{node.title}</strong>
                  </button>
                  <span className="mapping-node__states">
                    {state !== "mapped" && (
                      <span className={`mapping-state mapping-state--${state}`}>
                        {state === "not_applicable" ? "不适用" : "内容待补"}
                      </span>
                    )}
                    <span className={`mapping-review-state${isReviewed ? " is-reviewed" : ""}`}>
                      {isReviewed ? "已看过" : "初稿待确认"}
                    </span>
                  </span>
                </header>

                {!isActive && (
                  <p className={`mapping-user-node__preview${node.value.trim() ? "" : " is-empty"}`}>
                    {node.skipped ? "这处暂不对应你的经历" : node.value.trim() || "这处需要你看一眼"}
                  </p>
                )}

                <div id={editorId} className="mapping-user-node__editor" hidden={!isActive}>
                  <p className="mapping-user-node__prompt">{node.prompt}</p>
                  <label>
                    <span className="sr-only">{node.title}</span>
                    <textarea
                      rows={4}
                      maxLength={360}
                      disabled={disabled || node.skipped}
                      value={node.value}
                      onFocus={() => onActivate(node.id)}
                      onChange={(event) => onChange(node.id, {
                        value: event.target.value,
                        skipped: false,
                        expressionOrigin: node.expressionOrigin === "model_edited" ? "model_edited" : "user",
                      })}
                      placeholder="用自己的说法写下对应的情境、动作或关系；不必模仿原典。"
                    />
                  </label>
                  <div className="mapping-user-node__counter">
                    {node.expressionOrigin === "model_edited" && <span>{isReviewed ? "栖蝶起草，你已看过" : "栖蝶起草，待你确认"}</span>}
                    <span>{node.value.length}/360</span>
                  </div>

                  {node.suggestions.length > 0 && (
                    <div className="mapping-suggestions" aria-label="可修改的节点建议">
                      {node.suggestions.slice(0, 2).map((suggestion, suggestionIndex) => (
                        <button
                          key={`${node.id}-${suggestionIndex}`}
                          type="button"
                          disabled={disabled}
                          onClick={() => onChooseSuggestion(node.id, suggestion)}
                        >
                          <span>备选说法 {suggestionIndex + 1}</span>{suggestion}
                        </button>
                      ))}
                    </div>
                  )}

                  <footer className="mapping-user-node__tools">
                    <button
                      type="button"
                      disabled={disabled || isReviewed}
                      onClick={() => onReview(node.id)}
                    >
                      {isReviewed ? "这处已看过" : "这处没问题"}
                    </button>
                    <button
                      type="button"
                      disabled={disabled || suggestingNode !== null}
                      onClick={() => onRequestSuggestion(node.id)}
                    >
                      {suggestingNode === node.id ? "正在准备建议…" : "给我两条可改建议"}
                    </button>
                    <button
                      type="button"
                      disabled={disabled || index === 0}
                      onClick={() => onMoveContent(node.id, -1)}
                      aria-label={`将${node.title}的用户内容上移`}
                    >
                      内容上移
                    </button>
                    <button
                      type="button"
                      disabled={disabled || index === nodes.length - 1}
                      onClick={() => onMoveContent(node.id, 1)}
                      aria-label={`将${node.title}的用户内容下移`}
                    >
                      内容下移
                    </button>
                    <button
                      type="button"
                      disabled={disabled}
                      aria-pressed={node.skipped}
                      onClick={() => onChange(node.id, { skipped: !node.skipped })}
                    >
                      {node.skipped ? "恢复为待补" : "标为不适用"}
                    </button>
                  </footer>
                </div>
              </article>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
