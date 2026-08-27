import { useEffect, useMemo, useRef, useState } from "react";

import "../mapping-canvas.css";
import type {
  BranchNodeDraft,
  BranchNodeId,
  BranchSuggestionResponse,
  HopeAnchor,
  HopeAnchorType,
  StoryCard,
  UserBranchVersion,
} from "../types";
import {
  BRANCH_NODE_DEFINITIONS,
  HOPE_ANCHORS,
  branchReadyForApproval,
  buildBranchPreview,
  makeEmptyBranchNodes,
} from "../lib/story";
import { toSimplifiedDisplay } from "../lib/display-text";
import { NarrativeMappingCanvas, deriveSourceBeats } from "./NarrativeMappingCanvas";
import { QidieAvatar } from "./QidieAvatar";
import { StatusMessage } from "./StoryLoom";

type AssistantIntent = "clarify" | "reorder" | "fill_gap" | "preserve_boundary";

interface AssistantMessage {
  id: number;
  role: "assistant" | "user";
  text: string;
}

interface SuggestedNodeUpdate {
  nodeId: BranchNodeId;
  value: string;
  rationale?: string;
}

const INTENTS: ReadonlyArray<{ id: AssistantIntent; label: string; description: string }> = [
  { id: "clarify", label: "说明这处对应", description: "比较原典情节与我的经历" },
  { id: "reorder", label: "调整节点顺序", description: "交换右页两处内容" },
  { id: "fill_gap", label: "补上空缺节点", description: "为未写清的一处起草" },
  { id: "preserve_boundary", label: "核对原典边界", description: "查看哪些事实不能改写" },
];

const ALL_NODE_IDS = BRANCH_NODE_DEFINITIONS.map((definition) => definition.id);

function normalizeNodes(nodes: BranchNodeDraft[] | undefined): BranchNodeDraft[] {
  const fallback = makeEmptyBranchNodes();
  if (!nodes?.length) return fallback;
  return fallback.map((node) => {
    const existing = nodes.find((candidate) => candidate.id === node.id);
    return existing ? { ...node, ...existing, suggestions: existing.suggestions ?? [] } : node;
  });
}

function SourceBoundary({ story }: { story: StoryCard }) {
  const canon = story.sourceCanon;
  return (
    <details className="mapping-boundary">
      <summary>
        <span className="mapping-boundary__seal" aria-hidden="true">典</span>
        <span>
          <strong>本次映照依据：《{toSimplifiedDisplay(canon.title)}》</strong>
          <small>{toSimplifiedDisplay(canon.sourceTitle)} · 情节线依据原文整理</small>
        </span>
        <span className="mapping-boundary__action">查看原文与改编边界</span>
      </summary>
      <div className="mapping-boundary__content">
        <section>
          <p className="section-label">原文节选</p>
          <blockquote>{toSimplifiedDisplay(canon.excerpt)}</blockquote>
          <p>{toSimplifiedDisplay(story.summary)}</p>
        </section>
        <section>
          <h3>必须保留</h3>
          <ul>{canon.mustKeep.map((item) => <li key={item}>{toSimplifiedDisplay(item)}</li>)}</ul>
        </section>
        <section>
          <h3>允许转化</h3>
          <ul>{canon.allowedTransformations.map((item) => <li key={item}>{toSimplifiedDisplay(item)}</li>)}</ul>
        </section>
        {canon.prohibitedChanges.length > 0 && (
          <section>
            <h3>禁止改写</h3>
            <ul>{canon.prohibitedChanges.map((item) => <li key={item}>{toSimplifiedDisplay(item)}</li>)}</ul>
          </section>
        )}
      </div>
    </details>
  );
}

