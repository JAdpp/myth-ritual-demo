import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  approveBranch,
  completeRitual,
  confirmExperienceBrief,
  createBranch,
  createConversationTurn,
  createExperienceBrief,
  createSession,
  createTheatreScript,
  deleteSession,
  getHealth,
  getProvenance,
  getStoryOffers,
  requestNodeSuggestions,
  saveBranch,
  updateStorySelection,
} from "./api";
import { ArticulationStage } from "./components/ArticulationStage";
import { ConsentScreen } from "./components/ConsentScreen";
import { EncounterStage } from "./components/EncounterStage";
import { ArtifactView, RitualizationStage } from "./components/RitualizationStage";
import { SiteHeader, StoryLoom } from "./components/StoryLoom";
import { getSafetyStopRoute, isDeletionReceiptFor } from "./lib/contracts";
import { buildBranchPreview, prepareStoryForExperience } from "./lib/story";
import type {
  BranchNodeDraft,
  BranchNodeId,
  BranchSuggestionResponse,
  BriefInputMode,
  ConversationMessage,
  ConversationTurnResponse,
  ExperienceBrief,
  ExperienceSession,
  ExperienceStage,
  HopeAnchor,
  HealthStatus,
  ProvenanceLedger,
  RitualArtifact,
  RitualGesture,
  SessionConsent,
  StoryCard,
  StoryOffer,
  TheatreScript,
  UserBranchVersion,
} from "./types";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return "操作没有完成。请检查故事服务后重试。";
}

