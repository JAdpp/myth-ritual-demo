import { useEffect, useMemo, useState } from "react";

import type {
  BriefInputMode,
  ConversationMessage,
  ConversationTurnResponse,
  ExperienceBrief,
  SafetyStopRoute,
  StoryCard,
  StoryOffer,
} from "../types";
import { getSafetyStopRoute } from "../lib/contracts";
import { toSimplifiedDisplay } from "../lib/display-text";
import {
  formatSourceReference,
  prepareStoryForExperience,
  storyRecommendationText,
  storySourceLabel,
} from "../lib/story";
import { createStoryCover } from "../api";
import { QidieAvatar } from "./QidieAvatar";
import { StatusMessage } from "./StoryLoom";
import { StoryIllustration } from "./StoryIllustration";

const STORY_STARTERS = [
  { id: "time", label: "事情发生在……" },
  { id: "turn", label: "原本……，后来……" },
  { id: "care", label: "当时我最在意的是……" },
] as const;

/** Mirrors _MAX_GUIDANCE_TURNS in the API; only a fallback until a turn responds. */
const MAX_GUIDANCE_TURNS = 4;

/** Shown while the confirmed summary is being turned into story cards. */
const RETRIEVAL_STEPS = [
  "确认摘要",
  "在已审核语料中检索",
  "核对出处与权利",
  "整理推荐理由",
] as const;