function MappingAssistant({
  story,
  nodes,
  activeNodeId,
  disabled,
  messages,
  busyIntent,
  assistantBusy,
  pendingUpdates,
  onIntent,
  onSend,
  onApplyUpdates,
  onDiscardUpdates,
}: {
  story: StoryCard;
  nodes: BranchNodeDraft[];
  activeNodeId: BranchNodeId;
  disabled: boolean;
  messages: AssistantMessage[];
  busyIntent: AssistantIntent | null;
  assistantBusy: boolean;
  pendingUpdates: SuggestedNodeUpdate[];
  onIntent: (intent: AssistantIntent) => void;
  onSend: (text: string, retry: boolean) => Promise<boolean>;
  onApplyUpdates: () => void;
  onDiscardUpdates: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [sendFailed, setSendFailed] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const previewRef = useRef<HTMLElement>(null);
  const activeNode = nodes.find((node) => node.id === activeNodeId) ?? nodes[0];
  const sourceBeat = deriveSourceBeats(story).find((beat) => beat.id === activeNodeId);
  const awaitingPreviewDecision = pendingUpdates.length > 0;

  useEffect(() => {
    if (sendFailed) {
      composerRef.current?.focus();
    } else if (awaitingPreviewDecision) {
      previewRef.current?.focus();
    }
  }, [awaitingPreviewDecision, sendFailed]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || disabled || assistantBusy || awaitingPreviewDecision) return;
    const succeeded = await onSend(text, sendFailed);
    setSendFailed(!succeeded);
    if (succeeded) setDraft("");
  }

  return (
    <aside className="mapping-assistant" aria-labelledby="mapping-assistant-title">
      <header className="mapping-assistant__header">
        <div className="mapping-assistant__identity">
          <QidieAvatar size={46} className="mapping-assistant__avatar" />
          <div>
          <p className="section-label">通过与栖蝶对话修改</p>
          <h2 id="mapping-assistant-title">栖蝶</h2>
          </div>
        </div>
      </header>

      <div className="mapping-assistant__disclosure">
        <p>指出哪一处不准确，栖蝶只会准备修改预览；你点击“应用到画布”后才会改动初稿。</p>
      </div>

      <section className="mapping-assistant__context" aria-label="当前映照位置">
        <span>当前校对</span>
        <strong>{activeNode?.title}</strong>
        <p><b>古籍依据：</b>{toSimplifiedDisplay(sourceBeat?.content)}</p>
      </section>

      <div className="mapping-assistant__intents" aria-label="常用修改方式">
        {INTENTS.map((intent) => (
          <button
            key={intent.id}
            type="button"
            disabled={disabled || assistantBusy || awaitingPreviewDecision || busyIntent !== null}
            onClick={() => onIntent(intent.id)}
            aria-label={`${intent.label}：${intent.description}`}
          >
            <strong>{busyIntent === intent.id ? "处理中…" : intent.label}</strong>
            <small>{intent.description}</small>
          </button>
        ))}
      </div>

      <div className="mapping-assistant__messages" aria-live="polite" aria-label="映照对话记录">
        {messages.map((message) => (
          <div key={message.id} className={`mapping-message mapping-message--${message.role}`}>
            <span className="mapping-message__author">
              {message.role === "assistant" ? <><QidieAvatar size={24} className="mapping-message__avatar" />栖蝶</> : "你"}
            </span>
            <p>{message.text}</p>
          </div>
        ))}
      </div>

      {pendingUpdates.length > 0 && (
        <section
          ref={previewRef}
          className="mapping-assistant__preview"
          aria-label="待确认的画布修改"
          tabIndex={-1}
        >
          <h3>修改预览 · 尚未写入画布</h3>
          {pendingUpdates.map((update) => {
            const node = nodes.find((item) => item.id === update.nodeId);
            return (
              <article key={update.nodeId}>
                <strong>{node?.title ?? "映照节点"}</strong>
                <p>{update.value}</p>
                {update.rationale ? <small>{update.rationale}</small> : null}
              </article>
            );
          })}
          <div>
            <button type="button" onClick={() => { setSendFailed(false); onDiscardUpdates(); }}>暂不采用</button>
            <button type="button" className="primary-action compact-action" onClick={onApplyUpdates}>应用到画布</button>
          </div>
        </section>
      )}

      <form className="mapping-assistant__composer" onSubmit={submit}>
        <label htmlFor="mapping-chat-input">告诉栖蝶哪处不准确，或希望怎样改</label>
        <textarea
          ref={composerRef}
          id="mapping-chat-input"
          rows={3}
          maxLength={360}
          value={draft}
          disabled={disabled || assistantBusy || awaitingPreviewDecision}
          aria-describedby={sendFailed ? "mapping-chat-retry-hint" : undefined}
          onChange={(event) => {
            setDraft(event.target.value);
            setSendFailed(false);
          }}
          placeholder="例如：第二段其实先发生；这里写的是同事，不是家人。"
        />
        {sendFailed && <p id="mapping-chat-retry-hint" className="mapping-assistant__retry" role="status">刚才的说明已保留，可以直接重试。</p>}
        <div>
          <small>{draft.length}/360</small>
          <button
            type="submit"
            disabled={disabled || assistantBusy || awaitingPreviewDecision || !draft.trim()}
          >
            {assistantBusy ? "正在生成预览…" : sendFailed ? "重试生成预览" : "发送修改说明"}
          </button>
        </div>
      </form>
    </aside>
  );
}