export function App() {
  const [stage, setStage] = useState<ExperienceStage>("welcome");
  const [consent, setConsent] = useState<SessionConsent | null>(null);
  const [session, setSession] = useState<ExperienceSession | null>(null);
  const [brief, setBrief] = useState<ExperienceBrief | null>(null);
  const [offer, setOffer] = useState<StoryOffer | null>(null);
  const [offersExhausted, setOffersExhausted] = useState(false);
  const [rejectedAll, setRejectedAll] = useState(false);
  const [selectedStory, setSelectedStory] = useState<StoryCard | null>(null);
  const [branch, setBranch] = useState<UserBranchVersion | null>(null);
  const [script, setScript] = useState<TheatreScript | null>(null);
  // Kept at App level so the summary card can reuse the acts' generated art.
  const [sceneImages, setSceneImages] = useState<Record<string, string>>({});
  const [artifact, setArtifact] = useState<RitualArtifact | null>(null);
  const [ledger, setLedger] = useState<ProvenanceLedger | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const safetyStopped = Boolean(getSafetyStopRoute(brief));

  useEffect(() => {
    document.title = "梦蝶记 · 中国古典神话传说与个人经历共谱";
  }, []);

  useEffect(() => {
    const heading = document.querySelector<HTMLElement>("#main h1");
    if (!heading) return;
    heading.tabIndex = -1;
    heading.focus();
  }, [stage, safetyStopped]);

  useEffect(() => {
    let active = true;
    void getHealth()
      .then((nextHealth) => {
        if (active) setHealth(nextHealth);
      })
      .catch(() => {
        // The landing page keeps an explicit offline placeholder. Starting a
        // session will surface the actionable connection error if needed.
      });
    return () => {
      active = false;
    };
  }, []);

  const clearMessages = useCallback(() => {
    setError(null);
    setNotice(null);
  }, []);

  function requireSession() {
    if (!session) throw new Error("会话尚未建立。请返回首页重新开始。");
    return session;
  }

  function resetExperience() {
    setStage("welcome");
    setConsent(null);
    setSession(null);
    setBrief(null);
    setOffer(null);
    setOffersExhausted(false);
    setRejectedAll(false);
    setSelectedStory(null);
    setBranch(null);
    setScript(null);
    setArtifact(null);
    setLedger(null);
    setBusy(false);
    setError(null);
    setNotice(null);
  }

  async function handleBegin(nextConsent: SessionConsent) {
    clearMessages();
    setBusy(true);
    try {
      const nextSession = await createSession(nextConsent);
      setConsent(nextConsent);
      setSession(nextSession);
      setStage("encounter");
      setNotice("私人会话已建立。你可以从愿意分享的部分开始。");
    } catch (startError) {
      setError(errorMessage(startError));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateBrief(
    input: { mode: BriefInputMode; text?: string; presetId?: string },
  ): Promise<ExperienceBrief | null> {
    clearMessages();
    setBusy(true);
    try {
      const nextBrief = await createExperienceBrief(requireSession().id, input);
      setBrief(nextBrief);
      setNotice(null);
      return nextBrief;
    } catch (briefError) {
      setError(errorMessage(briefError));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmBrief(summary: string) {
    if (!brief) return;
    clearMessages();
    setBusy(true);
    try {
      const confirmed = await confirmExperienceBrief(requireSession().id, brief, summary);
      setBrief(confirmed);
      const nextOffer = await getStoryOffers(requireSession().id);
      setOffer(nextOffer);
      setOffersExhausted(Boolean(nextOffer.exhausted));
      setRejectedAll(false);
      setNotice("栖蝶已从机器切分的开发故事池取得候选。卡牌会显示题名、出处与推荐理由；技术演示版尚未逐条完成人工复核。");
    } catch (confirmError) {
      setError(errorMessage(confirmError));
    } finally {
      setBusy(false);
    }
  }

  async function handleConversationReply(
    message: string,
    history: ConversationMessage[],
  ): Promise<ConversationTurnResponse | null> {
    try {
      return await createConversationTurn(requireSession().id, message, history);
    } catch (conversationError) {
      // The encounter keeps its reviewed local follow-up when the optional
      // conversational endpoint is unavailable.
      if (conversationError instanceof ApiError && conversationError.code === "safety_blocked") {
        setError(conversationError.message);
      } else {
        setNotice("云端回应暂时没有到达，栖蝶已用本地追问继续；你写下的内容仍保留。");
      }
      return null;
    }
  }

  async function handleRetryOffers() {
    clearMessages();
    setBusy(true);
    try {
      const nextOffer = await getStoryOffers(requireSession().id);
      setOffer(nextOffer);
      setOffersExhausted(Boolean(nextOffer.exhausted));
      setRejectedAll(false);
      setNotice(
        nextOffer.exhausted
          ? "已取得本次可浏览故事的最后一批。"
          : "故事卡已重新取得，选择权仍在你手上。",
      );
    } catch (offerError) {
      setError(errorMessage(offerError));
    } finally {
      setBusy(false);
    }
  }

  async function handleRefreshOffers() {
    clearMessages();
    setBusy(true);
    try {
      const excluded = offer?.cards.map((card) => card.storyVersionId) ?? [];
      const nextOffer = await getStoryOffers(requireSession().id, excluded, true);
      setOffer(nextOffer);
      setOffersExhausted(Boolean(nextOffer.exhausted));
      setRejectedAll(false);
      setNotice(nextOffer.exhausted ? "已到达本次可浏览范围，这是最后一批。" : "已换一批，上一批已退出当前卡面。");
    } catch (offerError) {
      setError(errorMessage(offerError));
    } finally {
      setBusy(false);
    }
  }

  async function handleRejectAll() {
    if (!offer) return;
    clearMessages();
    setBusy(true);
    try {
      await updateStorySelection(requireSession().id, { action: "reject_all", offerId: offer.id });
      setOffer(null);
      setRejectedAll(true);
      setNotice("已记录“都不合适”，这些故事就此退出本次流程。");
    } catch (selectionError) {
      setError(errorMessage(selectionError));
    } finally {
      setBusy(false);
    }
  }

  async function handleSelectStory(card: StoryCard) {
    if (!offer) return;
    clearMessages();
    setBusy(true);
    try {
      const selection = await updateStorySelection(requireSession().id, {
        action: "select",
        offerId: offer.id,
        storyVersionId: card.storyVersionId,
      });
      const nextStory = prepareStoryForExperience(selection.selectedStory ?? card);
      const nextBranch = await createBranch(requireSession().id, card.storyVersionId, []);
      setSelectedStory(nextStory);
      setBranch(nextBranch);
      setStage("articulation");
      setNotice("栖蝶已依据原文整理好映照初稿。请先浏览，再校对，或通过与栖蝶对话修改。");
    } catch (selectionError) {
      setError(errorMessage(selectionError));
    } finally {
      setBusy(false);
    }
  }

  async function handleSuggest(
    nodeId: BranchNodeId,
    nodes: BranchNodeDraft[],
    hopeAnchor?: HopeAnchor,
    assistantMessage?: string,
  ): Promise<BranchSuggestionResponse> {
    if (!branch) throw new Error("你的故事版本尚未建立。");
    const response = await requestNodeSuggestions(
      requireSession().id,
      branch,
      nodeId,
      nodes,
      hopeAnchor,
      assistantMessage,
    );
    if (response.branch) setBranch(response.branch);
    return response;
  }

  async function handleSaveBranch(nodes: BranchNodeDraft[], hopeAnchor: HopeAnchor) {
    if (!branch) return;
    clearMessages();
    setBusy(true);
    try {
      const nextBranch = await saveBranch(
        requireSession().id,
        branch,
        nodes,
        hopeAnchor,
        buildBranchPreview(nodes, hopeAnchor),
      );
      setBranch(nextBranch);
      setNotice("这版修改已保存，古籍原文保持原样。");
    } catch (saveError) {
      setError(errorMessage(saveError));
    } finally {
      setBusy(false);
    }
  }

  async function handleApproveBranch(nodes: BranchNodeDraft[], hopeAnchor: HopeAnchor) {
    if (!branch) return;
    clearMessages();
    setBusy(true);
    try {
      let approved = branch;
      if (branch.status !== "approved") {
        const saved = await saveBranch(
          requireSession().id,
          branch,
          nodes,
          hopeAnchor,
          buildBranchPreview(nodes, hopeAnchor),
        );
        setBranch(saved);
        approved = await approveBranch(requireSession().id, saved);
        setBranch(approved);
      }

      try {
        const nextScript = await createTheatreScript(requireSession().id, approved.id);
        setScript(nextScript);
        setStage("ritualization");
        setNotice("再演只使用刚刚确认的共谱版本，古籍片段保持只读。");
      } catch (theatreError) {
        setError(
          `这版共谱已确认，但再演暂时没有打开：${errorMessage(theatreError)} 请重试“继续进入再演”。`,
        );
      }
    } catch (approvalError) {
      setError(errorMessage(approvalError));
    } finally {
      setBusy(false);
    }
  }

  async function handleCompleteRitual(payload: {
    title: string;
    finalLine: string;
    gesture: RitualGesture;
    save: boolean;
  }) {
    if (!script) return;
    clearMessages();
    setBusy(true);
    let nextArtifact = artifact;
    try {
      if (!nextArtifact) {
        nextArtifact = await completeRitual(requireSession().id, {
          theatreScriptId: script.id,
          storyTitle: payload.title,
          finalLine: payload.finalLine,
          ritualGesture: payload.gesture,
          saveArtifact: payload.save,
        });
        setArtifact(nextArtifact);
        setScript((current) => {
          if (!current || current.acts.length === 0) return current;
          const lastIndex = current.acts.length - 1;
          return {
            ...current,
            finalLineSuggestions: [
              payload.finalLine,
              ...current.finalLineSuggestions.filter((line) => line !== payload.finalLine),
            ],
            acts: current.acts.map((act, index) => (
              index === lastIndex ? { ...act, dialogue: payload.finalLine } : act
            )),
          };
        });
      }
      const nextLedger = await getProvenance(requireSession().id);
      setLedger(nextLedger);
      setStage("artifact");
      setNotice(null);
    } catch (ritualError) {
      if (nextArtifact) {
        setError(`终幕已经完成，但来源记录暂时没有载入：${errorMessage(ritualError)}`);
        setNotice("再次点击终幕按钮只会补取来源记录，不会重复完成或重复保存纪念卡。");
      } else {
        setError(errorMessage(ritualError));
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    clearMessages();
    setBusy(true);
    try {
      const currentSession = requireSession();
      const receipt = await deleteSession(currentSession.id);
      if (!isDeletionReceiptFor(receipt, currentSession.id)) {
        throw new ApiError("服务没有返回可验证的删除凭证。删除状态保持未确认，请重试。", {
          code: "INVALID_DELETION_RECEIPT",
        });
      }
      resetExperience();
    } catch (deleteError) {
      setError(errorMessage(deleteError));
      setBusy(false);
    }
  }

  return (
    <div className={`app-shell stage-${stage}`}>
      <a className="skip-link" href="#main">跳到主要内容</a>
      <SiteHeader stage={stage} />
      <div>
      {stage !== "welcome" && <StoryLoom stage={stage} />}

      {stage === "welcome" && (
        <ConsentScreen
          busy={busy}
          error={error}
          health={health}
          onBegin={handleBegin}
        />
      )}
      {stage === "encounter" && (
        <EncounterStage
          sessionId={session?.id}
          allowPrivateText={Boolean(consent?.cloudProcessingAccepted && health?.liveModelEnabled)}
          brief={brief}
          offer={offer}
          busy={busy}
          error={error}
          notice={notice}
          rejectedAll={rejectedAll}
          offersExhausted={offersExhausted}
          onCreateBrief={handleCreateBrief}
          onConversationReply={handleConversationReply}
          onConfirmBrief={handleConfirmBrief}
          onRetryOffers={handleRetryOffers}
          onRefresh={handleRefreshOffers}
          onRejectAll={handleRejectAll}
          onSelect={handleSelectStory}
          onExit={handleDelete}
        />
      )}
      {stage === "articulation" && selectedStory && branch && (
        <ArticulationStage
          story={selectedStory}
          branch={branch}
          busy={busy}
          error={error}
          notice={notice}
          onSuggest={handleSuggest}
          onSave={handleSaveBranch}
          onApprove={handleApproveBranch}
        />
      )}
      {stage === "ritualization" && selectedStory && script && (
        <RitualizationStage
          sessionId={session?.id}
          story={selectedStory}
          script={script}
          busy={busy}
          error={error}
          notice={notice}
          onComplete={handleCompleteRitual}
          onSceneImage={(actId, imageUrl) => setSceneImages((current) => ({ ...current, [actId]: imageUrl }))}
        />
      )}
      {stage === "artifact" && artifact && ledger && (
        <ArtifactView artifact={artifact} ledger={ledger} script={script} sceneImages={sceneImages} busy={busy} error={error} onDelete={handleDelete} />
      )}
      </div>
    </div>
  );
}
