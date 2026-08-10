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
  { id: "clarify", label: "解释这处", description: "说明故事与经历为何这样对应" },
  { id: "reorder", label: "调整顺序", description: "重新安排右页内容的位置" },
  { id: "fill_gap", label: "补充一处", description: "为尚未写清的地方准备说法" },
  { id: "preserve_boundary", label: "检查原文", description: "核对哪些内容不能误写" },
];

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
  onSend: (text: string) => Promise<void>;
  onApplyUpdates: () => void;
  onDiscardUpdates: () => void;
}) {
  const [draft, setDraft] = useState("");
  const activeNode = nodes.find((node) => node.id === activeNodeId) ?? nodes[0];
  const sourceBeat = deriveSourceBeats(story).find((beat) => beat.id === activeNodeId);

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || disabled) return;
    void onSend(text);
    setDraft("");
  }

  return (
    <aside className="mapping-assistant" aria-labelledby="mapping-assistant-title">
      <header className="mapping-assistant__header">
        <div className="mapping-assistant__identity">
          <img src="/assets/qidie-assistant-avatar-transparent.png" alt="" />
          <div>
          <p className="section-label">通过与栖蝶对话修改</p>
          <h2 id="mapping-assistant-title">栖蝶</h2>
          </div>
        </div>
      </header>

      <div className="mapping-assistant__disclosure">
        <p>告诉栖蝶想改哪一处；它会先给出预览，由你决定是否应用。</p>
      </div>

      <section className="mapping-assistant__context" aria-label="当前映照位置">
        <span>正在看</span>
        <strong>{activeNode?.title}</strong>
        <p><b>古籍依据：</b>{toSimplifiedDisplay(sourceBeat?.content)}</p>
      </section>

      <div className="mapping-assistant__intents" aria-label="半结构化对话意图">
        {INTENTS.map((intent) => (
          <button
            key={intent.id}
            type="button"
            disabled={disabled || busyIntent !== null}
            onClick={() => onIntent(intent.id)}
            aria-label={`${intent.label}：${intent.description}`}
          >
            <strong>{busyIntent === intent.id ? "处理中…" : intent.label}</strong>
          </button>
        ))}
      </div>

      <div className="mapping-assistant__messages" aria-live="polite" aria-label="映照对话记录">
        {messages.map((message) => (
          <div key={message.id} className={`mapping-message mapping-message--${message.role}`}>
            <span>{message.role === "assistant" ? "栖蝶" : "你"}</span>
            <p>{message.text}</p>
          </div>
        ))}
      </div>

      {pendingUpdates.length > 0 && (
        <section className="mapping-assistant__preview" aria-label="待确认的画布修改">
          <h3>待确认的修改</h3>
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
            <button type="button" onClick={onDiscardUpdates}>暂不采用</button>
            <button type="button" className="primary-action compact-action" onClick={onApplyUpdates}>应用到画布</button>
          </div>
        </section>
      )}

      <form className="mapping-assistant__composer" onSubmit={submit}>
        <label htmlFor="mapping-chat-input">继续说明你想怎么改</label>
        <textarea
          id="mapping-chat-input"
          rows={3}
          maxLength={360}
          value={draft}
          disabled={disabled || assistantBusy}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="例如：第二段其实先发生；或这个节点和我的经历不对应。"
        />
        <div>
          <small>{draft.length}/360</small>
          <button type="submit" disabled={disabled || assistantBusy || !draft.trim()}>{assistantBusy ? "正在整理…" : "发送"}</button>
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
  const messageCounter = useRef(1);
  const [messages, setMessages] = useState<AssistantMessage[]>([
    {
      id: 0,
      role: "assistant",
      text: `《${toSimplifiedDisplay(story.title)}》与刚才经历的五处映照已列在画布中。选中节点后，可以继续说明要修改的地方。`,
    },
  ]);

  useEffect(() => {
    setNodes(normalizeNodes(branch.nodes));
    setHopeType(branch.hopeAnchor?.type ?? "");
    setHopeDetail(branch.hopeAnchor?.detail ?? "");
  }, [branch.id, branch.version]);

  const hopeAnchor = hopeType ? { type: hopeType, detail: hopeDetail } satisfies HopeAnchor : undefined;
  const addressedCount = nodes.filter((node) => node.skipped || node.value.trim()).length;
  const gapCount = nodes.filter((node) => !node.skipped && !node.value.trim()).length;
  const preview = useMemo(() => buildBranchPreview(nodes, hopeAnchor), [nodes, hopeAnchor]);
  const ready = branchReadyForApproval(nodes, hopeAnchor);
  const branchApproved = branch.status === "approved";
  const recoverableHopeAnchor = hopeAnchor ?? branch.hopeAnchor;

  function appendMessage(role: AssistantMessage["role"], text: string) {
    const id = messageCounter.current;
    messageCounter.current += 1;
    setMessages((current) => [...current.slice(-7), { id, role, text }]);
  }

  function updateNode(nodeId: BranchNodeId, patch: Partial<BranchNodeDraft>) {
    setNodes((current) => current.map((node) => node.id === nodeId ? { ...node, ...patch } : node));
  }

  function moveContent(nodeId: BranchNodeId, delta: -1 | 1) {
    const fromIndex = nodes.findIndex((node) => node.id === nodeId);
    const targetNode = nodes[fromIndex + delta];
    if (!targetNode) return;
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

  async function handleFreeformMessage(text: string) {
    appendMessage("user", text);
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
        appendMessage("assistant", `已生成 ${fallbackUpdates.length} 处修改，确认后写入画布。`);
      } else {
        appendMessage("assistant", "我理解了你的说明，但这次没有形成可写入的修改。你可以换一种说法，或直接展开节点校对。");
      }
    } catch (assistantError) {
      setLocalError(assistantError instanceof Error ? assistantError.message : "这次没有取得修改建议，请稍后再试。");
      appendMessage("assistant", "这次没有取得修改建议。你的原有画布没有变化，可以稍后再试。");
    } finally {
      setAssistantBusy(false);
    }
  }

  function applyPendingUpdates() {
    setNodes((current) => current.map((node) => {
      const update = pendingUpdates.find((item) => item.nodeId === node.id);
      return update
        ? { ...node, value: update.value, skipped: false, expressionOrigin: "model_edited" }
        : node;
    }));
    appendMessage("assistant", `已把 ${pendingUpdates.length} 处修改写入画布。你仍可逐项展开校对。`);
    setPendingUpdates([]);
  }

  return (
    <main id="main" className="stage-page articulation-page mapping-stage">
      <header className="stage-intro mapping-stage__intro">
        <div>
          <p className="section-label">共谱</p>
          <h1>校对故事与经历的映照初稿。</h1>
        </div>
        <p>画布已按对话自动生成；可以直接编辑，或使用右侧助手修改。</p>
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
            disabled={branchApproved || busy}
            suggestingNode={suggestingNode}
            onActivate={setActiveNodeId}
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
                    disabled={branchApproved}
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
                disabled={branchApproved}
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
        </div>

        <MappingAssistant
          story={story}
          nodes={nodes}
          activeNodeId={activeNodeId}
          disabled={branchApproved || busy}
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

      <footer className="mapping-approval-dock">
        <div className="mapping-approval-dock__status">
          <span>{gapCount > 0 ? <><strong>{gapCount}</strong> 个节点待处理</> : <><strong>{addressedCount}</strong> 个节点已处理</>}</span>
          <p>{branchApproved ? "这版共谱已确认，可以进入再演。" : ready ? "初稿已完成校对，可以进入再演。" : "请看完所有节点，并添加一个回返支点。"}</p>
        </div>
        <div className="mapping-approval-dock__actions">
          <button
            className="secondary-action"
            type="button"
            disabled={branchApproved || !hopeAnchor || busy}
            onClick={() => hopeAnchor && void onSave(nodes, hopeAnchor)}
          >
            保存这版修改
          </button>
          <button
            className="primary-action compact-action"
            type="button"
            disabled={busy || !recoverableHopeAnchor || (!branchApproved && !ready)}
            onClick={() => recoverableHopeAnchor && void onApprove(nodes, recoverableHopeAnchor)}
          >
            {busy ? "正在准备…" : branchApproved ? "继续进入再演" : "确认共谱并进入再演"}
          </button>
        </div>
      </footer>
    </main>
  );
}