export function ArticulationStage({
  story,
  branch,
  busy,
  error,
  notice,
  onSuggest,
  onSave,
  onApprove,
}: {
  story: StoryCard;
  branch: UserBranchVersion;
  busy: boolean;
  error: string | null;
  notice: string | null;
  onSuggest: (
    nodeId: BranchNodeId,
    nodes: BranchNodeDraft[],
    hopeAnchor?: HopeAnchor,
    assistantMessage?: string,
  ) => Promise<BranchSuggestionResponse>;
  onSave: (nodes: BranchNodeDraft[], hopeAnchor: HopeAnchor) => Promise<void>;
  onApprove: (nodes: BranchNodeDraft[], hopeAnchor: HopeAnchor) => Promise<void>;
}) {
  const [nodes, setNodes] = useState<BranchNodeDraft[]>(() => normalizeNodes(branch.nodes));
  const [activeNodeId, setActiveNodeId] = useState<BranchNodeId>("world_crack");
  const [hopeType, setHopeType] = useState<HopeAnchorType | "">(branch.hopeAnchor?.type ?? "");
  const [hopeDetail, setHopeDetail] = useState(branch.hopeAnchor?.detail ?? "");
  const [suggestingNode, setSuggestingNode] = useState<BranchNodeId | null>(null);
  const [busyIntent, setBusyIntent] = useState<AssistantIntent | null>(null);
  const [assistantBusy, setAssistantBusy] = useState(false);
  const [pendingUpdates, setPendingUpdates] = useState<SuggestedNodeUpdate[]>([]);
  const [localError, setLocalError] = useState<string | null>(null);
  const [reviewedNodeIds, setReviewedNodeIds] = useState<Set<BranchNodeId>>(() => new Set(
    branch.status === "approved" ? ALL_NODE_IDS : [],
  ));
  const messageCounter = useRef(1);
  const [messages, setMessages] = useState<AssistantMessage[]>([
    {
      id: 0,
      role: "assistant",
      text: `我先把《${toSimplifiedDisplay(story.title)}》与你的经历写成五处对应。当前只是初稿；依次看一遍，准确的不用改，不准确的再告诉我。`,
    },
  ]);

  useEffect(() => {
    setNodes(normalizeNodes(branch.nodes));
    setHopeType(branch.hopeAnchor?.type ?? "");
    setHopeDetail(branch.hopeAnchor?.detail ?? "");
  }, [branch.id, branch.version]);

  useEffect(() => {
    setReviewedNodeIds(new Set(branch.status === "approved" ? ALL_NODE_IDS : []));
  }, [branch.id]);

  useEffect(() => {
    if (branch.status === "approved") setReviewedNodeIds(new Set(ALL_NODE_IDS));
  }, [branch.status]);

  const hopeAnchor = hopeType ? { type: hopeType, detail: hopeDetail } satisfies HopeAnchor : undefined;
  const draftedCount = nodes.filter((node) => node.skipped || node.value.trim()).length;
  const gapCount = nodes.filter((node) => !node.skipped && !node.value.trim()).length;
  const reviewedCount = reviewedNodeIds.size;
  const allNodesReviewed = reviewedCount === ALL_NODE_IDS.length;
  const preview = useMemo(() => buildBranchPreview(nodes, hopeAnchor), [nodes, hopeAnchor]);
  const contentReady = branchReadyForApproval(nodes, hopeAnchor);
  const ready = contentReady && allNodesReviewed;
  const branchApproved = branch.status === "approved";
  const recoverableHopeAnchor = hopeAnchor ?? branch.hopeAnchor;
  const assistantWorkPending = assistantBusy
    || busyIntent !== null
    || suggestingNode !== null
    || pendingUpdates.length > 0;
  const editingLocked = branchApproved || busy || assistantWorkPending;

  function appendMessage(role: AssistantMessage["role"], text: string) {
    const id = messageCounter.current;
    messageCounter.current += 1;
    setMessages((current) => [...current.slice(-7), { id, role, text }]);
  }

  function markNodeReviewed(nodeId: BranchNodeId) {
    setReviewedNodeIds((current) => {
      if (current.has(nodeId)) return current;
      const next = new Set(current);
      next.add(nodeId);
      return next;
    });
  }

  function activateNode(nodeId: BranchNodeId) {
    setActiveNodeId(nodeId);
    markNodeReviewed(nodeId);
  }

  function updateNode(nodeId: BranchNodeId, patch: Partial<BranchNodeDraft>) {
    markNodeReviewed(nodeId);
    setNodes((current) => current.map((node) => node.id === nodeId ? { ...node, ...patch } : node));
  }

  function moveContent(nodeId: BranchNodeId, delta: -1 | 1) {
    const fromIndex = nodes.findIndex((node) => node.id === nodeId);
    const targetNode = nodes[fromIndex + delta];
    if (!targetNode) return;
    markNodeReviewed(nodeId);
    markNodeReviewed(targetNode.id);
    setNodes((current) => {
      const from = current.findIndex((node) => node.id === nodeId);
      const to = from + delta;
      if (from < 0 || to < 0 || to >= current.length) return current;
      const transferable = (["value", "skipped", "suggestions", "expressionOrigin"] as const);
      const next = current.map((node) => ({ ...node, suggestions: [...node.suggestions] }));
      const fromData = Object.fromEntries(transferable.map((key) => [key, next[from][key]]));
      const toData = Object.fromEntries(transferable.map((key) => [key, next[to][key]]));
      next[from] = { ...next[from], ...toData } as BranchNodeDraft;
      next[to] = { ...next[to], ...fromData } as BranchNodeDraft;
      return next;
    });
    setActiveNodeId(targetNode.id);
    appendMessage("assistant", "已移动右页内容；左页的古籍节点和顺序没有变化。请检查新的对应关系是否准确。");
  }

  async function getSuggestions(nodeId: BranchNodeId, assistantMessage?: string): Promise<string[]> {
    setLocalError(null);
    setSuggestingNode(nodeId);
    try {
      const response = await onSuggest(nodeId, nodes, hopeAnchor, assistantMessage);
      const suggestions = response.suggestions.slice(0, 2);
      updateNode(nodeId, { suggestions });
      return suggestions;
    } catch (suggestionError) {
      setLocalError(suggestionError instanceof Error ? suggestionError.message : "这次没有取到建议。可以直接编辑，或先标为待补。");
      return [];
    } finally {
      setSuggestingNode(null);
    }
  }

  function chooseSuggestion(nodeId: BranchNodeId, suggestion: string) {
    updateNode(nodeId, { value: suggestion, skipped: false, expressionOrigin: "model_edited" });
  }

  async function handleIntent(intent: AssistantIntent) {
    const activeNode = nodes.find((node) => node.id === activeNodeId) ?? nodes[0];
    const sourceBeat = deriveSourceBeats(story).find((beat) => beat.id === activeNode.id);
    setBusyIntent(intent);
    try {
      if (intent === "clarify") {
        appendMessage("assistant", `先区分两件事：原典在这里记录的是“${sourceBeat?.content}”。对你的经历，${activeNode.prompt} 可以只回答发生了什么，不必解释原因。`);
      } else if (intent === "reorder") {
        appendMessage("assistant", "左页的五处古籍依据不会移动。展开要调整的节点，使用“内容上移/下移”交换右页内容，就能重排你的叙事而不改动原文顺序。");
      } else if (intent === "preserve_boundary") {
        const mustKeep = story.sourceCanon.mustKeep.join("；") || "无额外标注";
        const prohibited = story.sourceCanon.prohibitedChanges.join("；") || "无额外标注";
        appendMessage("assistant", `当前边界：必须保留——${mustKeep}。禁止改写——${prohibited}。这些约束只作用于对原典的陈述，不要求你的经历复制原典结局。`);
      } else {
        const gap = nodes.find((node) => !node.skipped && !node.value.trim());
        if (!gap) {
          appendMessage("assistant", "当前没有待补缺口。你仍可以点开任一节点修改，或把不对应的节点标为“不适用”。");
          return;
        }
        setActiveNodeId(gap.id);
        const suggestions = await getSuggestions(gap.id);
        appendMessage(
          "assistant",
          suggestions.length > 0
            ? `已为“${gap.title}”取得 ${suggestions.length} 条可修改草案。它们显示在画布节点里，不会自动写入你的版本。`
            : `“${gap.title}”仍保留为待补；建议服务暂时没有返回内容。`,
        );
      }
    } finally {
      setBusyIntent(null);
    }
  }

  async function handleFreeformMessage(text: string, retry = false): Promise<boolean> {
    if (!retry) appendMessage("user", text);
    setAssistantBusy(true);
    setLocalError(null);
    try {
      const response = await onSuggest(activeNodeId, nodes, hopeAnchor, text);
      const updates = (response.nodeUpdates ?? []).filter((update) =>
        nodes.some((node) => node.id === update.nodeId) && Boolean(update.value.trim()),
      );
      const fallbackUpdates: SuggestedNodeUpdate[] = updates.length === 0 && response.suggestions.length > 0
        ? [{ nodeId: activeNodeId, value: response.suggestions[0], rationale: "根据刚才的说明调整当前节点" }]
        : updates;
      if (fallbackUpdates.length > 0) {
        setPendingUpdates(fallbackUpdates);
        appendMessage("assistant", `我准备了 ${fallbackUpdates.length} 处修改预览。先核对内容，点击“应用到画布”后才会替换初稿。`);
      } else {
        appendMessage("assistant", "这段说明没有形成具体改动。可以直接指出节点名称和要改的事实，或在左侧展开该节点编辑。");
      }
      return true;
    } catch (assistantError) {
      setLocalError(assistantError instanceof Error ? assistantError.message : "未能生成修改预览。刚才的说明已保留，可以直接重试。");
      appendMessage("assistant", "未能生成修改预览。画布没有变化，刚才的说明仍在输入框中。");
      return false;
    } finally {
      setAssistantBusy(false);
    }
  }

  function applyPendingUpdates() {
    pendingUpdates.forEach((update) => markNodeReviewed(update.nodeId));
    setNodes((current) => current.map((node) => {
      const update = pendingUpdates.find((item) => item.nodeId === node.id);
      return update
        ? { ...node, value: update.value, skipped: false, expressionOrigin: "model_edited" }
        : node;
    }));
    appendMessage("assistant", `已把 ${pendingUpdates.length} 处修改写入画布。其他节点没有变化。`);
    setPendingUpdates([]);
  }

  return (
    <main id="main" className="stage-page articulation-page mapping-stage">
      <header className="stage-intro mapping-stage__intro">
        <div>
          <p className="section-label">共谱</p>
          <h1>校对故事与经历的映照初稿。</h1>
        </div>
        <p>栖蝶已起草五处映照。依次展开看一眼；准确的不用改，不准确的再调整。</p>
      </header>

      <StatusMessage error={error ?? localError} notice={notice} />
      {branchApproved && (
        <p className="approved-recovery" role="status">
          这版共谱已确认，画布保持锁定。可以直接重试进入再演。
        </p>
      )}

      <SourceBoundary story={story} />

      <div className="mapping-stage__layout">
        <div className="mapping-stage__main">
          <NarrativeMappingCanvas
            story={story}
            nodes={nodes}
            activeNodeId={activeNodeId}
            disabled={editingLocked}
            suggestingNode={suggestingNode}
            reviewedNodeIds={reviewedNodeIds}
            onActivate={activateNode}
            onReview={markNodeReviewed}
            onChange={updateNode}
            onMoveContent={moveContent}
            onRequestSuggestion={(nodeId) => void getSuggestions(nodeId)}
            onChooseSuggestion={chooseSuggestion}
          />

          <section className="mapping-hope" aria-labelledby="mapping-hope-title">
            <header>
              <div>
                <p className="section-label">回返支点</p>
                <h2 id="mapping-hope-title">这段映照最后带回什么？</h2>
              </div>
            </header>
            <fieldset>
              <legend className="sr-only">希望支点类型</legend>
              {HOPE_ANCHORS.map((anchor) => (
                <label key={anchor.type} className={hopeType === anchor.type ? "is-selected" : ""}>
                  <input
                    type="radio"
                    disabled={editingLocked}
                    name="hope-anchor"
                    value={anchor.type}
                    checked={hopeType === anchor.type}
                    onChange={() => setHopeType(anchor.type)}
                  />
                  <span><strong>{anchor.label}</strong><small>{anchor.description}</small></span>
                </label>
              ))}
            </fieldset>
            <label className="mapping-hope__detail">
              <span>用一句话写下支点 <small>{hopeDetail.length}/180</small></span>
              <textarea
                rows={3}
                maxLength={180}
                disabled={editingLocked}
                value={hopeDetail}
                onChange={(event) => setHopeDetail(event.target.value)}
                placeholder="例如：答案还没出现，但我愿意先联系一个可以同行的人。"
              />
            </label>
          </section>

          <details className="mapping-preview">
            <summary>预览你的完整版本 <span>{preview.length} 字</span></summary>
            <div>{preview ? preview.split("\n\n").map((paragraph, index) => <p key={`${paragraph}-${index}`}>{paragraph}</p>) : <p>画布中的用户内容会在这里连成支线。</p>}</div>
          </details>

          <footer className="mapping-approval-dock" aria-label="保存或确认共谱">
            <div className="mapping-approval-dock__status" role="status" aria-live="polite">
              <span><strong>{draftedCount}</strong> 处已有初稿</span>
              <span><strong>{reviewedCount}</strong> / {ALL_NODE_IDS.length} 处已查看</span>
              <p>
                {branchApproved
                  ? "这版共谱已由你确认，可以进入再演。"
                  : assistantBusy || busyIntent !== null || suggestingNode !== null
                    ? "栖蝶正在生成内容；完成前不会保存或进入再演。"
                    : pendingUpdates.length > 0
                      ? "有修改预览待决定；应用或暂不采用后才能保存。"
                      : gapCount > 0
                        ? `${gapCount} 处仍待补；也可以把确实不对应的节点标为“不适用”。`
                        : !allNodesReviewed
                          ? `再展开 ${ALL_NODE_IDS.length - reviewedCount} 处看一眼；只改不准确的地方。`
                          : !contentReady
                            ? "五处都已查看；再选择并写下一个回返支点。"
                            : "五处都已查看，可以确认这版共谱。"}
              </p>
            </div>
            <div className="mapping-approval-dock__actions">
              <button
                className="secondary-action"
                type="button"
                disabled={branchApproved || !hopeAnchor || busy || assistantWorkPending}
                onClick={() => hopeAnchor && void onSave(nodes, hopeAnchor)}
              >
                保存这版修改
              </button>
              <button
                className="primary-action compact-action"
                type="button"
                disabled={busy || assistantWorkPending || !recoverableHopeAnchor || (!branchApproved && !ready)}
                onClick={() => recoverableHopeAnchor && void onApprove(nodes, recoverableHopeAnchor)}
              >
                {busy ? "正在准备…" : branchApproved ? "继续进入再演" : "确认共谱并进入再演"}
              </button>
            </div>
          </footer>
        </div>

        <MappingAssistant
          story={story}
          nodes={nodes}
          activeNodeId={activeNodeId}
          disabled={branchApproved || busy || suggestingNode !== null}
          messages={messages}
          busyIntent={busyIntent}
          assistantBusy={assistantBusy}
          pendingUpdates={pendingUpdates}
          onIntent={(intent) => void handleIntent(intent)}
          onSend={handleFreeformMessage}
          onApplyUpdates={applyPendingUpdates}
          onDiscardUpdates={() => setPendingUpdates([])}
        />
      </div>
    </main>
  );
}