function RetrievalProgress({ activeStep }: { activeStep: number }) {
  return (
    <section className="retrieval-progress" role="status" aria-live="polite">
      <p className="section-label">正在找故事</p>
      <ol>
        {RETRIEVAL_STEPS.map((step, index) => {
          const state = index < activeStep ? "done" : index === activeStep ? "active" : "waiting";
          return (
            <li key={step} className={`retrieval-step is-${state}`}>
              <span className="retrieval-check" aria-hidden="true">{state === "done" ? "✓" : ""}</span>
              <span>{step}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

interface ChatMessage {
  id: string;
  role: "assistant" | "user";
  text: string;
  acknowledgement?: string;
  followUpQuestion?: string;
  source?: ConversationTurnResponse["source"];
}

/* Stands in only when the turn endpoint gives us nothing. It mirrors the
   server-side wording: acknowledge that we heard the person, without claiming
   to know how they feel about it. */
function assistantFollowUp(turn: number) {
  if (turn === 1) {
    return {
      acknowledgement: "这件事的起点我听见了，先放在这里。",
      followUpQuestion: "后来最先发生了什么，让事情有了变化？",
    };
  }
  if (turn === 2) {
    return {
      acknowledgement: "你愿意继续讲下去，这段经过我大致跟上了。",
      followUpQuestion: "在那个时刻，你最在意的是什么？",
    };
  }
  return {
    acknowledgement: "这些细节我都记着，不会被后面的概括盖过去。",
    followUpQuestion: "还有哪一处，是你希望我不要概括掉的？",
  };
}

function responseFollowUpOptions(response: ConversationTurnResponse | null | undefined): string[] {
  if (!response || !("followUpOptions" in response)) return [];
  const options = (response as ConversationTurnResponse & { followUpOptions?: unknown }).followUpOptions;
  if (!Array.isArray(options)) return [];
  return [...new Set(options
    .filter((option): option is string => typeof option === "string")
    .map((option) => option.trim())
    .filter(Boolean))]
    .slice(0, 3);
}

function StorySource({ card }: { card: StoryCard }) {
  return (
    <details className="source-details">
      <summary>查看原文出处与说明</summary>
      <div className="source-detail-grid">
        <div>
          <span>原文节选</span>
          <blockquote>{toSimplifiedDisplay(card.sourceCanon.excerpt)}</blockquote>
          <p><strong>原典结局：</strong>{toSimplifiedDisplay(card.sourceCanon.originalEnding)}</p>
        </div>
        <div>
          <span>出处与位置</span>
          <ul className="plain-list">
            {card.sourceCanon.references.map((reference) => (
              <li key={reference.id}>
                {reference.url ? (
                  <a href={reference.url} target="_blank" rel="noreferrer">{toSimplifiedDisplay(formatSourceReference(reference))}</a>
                ) : toSimplifiedDisplay(formatSourceReference(reference))}
              </li>
            ))}
          </ul>
          <span>需要保留的原典事实</span>
          <ul className="plain-list">{card.sourceCanon.mustKeep.map((item) => <li key={item}>{toSimplifiedDisplay(item)}</li>)}</ul>
        </div>
      </div>
    </details>
  );
}

function SafetyStopView({
  route,
  busy,
  error,
  onExit,
}: {
  route: SafetyStopRoute;
  busy: boolean;
  error: string | null;
  onExit: () => Promise<void>;
}) {
  const hotline = route.support?.chinaMentalHealthHotline ?? "12356";
  const emergency = route.support?.emergency?.length ? route.support.emergency : ["110", "120"];

  return (
    <main id="main" className="stage-page safety-stop-page">
      <section className="safety-stop-card" aria-labelledby="safety-stop-title">
        <div className="safety-stop-mark" aria-hidden="true">止</div>
        <p className="section-label">个性化流程已停止</p>
        <h1 id="safety-stop-title">这里先停下，不把危机写成故事。</h1>
        <p className="safety-stop-lead">系统读到可能需要即时支持的表达，于是在这里停下。刚才的内容只用于这一次安全判断，会随会话一同删除。</p>
        <div className="support-path" aria-label="可以立即联系的支持">
          <section><span>心理援助</span><h2>拨打全国统一心理援助热线</h2><a className="support-number" href={`tel:${hotline}`}>{hotline}</a></section>
          <section><span>紧急危险</span><h2>如果你或他人可能马上受伤</h2><div className="emergency-links">{emergency.map((number) => <a key={number} href={`tel:${number}`}>拨打 {number}</a>)}</div></section>
        </div>
        <p className="monitoring-note">本服务没有真人实时监控，也不能代替急救或专业支持。可以立即联系身边信任的人，请对方陪你一起求助。</p>
        <StatusMessage error={error} />
        <button className="danger-action" type="button" disabled={busy} onClick={() => void onExit()}>{busy ? "正在删除本次会话…" : "删除本次会话并退出"}</button>
      </section>
    </main>
  );
}

function StoryExplanation({
  card,
  coverUrl,
  busy,
  onClose,
  onConfirm,
  onCoverError,
}: {
  card: StoryCard;
  coverUrl?: string | null;
  busy: boolean;
  onClose: () => void;
  onConfirm: (card: StoryCard) => Promise<void>;
  onCoverError: () => void;
}) {
  const explanation = card.explanation;
  return (
    <section id="story-explanation" className="story-explanation" aria-labelledby="story-explanation-title">
      <button className="story-explanation-close" type="button" onClick={onClose} aria-label="关闭故事详解">×</button>
      <div className="story-explanation-art">
        <StoryIllustration
          family={card.illustrationKey ?? card.storyFamilyId}
          title={toSimplifiedDisplay(card.title)}
          imageUrl={coverUrl}
          onImageError={onCoverError}
        />
      </div>
      <div className="story-explanation-copy">
        <p className="section-label">故事详解</p>
        <h2 id="story-explanation-title">{toSimplifiedDisplay(card.title)}</h2>
        <p className="story-preparation-note">以下情节与出处依据原文整理，请核对后再决定是否进入共谱。</p>
        <p className="story-explanation-overview">{toSimplifiedDisplay(explanation?.overview ?? card.summary)}</p>
        <div className="plot-beats">
          <h3>原典情节线</h3>
          <ol>{(explanation?.plotBeats ?? [card.summary]).map((beat, index) => <li key={`${beat}-${index}`}><span>{index + 1}</span><p>{toSimplifiedDisplay(beat)}</p></li>)}</ol>
        </div>
        <aside className="recommendation-explained"><strong>为什么推荐</strong><p>{toSimplifiedDisplay(card.recommendationReason ?? card.possibleResonance)}</p><small>你的线索：{toSimplifiedDisplay(card.recommendationBasis?.userSignal ?? "你确认的摘要")}；故事线索：{toSimplifiedDisplay(card.recommendationBasis?.storySignal ?? "故事中的相关情节")}</small></aside>
        <StorySource card={card} />
        <div className="story-explanation-actions">
          <button className="secondary-action" type="button" onClick={onClose}>返回比较其他故事</button>
          <button className="primary-action compact-action" type="button" disabled={busy} onClick={() => void onConfirm(card)}>{busy ? "正在准备故事…" : "确认这则故事，进入共谱"}</button>
        </div>
      </div>
    </section>
  );
}

export function EncounterStage({
  sessionId,
  allowPrivateText,
  brief,
  offer,
  busy,
  error,
  notice,
  rejectedAll,
  offersExhausted,
  onCreateBrief,
  onConversationReply,
  onConfirmBrief,
  onRetryOffers,
  onRefresh,
  onRejectAll,
  onSelect,
  onExit,
}: {
  sessionId?: string;
  allowPrivateText: boolean;
  brief: ExperienceBrief | null;
  offer: StoryOffer | null;
  busy: boolean;
  error: string | null;
  notice: string | null;
  rejectedAll: boolean;
  offersExhausted: boolean;
  onCreateBrief: (input: { mode: BriefInputMode; text?: string; presetId?: string }) => Promise<ExperienceBrief | null | void>;
  onConversationReply?: (message: string, history: ConversationMessage[]) => Promise<ConversationTurnResponse | null>;
  onConfirmBrief: (summary: string) => Promise<void>;
  onRetryOffers: () => Promise<void>;
  onRefresh: () => Promise<void>;
  onRejectAll: () => Promise<void>;
  onSelect: (card: StoryCard) => Promise<void>;
  onExit: () => Promise<void>;
}) {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: "assistant-0", role: "assistant", text: "请讲一件最近真实发生、你愿意分享的小事。" },
  ]);
  const [followUpOptions, setFollowUpOptions] = useState<string[]>([]);
  const [summary, setSummary] = useState(brief?.neutralSummary ?? "");
  const [selectedId, setSelectedId] = useState("");
  const [detailCard, setDetailCard] = useState<StoryCard | null>(null);
  const [guidanceComplete, setGuidanceComplete] = useState(false);
  const [turnBudget, setTurnBudget] = useState({ used: 0, total: MAX_GUIDANCE_TURNS });
  const [modelSummary, setModelSummary] = useState<string | null>(null);
  const [covers, setCovers] = useState<Record<string, string | null>>({});

  useEffect(() => {
    // The brief created earlier in the same send only carries the user's own
    // sentences pasted together. Once the closing turn hands back 栖蝶's
    // summary, that is what the shared notes must keep showing.
    if (modelSummary !== null) return;
    if (brief) setSummary(brief.neutralSummary);
  }, [brief, modelSummary]);

  useEffect(() => {
    setSelectedId("");
    setDetailCard(null);
  }, [offer?.id]);

  const preparedCards = useMemo(
    () => offer?.cards.map(prepareStoryForExperience) ?? [],
    [offer],
  );
  const selectedCard = useMemo(
    () => preparedCards.find((card) => card.storyVersionId === selectedId) ?? null,
    [preparedCards, selectedId],
  );
  const safetyStop = getSafetyStopRoute(brief);
  const userMessages = messages.filter((message) => message.role === "user");

  // Generated card headers, keyed by story. `null` means asked-and-unavailable,
  // which is not an error: the local line-drawing is a complete fallback.
  useEffect(() => {
    if (!sessionId) return;
    const pending = preparedCards
      .map((card) => card.storyVersionId)
      .filter((storyVersionId) => covers[storyVersionId] === undefined);
    if (pending.length === 0) return;
    let cancelled = false;
    // Claim them up front so a re-render cannot queue a second request for a
    // card whose first one is still in flight.
    setCovers((current) => {
      const next = { ...current };
      for (const storyVersionId of pending) next[storyVersionId] = null;
      return next;
    });
    void Promise.all(pending.map(async (storyVersionId) => {
      try {
        const cover = await createStoryCover(sessionId, storyVersionId);
        if (!cancelled && cover.status === "ready" && cover.imageUrl) {
          setCovers((current) => ({ ...current, [storyVersionId]: cover.imageUrl }));
        }
      } catch {
        // Keep the local drawing; a missing header must never block the offer.
      }
    }));
    return () => { cancelled = true; };
  }, [covers, preparedCards, sessionId]);

  function dropCover(storyVersionId: string) {
    setCovers((current) => ({ ...current, [storyVersionId]: null }));
  }

  async function sendMessage() {
    const nextText = input.trim();
    if (nextText.length < 4 || busy) return;
    const nextUserMessages = [...userMessages.map((message) => message.text), nextText];
    setMessages((current) => [...current, { id: `user-${Date.now()}`, role: "user", text: nextText }]);
    setInput("");
    setFollowUpOptions([]);
    const aggregate = nextUserMessages.join("；").slice(0, 980);
    const nextBrief = await onCreateBrief({ mode: "text", text: aggregate });
    if (nextBrief && !getSafetyStopRoute(nextBrief)) {
      const response = await onConversationReply?.(
        nextText,
        messages.map((message) => ({ role: message.role, text: message.text })),
      );
      const complete = response?.guidanceComplete === true
        || nextUserMessages.length >= (response?.turnBudget ?? MAX_GUIDANCE_TURNS);
      setGuidanceComplete(complete);
      setTurnBudget({
        used: response?.turnsUsed ?? nextUserMessages.length,
        total: response?.turnBudget ?? MAX_GUIDANCE_TURNS,
      });
      const turnSummary = response?.summary?.trim();
      if (complete && turnSummary) {
        setModelSummary(turnSummary);
        setSummary(turnSummary);
      }
      // Once guidance closes the assistant stops asking, so drop the
      // follow-up prompts rather than inviting another round.
      setFollowUpOptions(complete ? [] : responseFollowUpOptions(response));
      const fallback = assistantFollowUp(nextUserMessages.length);
      const acknowledgement = response?.acknowledgement?.trim() || fallback.acknowledgement;
      const followUpQuestion = complete
        ? ""
        : response?.followUpQuestion?.trim() || fallback.followUpQuestion;
      const reply = response?.reply?.trim()
        || (followUpQuestion ? `${acknowledgement}\n\n${followUpQuestion}` : acknowledgement);
      setMessages((current) => [...current, {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        text: reply,
        acknowledgement: response ? response.acknowledgement?.trim() : acknowledgement,
        followUpQuestion: complete ? undefined : (response ? response.followUpQuestion?.trim() : followUpQuestion),
        source: response?.source,
      }]);
    }
  }

  if (safetyStop) return <SafetyStopView route={safetyStop} busy={busy} error={error} onExit={onExit} />;

  return (
    <main id="main" className="stage-page encounter-page encounter-chat-page">
      <header className="stage-intro encounter-intro">
        <div><p className="section-label">相遇</p><h1>从最近发生的一件小事说起。</h1></div>
        <p>不必讲得完整；栖蝶会先回应你刚说的，再顺着其中的细节追问，并整理一份由你确认的摘要。</p>
      </header>

      <StatusMessage error={error} notice={notice} />

      {!brief?.confirmed && (
        <section className="encounter-chat-layout" aria-labelledby="conversation-title">
          <div className="conversation-panel">
            <div className="conversation-toolbar"><div><QidieAvatar className="assistant-presence" /><div><h2 id="conversation-title">和栖蝶聊一段</h2><small>{allowPrivateText ? "可以自由对话，也可随时回改" : "当前使用结构化追问"}</small></div></div><span className="conversation-status">本次会话暂存</span></div>
            <div className="chat-transcript" role="log" aria-live="polite" aria-label="与栖蝶的叙事对话">
              {messages.map((message) => {
                const completeAssistantTurn = message.role === "assistant"
                  && Boolean(message.acknowledgement)
                  && Boolean(message.followUpQuestion);
                return (
                  <div key={message.id} className={`chat-bubble chat-${message.role}`}>
                    <span>{message.role === "assistant" ? <><QidieAvatar size={24} /> 栖蝶</> : "你"}</span>
                    {completeAssistantTurn ? (
                      <div className="chat-assistant-turn">
                        <p className="chat-acknowledgement">{toSimplifiedDisplay(message.acknowledgement)}</p>
                        <p className="chat-follow-up-question">{toSimplifiedDisplay(message.followUpQuestion)}</p>
                      </div>
                    ) : <p>{message.role === "assistant" ? toSimplifiedDisplay(message.text) : message.text}</p>}
                  </div>
                );
              })}
              {busy && <div className="chat-bubble chat-assistant chat-thinking"><span><QidieAvatar size={24} /> 栖蝶</span><p>正在整理这段线索…</p></div>}
            </div>
            {userMessages.length === 0 && (
              <div className="conversation-starters" aria-label="讲述这件事的句式起点">
                <span>可以从一句话开始</span>
                {STORY_STARTERS.map((starter) => <button key={starter.id} type="button" onClick={() => setInput(starter.label)}>{starter.label}</button>)}
              </div>
            )}
            {userMessages.length > 0 && followUpOptions.length > 0 && (
              <div className="conversation-starters" aria-label="栖蝶根据这件事给出的继续讲述方向">
                <span>可以接着说</span>
                {followUpOptions.map((option) => <button key={option} type="button" onClick={() => setInput(option)}>{option}</button>)}
              </div>
            )}
            {guidanceComplete ? (
              <div className="conversation-closed" role="status">
                <p><strong>这些已经够用了。</strong>右边的摘要可以直接改，确认后就去找故事。</p>
                <button className="text-action" type="button" onClick={() => setGuidanceComplete(false)}>还想再补充一点</button>
              </div>
            ) : (
              <form className="chat-composer" onSubmit={(event) => { event.preventDefault(); void sendMessage(); }}>
                <label htmlFor="story-chat-input" className="sr-only">回复栖蝶</label>
                <textarea id="story-chat-input" rows={3} maxLength={320} value={input} disabled={busy} onChange={(event) => setInput(event.target.value)} placeholder="写下此刻愿意分享的部分；请不要填写姓名、联系方式或单位。" />
                <div>
                  <small>
                    {input.length}/320 · 回车换行，点击发送
                    {turnBudget.used > 0 && ` · 已聊 ${turnBudget.used}/${turnBudget.total} 轮`}
                  </small>
                  <button className="primary-action compact-action" type="submit" disabled={input.trim().length < 4 || busy}>发送</button>
                </div>
              </form>
            )}
          </div>

          <aside className={`conversation-notes ${brief ? "has-notes" : ""}`} aria-labelledby="notes-title">
            <p className="section-label">可共同修改</p>
            <h2 id="notes-title">栖蝶听到的线索</h2>
            {brief && guidanceComplete ? (
              <>
                <p>栖蝶聊完后整理的摘要，只用于下一步检索。可以直接改。</p>
                <label className="field-block summary-field"><span>可编辑摘要 <small>{summary.length}/240</small></span><textarea rows={7} maxLength={240} value={summary} onChange={(event) => setSummary(event.target.value)} /></label>
                <button className="primary-action compact-action" type="button" disabled={summary.trim().length < 6 || busy} onClick={() => void onConfirmBrief(summary.trim())}>{busy ? "正在检索故事库…" : "摘要准确，生成故事推荐"}<span aria-hidden="true">→</span></button>
              </>
            ) : (
              <div className="empty-notes">
                <i aria-hidden="true" />
                <p>{turnBudget.used > 0
                  ? `聊到第 ${turnBudget.used}/${turnBudget.total} 轮。栖蝶问完后，这里会出现一份由它整理的摘要。`
                  : "聊过几轮后，栖蝶会在这里整理一份可编辑摘要。你决定何时用它寻找故事。"}</p>
              </div>
            )}
          </aside>
        </section>
      )}

      {busy && brief?.confirmed && !offer && <RetrievalProgress activeStep={2} />}
      {busy && !brief?.confirmed && guidanceComplete && <RetrievalProgress activeStep={0} />}

      {brief?.confirmed && !offer && !rejectedAll && !busy && (
        <section className="no-match recovery-panel" aria-labelledby="offer-retry-title"><p className="section-label">故事卡尚未载入</p><h2 id="offer-retry-title">摘要已确认，故事卡还没有取到。</h2><p>你的确认依然有效。可以重试取得故事卡，或结束并删除会话。</p><div className="action-row"><button className="secondary-action" type="button" disabled={busy} onClick={() => void onRetryOffers()}>{busy ? "正在重新取得…" : "重新取得故事卡"}</button><button className="text-action danger-text" type="button" disabled={busy} onClick={() => void onExit()}>结束并删除</button></div></section>
      )}

      {brief?.confirmed && rejectedAll && !offer && (
        <section className="no-match" aria-live="polite"><p className="section-label">{offersExhausted ? "本轮浏览完成" : "没有选中故事"}</p><h2>{offersExhausted ? "本次可浏览的故事已经看完。" : "这批故事都不合适。"}</h2><p>{offersExhausted ? "本轮浏览已结束。" : "可以再看一批，或结束并删除会话。"}</p><div className="action-row">{!offersExhausted && <button className="secondary-action" type="button" disabled={busy} onClick={() => void onRefresh()}>再看一批</button>}<button className="text-action danger-text" type="button" disabled={busy} onClick={() => void onExit()}>结束并删除</button></div></section>
      )}

      {offer && offer.cards.length === 0 && (
        <section className="no-match" aria-live="polite"><p className="section-label">{offer.exhausted ? "本轮浏览完成" : "故事卡暂时为空"}</p><h2>{offer.exhausted ? "本次可浏览的故事已经看完。" : "这次取到的故事卡是空的。"}</h2><p>{offer.exhausted ? "已看过和已拒绝的故事都留在原地。" : "可以重新取得故事卡。"}</p><div className="action-row">{!offer.exhausted && <button className="secondary-action" type="button" disabled={busy} onClick={() => void onRetryOffers()}>重新取得故事卡</button>}<button className="text-action danger-text" type="button" disabled={busy} onClick={() => void onExit()}>结束并删除</button></div></section>
      )}

      {offer && offer.cards.length > 0 && !detailCard && (
        <section className="story-offer redesigned-story-offer" aria-labelledby="offer-title">
          <div className="offer-heading"><div><p className="section-label">故事推荐</p><h2 id="offer-title">请选择一则想进一步了解的故事。</h2></div></div>
          {offer.exhausted && <p className="offer-boundary" role="status">这是本次可浏览范围的最后一批，每一则都是首次出现。</p>}
          <div className="illustrated-story-grid">
            {preparedCards.map((card) => {
              const selected = card.storyVersionId === selectedId;
              const firstReference = card.sourceCanon.references[0];
              return (
                <article
                  key={card.storyVersionId}
                  className={`illustrated-story-card story-card-tilt ${selected ? "selected" : ""}`}
                >
                  {/* The whole card is the control: clicking it selects. The radio
                      stays for keyboard and screen-reader users. */}
                  <label className="story-card-choice">
                    <input
                      type="radio"
                      name="story"
                      checked={selected}
                      onChange={() => setSelectedId(card.storyVersionId)}
                    />
                    <span className="sr-only">选择《{toSimplifiedDisplay(card.title)}》</span>
                  </label>
                  <button
                    type="button"
                    className="story-card-hit"
                    aria-pressed={selected}
                    aria-label={`选择《${toSimplifiedDisplay(card.title)}》`}
                    onClick={() => setSelectedId(card.storyVersionId)}
                  />
                  <span className="story-card-selected-mark" aria-hidden="true">{selected ? "已选" : ""}</span>
                  <StoryIllustration
                    family={card.illustrationKey ?? card.storyFamilyId}
                    title={toSimplifiedDisplay(card.title)}
                    imageUrl={covers[card.storyVersionId]}
                    onImageError={() => dropCover(card.storyVersionId)}
                  />
                  <div className="illustrated-story-copy">
                    <h3>{toSimplifiedDisplay(card.title)}</h3>
                    <p className="story-card-origin"><span>原典出处</span><strong>{toSimplifiedDisplay(storySourceLabel(card))}</strong></p>
                    <div className="recommendation-reason"><span>为什么推荐</span><p>{toSimplifiedDisplay(storyRecommendationText(card))}</p></div>
                    <details className="source-details story-card-source">
                      <summary>查看梗概与出处</summary>
                      <p className="story-card-summary">{toSimplifiedDisplay(card.summary)}</p>
                      {card.motifs.length > 0 && <ul className="motif-chips">{card.motifs.slice(0, 3).map((motif) => <li key={motif}>{toSimplifiedDisplay(motif)}</li>)}</ul>}
                      <p>{toSimplifiedDisplay(card.subtitle ?? storySourceLabel(card))}</p>
                      {/* Content warnings are disclosed here rather than on the face of
                          the card: surfacing them unprompted frames every story as risky. */}
                      {card.contentWarnings.length > 0 && (
                        <p className="content-warning"><strong>内容提示</strong>{toSimplifiedDisplay(card.contentWarnings.join("、"))}</p>
                      )}
                      {firstReference ? (
                        firstReference.url ? (
                          <a href={firstReference.url} target="_blank" rel="noreferrer">{toSimplifiedDisplay(formatSourceReference(firstReference))}</a>
                        ) : <small>{toSimplifiedDisplay(formatSourceReference(firstReference))}</small>
                      ) : null}
                    </details>
                  </div>
                </article>
              );
            })}
          </div>
          <div className="offer-actions"><div><button className="secondary-action" type="button" disabled={busy || Boolean(offer.exhausted)} onClick={() => void onRefresh()}>换一批</button><button className="text-action" type="button" disabled={busy} onClick={() => void onRejectAll()}>都不合适</button></div><button className="primary-action compact-action" type="button" disabled={!selectedCard || busy} onClick={() => selectedCard && setDetailCard(selectedCard)}>查看选中故事 <span aria-hidden="true">→</span></button></div>
        </section>
      )}

      {detailCard && (
        <StoryExplanation
          card={detailCard}
          coverUrl={covers[detailCard.storyVersionId]}
          busy={busy}
          onClose={() => setDetailCard(null)}
          onConfirm={onSelect}
          onCoverError={() => dropCover(detailCard.storyVersionId)}
        />
      )}
    </main>
  );
}
