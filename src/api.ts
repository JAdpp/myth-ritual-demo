import type {
  BranchNodeDraft,
  BranchNodeId,
  BranchSuggestionResponse,
  BriefInputMode,
  ConversationMessage,
  ConversationTurnResponse,
  DeletionReceipt,
  ExperienceBrief,
  ExperienceSession,
  HopeAnchor,
  HealthStatus,
  ProvenanceLedger,
  RitualArtifact,
  RitualGesture,
  SessionConsent,
  StoryCandidate,
  StoryCoverImage,
  StoryOffer,
  TheatreActNarration,
  TheatreSceneImage,
  TheatreScript,
  UserBranchVersion,
} from "./types";

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();
export const API_BASE_URL = configuredBaseUrl || "";

const DEFAULT_REQUEST_TIMEOUT_MS = 45_000;
const MODEL_REQUEST_TIMEOUT_MS = 90_000;
const MEDIA_REQUEST_TIMEOUT_MS = 120_000;
const pendingIdempotencyKeys = new Map<string, string>();

type RequestOptions = {
  timeoutMs?: number;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function extractProblemMessage(payload: unknown): string | null {
  if (typeof payload === "string" && payload.trim()) return payload;
  if (!isRecord(payload)) return null;
  if (typeof payload.message === "string") return payload.message;
  if (typeof payload.detail === "string") return payload.detail;
  if (isRecord(payload.error)) return extractProblemMessage(payload.error);
  if (Array.isArray(payload.detail)) {
    const messages = payload.detail
      .map((item) => {
        if (!isRecord(item) || typeof item.msg !== "string") return null;
        const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
        return location ? `${location}: ${item.msg}` : item.msg;
      })
      .filter((message): message is string => Boolean(message));
    return messages.length ? messages.join("；") : null;
  }
  return null;
}

const USER_FACING_PROBLEM_MESSAGES: Record<string, string> = {
  session_not_found: "本次会话已经结束或删除，请重新进入体验。",
  safety_blocked: "个性化流程已经停止，请使用页面提供的即时支持信息。",
  brief_missing: "请先完成一轮相遇对话。",
  brief_not_confirmed: "请先核对并确认对话摘要。",
  summary_required: "请写下一份非空摘要后再确认。",
  story_offer_missing: "故事卡尚未准备好，请重新取得一组。",
  story_not_offered: "这则故事不在当前推荐中，请重新选择。",
  story_not_selected: "请先选择一则故事再进入共谱。",
  story_no_longer_eligible: "这则故事暂时无法继续使用，请换一则。",
  adult_content_opt_in_required: "这则故事需要先确认敏感内容说明。",
  branch_missing: "共谱初稿尚未建立，请返回上一步重试。",
  branch_not_approved: "请先确认共谱画布，再进入再演。",
  branch_not_found: "没有找到这份共谱内容，请返回上一步重试。",
  branch_version_conflict: "画布已经更新，请刷新后再试。",
  parent_version_conflict: "画布已经更新，请刷新后再试。",
  etag_conflict: "内容已在别处更新，请刷新后再试。",
  theatre_missing: "多幕剧本尚未生成，请先完成共谱。",
  theatre_script_not_found: "没有找到这份多幕剧本，请重新生成。",
  theatre_act_not_found: "没有找到这一幕，请返回剧场重试。",
  source_integrity_error: "原典来源校验未通过，已停止继续生成。",
  source_canon_mutated: "原典内容发生异常变化，已停止继续生成。",
};

function userFacingProblemMessage(payload: unknown, status: number, code?: string): string {
  if (code && USER_FACING_PROBLEM_MESSAGES[code]) {
    return USER_FACING_PROBLEM_MESSAGES[code];
  }
  const rawMessage = extractProblemMessage(payload);
  if (rawMessage && !/[A-Za-z]/.test(rawMessage)) return rawMessage;
  if (status === 404) return "没有找到所需内容，请返回上一步重试。";
  if (status === 409) return "页面内容已经更新，请刷新后再试。";
  if (status === 422) return "提交的内容格式不正确，请检查后重试。";
  if (status === 403) return "当前操作已停止，请查看页面提示。";
  return `故事服务暂时无法完成请求（状态码 ${status}）。`;
}

export class ApiError extends Error {
  readonly status?: number;
  readonly code?: string;

  constructor(message: string, options: { status?: number; code?: string } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
  }
}

function idempotencySignature(path: string, init?: RequestInit): string | null {
  const method = (init?.method ?? "GET").toUpperCase();
  if (method === "GET" || method === "HEAD") return null;
  return `${method}\n${path}\n${typeof init?.body === "string" ? init.body : ""}`;
}

function createIdempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `mengdie-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function request<T>(
  path: string,
  init?: RequestInit,
  options: RequestOptions = {},
): Promise<T> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  const controller = new AbortController();
  let timedOut = false;
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const callerSignal = init?.signal;
  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  callerSignal?.addEventListener("abort", abortFromCaller, { once: true });

  const signature = idempotencySignature(path, init);
  const idempotencyKey = signature
    ? pendingIdempotencyKeys.get(signature) ?? createIdempotencyKey()
    : null;
  if (signature && idempotencyKey) pendingIdempotencyKeys.set(signature, idempotencyKey);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
        ...init?.headers,
      },
    });
  } catch {
    globalThis.clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", abortFromCaller);
    if (timedOut) {
      throw new ApiError(
        "这一步等待时间较长，连接已暂停。你可以重试；若服务端其实已经完成，同一次提交不会重复执行。",
        { code: "REQUEST_TIMEOUT" },
      );
    }
    throw new ApiError(
      "无法连接故事服务。请确认后端已在 127.0.0.1:8010 启动，然后重试；本页只显示服务的真实返回。",
      { code: "API_UNAVAILABLE" },
    );
  }

  globalThis.clearTimeout(timeout);
  callerSignal?.removeEventListener("abort", abortFromCaller);
  if (signature) pendingIdempotencyKeys.delete(signature);

  if (response.status === 204) return undefined as T;

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const code = isRecord(payload) && typeof payload.code === "string" ? payload.code : undefined;
    throw new ApiError(userFacingProblemMessage(payload, response.status, code), {
      status: response.status,
      code,
    });
  }
  return payload as T;
}

export function createSession(consent: SessionConsent) {
  return request<ExperienceSession>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ consent }),
  });
}

export function getHealth() {
  return request<HealthStatus>("/api/health", { cache: "no-store" });
}

export function createExperienceBrief(
  sessionId: string,
  input: { mode: BriefInputMode; text?: string; presetId?: string },
) {
  return request<ExperienceBrief>(`/api/sessions/${sessionId}/experience-briefs`, {
    method: "POST",
    body: JSON.stringify({
      inputMode: input.mode,
      originalText: input.text,
      presetId: input.presetId,
    }),
  });
}

export function createConversationTurn(
  sessionId: string,
  message: string,
  history: ConversationMessage[],
) {
  return request<ConversationTurnResponse>(
    `/api/sessions/${sessionId}/conversation-turns`,
    {
      method: "POST",
      body: JSON.stringify({ phase: "encounter", message, history: history.slice(-8) }),
    },
    { timeoutMs: MODEL_REQUEST_TIMEOUT_MS },
  );
}

export function confirmExperienceBrief(
  sessionId: string,
  brief: ExperienceBrief,
  neutralSummary: string,
) {
  return request<ExperienceBrief>(`/api/sessions/${sessionId}/experience-briefs`, {
    method: "PATCH",
    body: JSON.stringify({
      briefId: brief.id,
      parentVersion: brief.version,
      neutralSummary,
      confirmed: true,
    }),
  });
}

export function getStoryOffers(
  sessionId: string,
  excludedStoryVersionIds: string[] = [],
  refresh = false,
) {
  return request<StoryOffer>(
    `/api/sessions/${sessionId}/story-offers`,
    {
      method: "POST",
      body: JSON.stringify({ excludedStoryVersionIds, refresh }),
    },
    { timeoutMs: MODEL_REQUEST_TIMEOUT_MS },
  );
}

export function updateStorySelection(
  sessionId: string,
  payload:
    | { action: "select"; offerId: string; storyVersionId: string }
    | { action: "reject_all"; offerId: string },
) {
  return request<{ status: string; selectedStory?: StoryCandidate }>(
    `/api/sessions/${sessionId}/story-selection`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function createBranch(sessionId: string, selectedStoryVersionId: string, nodes: BranchNodeDraft[]) {
  return request<UserBranchVersion>(
    `/api/sessions/${sessionId}/branches`,
    {
      method: "POST",
      body: JSON.stringify({ selectedStoryVersionId, nodes }),
    },
    { timeoutMs: MODEL_REQUEST_TIMEOUT_MS },
  );
}

export function requestNodeSuggestions(
  sessionId: string,
  branch: UserBranchVersion,
  nodeId: BranchNodeId,
  nodes: BranchNodeDraft[],
  hopeAnchor?: HopeAnchor,
  assistantMessage?: string,
) {
  return request<BranchSuggestionResponse>(
    `/api/sessions/${sessionId}/branches`,
    {
      method: "PATCH",
      headers: branch.etag ? { "If-Match": branch.etag } : undefined,
      body: JSON.stringify({
        action: "suggest",
        branchVersionId: branch.id,
        parentVersion: branch.version,
        nodeId,
        nodes,
        hopeAnchor,
        assistantMessage,
      }),
    },
    { timeoutMs: MODEL_REQUEST_TIMEOUT_MS },
  );
}

export function saveBranch(
  sessionId: string,
  branch: UserBranchVersion,
  nodes: BranchNodeDraft[],
  hopeAnchor: HopeAnchor,
  preview: string,
) {
  return request<UserBranchVersion>(`/api/sessions/${sessionId}/branches`, {
    method: "PATCH",
    headers: branch.etag ? { "If-Match": branch.etag } : undefined,
    body: JSON.stringify({
      action: "save",
      branchVersionId: branch.id,
      parentVersion: branch.version,
      nodes,
      hopeAnchor,
      preview,
    }),
  });
}

export function approveBranch(sessionId: string, branch: UserBranchVersion) {
  return request<UserBranchVersion>(
    `/api/sessions/${sessionId}/branches/${branch.id}/approve`,
    {
      method: "POST",
      headers: branch.etag ? { "If-Match": branch.etag } : undefined,
      body: JSON.stringify({ branchVersionId: branch.id, parentVersion: branch.version }),
    },
  );
}

export function createTheatreScript(sessionId: string, branchVersionId: string) {
  return request<TheatreScript>(
    `/api/sessions/${sessionId}/theatre-scripts`,
    {
      method: "POST",
      body: JSON.stringify({ branchVersionId }),
    },
    { timeoutMs: MODEL_REQUEST_TIMEOUT_MS },
  );
}

export function createTheatreSceneImage(
  sessionId: string,
  theatreScriptId: string,
  actId: string,
) {
  return request<TheatreSceneImage>(
    `/api/sessions/${sessionId}/theatre-scripts/${theatreScriptId}/acts/${actId}/scene-image`,
    { method: "POST" },
    { timeoutMs: MEDIA_REQUEST_TIMEOUT_MS },
  );
}

export function createStoryCover(sessionId: string, storyVersionId: string) {
  return request<StoryCoverImage>(
    `/api/sessions/${sessionId}/stories/${storyVersionId}/cover`,
    { method: "POST" },
    { timeoutMs: MEDIA_REQUEST_TIMEOUT_MS },
  );
}

export function createActNarration(
  sessionId: string,
  theatreScriptId: string,
  actId: string,
) {
  return request<TheatreActNarration>(
    `/api/sessions/${sessionId}/theatre-scripts/${theatreScriptId}/acts/${actId}/narration`,
    { method: "POST" },
    { timeoutMs: MEDIA_REQUEST_TIMEOUT_MS },
  );
}

export function completeRitual(
  sessionId: string,
  payload: {
    theatreScriptId: string;
    storyTitle: string;
    finalLine: string;
    ritualGesture: RitualGesture;
    saveArtifact: boolean;
  },
) {
  return request<RitualArtifact>(`/api/sessions/${sessionId}/ritual-actions`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getProvenance(sessionId: string) {
  return request<ProvenanceLedger>(`/api/sessions/${sessionId}/provenance`, {
    cache: "no-store",
  });
}

export function deleteSession(sessionId: string) {
  return request<DeletionReceipt>(`/api/sessions/${sessionId}`, { method: "DELETE" });
}
