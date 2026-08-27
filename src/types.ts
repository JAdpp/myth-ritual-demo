export type ExperienceStage =
  | "welcome"
  | "encounter"
  | "articulation"
  | "ritualization"
  | "artifact";

export interface SessionConsent {
  adultConfirmed: boolean;
  adultContentOptIn: boolean;
  nonClinicalAcknowledged: boolean;
  cloudProcessingAccepted: boolean;
}

export interface ExperienceSession {
  id: string;
  status: string;
  corpusVersion: string;
  expiresAt?: string;
}

export interface CorpusGenreCount {
  name: string;
  count: number;
}

export interface CorpusWorkCount {
  sourceWorkId: string;
  title: string;
  count: number;
}

export interface CorpusOverview {
  /** All source-library stories that can appear in recommendations. */
  recommendationPoolStories?: number;
  /** Stories whose explanation and mapping anchors were prepared in advance. */
  curatedExperienceStories?: number;
  /** Compatibility name returned by the current API for prepared C3 stories. */
  deepAnnotatedStories?: number;
  /** C1 source candidates plus all C3 prepared candidates. */
  totalRecommendationCandidates?: number;
  /** Total candidate records with sensitive C3 stories kept off by default. */
  defaultTotalRecommendationCandidates?: number;
  /** Product-facing count: one adopted main text per experience story. */
  productStories?: number;
  /** Audit-facing C0 evidence count; these are not presented as story choices. */
  sourceWitnesses?: number;
  /** @deprecated Use productStories for product copy. */
  storyFamilies: number;
  /** @deprecated Legacy runtime-record count; use productStories for product copy; sourceWitnesses is audit-only. */
  storyVersions: number;
  /** @deprecated Compatibility alias for catalogUniqueStories. */
  catalogEntries?: number;
  /** Exact-hash-deduplicated, accepted C1 single-story units. */
  catalogUniqueStories?: number;
  /** Raw source segments inspected before rejection and exact deduplication. */
  catalogRawSegments?: number;
  catalogRejectedSegments?: number;
  /** C1 stories whose current source rights permit retained full text. */
  catalogFullTextStories?: number;
  catalogSourceWorks?: number;
  /** Scope of completed deduplication; near-variant review remains separate. */
  catalogDedupeScope?: string | null;
  /** Small aggregate only; the API never sends the 10k+ C1 story rows. */
  catalogWorks?: CorpusWorkCount[];
  catalogMetadataOnlyEntries?: number;
  catalogStatusCounts?: Record<string, number>;
  catalogGroups?: CorpusGenreCount[];
  catalogSnapshotDate?: string | null;
  familyIds: string[];
  genres: CorpusGenreCount[];
  storyTypes?: CorpusGenreCount[];
  eraLabels: string[];
  snapshotDate?: string | null;
  reviewStatus: "development_dual_review_pending" | string;
  productionEligibleVersions: number;
}

export interface HealthStatus {
  status: string;
  service: string;
  corpusVersion: string;
  eligibleC3Records: number;
  corpusOverview: CorpusOverview;
  model?: string;
  liveModelEnabled: boolean;
}

export type BriefInputMode = "text" | "preset";

export interface SafetySupport {
  chinaMentalHealthHotline?: string;
  emergency?: string[];
  realTimeMonitoring?: boolean;
}

export interface SafetyStopRoute {
  blocked: true;
  route: "crisis_stop";
  categories: string[];
  blockedAt?: string;
  support?: SafetySupport;
}

export type ExperienceBriefSafetyRoute =
  | "standard"
  | "preset_only"
  | "stopped"
  | SafetyStopRoute;

export interface ExperienceBrief {
  id: string;
  version: number;
  inputMode: BriefInputMode;
  originalText?: string;
  presetId?: string;
  neutralSummary: string;
  confirmed: boolean;
  safetyRoute?: ExperienceBriefSafetyRoute;
}

export interface ConversationMessage {
  role: "assistant" | "user";
  text: string;
}

export interface ConversationTurnResponse {
  phase: "encounter";
  reply: string;
  /** A brief, grounded response that shows what the assistant heard before it asks again. */
  acknowledgement?: string;
  /** The single question that gently carries the conversation forward. */
  followUpQuestion?: string;
  source: "deepseek" | "model_adapter" | "deterministic_fallback";
  modelVersion?: string | null;
  followUpOptions?: string[];
  /** How many user turns have been spent, and the cap before guidance closes. */
  turnsUsed?: number;
  turnBudget?: number;
  /** True on the closing turn: no further question, summary is ready. */
  guidanceComplete?: boolean;
  summarySource?: "deepseek" | "model_adapter" | "deterministic_fallback" | null;
  /** 栖蝶's summary of the whole conversation. Only the closing turn returns it. */
  summary?: string | null;
}

export interface SourceReference {
  id: string;
  title: string;
  workTitle?: string;
  author?: string;
  era?: string;
  edition?: string;
  locator: string;
  url?: string;
  rights?: string;
}

export interface SourceCanon {
  storyVersionId: string;
  title: string;
  sourceTitle: string;
  excerpt: string;
  originalEnding: string;
  motifs: string[];
  mustKeep: string[];
  allowedTransformations: string[];
  prohibitedChanges: string[];
  references: SourceReference[];
}

/**
 * A recommendation can come straight from the large source library.  Those
 * candidates intentionally carry only what the choice screen needs; selecting
 * one lets the service prepare the richer fields used by the mapping stage.
 */
export interface StoryCandidate {
  storyVersionId: string;
  storyFamilyId: string;
  title: string;
  subtitle?: string;
  summary?: string;
  characters?: string[];
  conflict?: string;
  motifs?: string[];
  imagery?: string[];
  emotionalArc?: string;
  possibleResonance?: string;
  mayNotFit?: string;
  contentWarnings?: string[];
  recommendationReason?: string;
  recommendationBasis?: {
    userSignal: string;
    storySignal: string;
    mode: "source_grounded_theme_match" | string;
    modelGenerated: boolean;
  };
  illustrationKey?: string;
  explanation?: {
    overview: string;
    plotBeats: string[];
    sourceVersion: string;
    editorialStatus: string;
  };
  sourceCanon?: Partial<SourceCanon>;
  /** `prepared` has a pre-written explanation; `on_demand` is prepared after selection. */
  experienceMode?: "prepared" | "on_demand" | string;
}

/** A story whose missing source-library fields have been safely filled for the experience. */
export interface StoryCard extends StoryCandidate {
  summary: string;
  characters: string[];
  conflict: string;
  motifs: string[];
  imagery: string[];
  emotionalArc: string;
  possibleResonance: string;
  mayNotFit: string;
  contentWarnings: string[];
  sourceCanon: SourceCanon;
}

export interface StoryOffer {
  id: string;
  cards: StoryCandidate[];
  corpusVersion: string;
  exhausted?: boolean;
}

export type BranchNodeId =
  | "world_crack"
  | "cross_threshold"
  | "allies_resources"
  | "new_understanding"
  | "bring_back";

export interface BranchNodeDraft {
  id: BranchNodeId;
  title: string;
  prompt: string;
  value: string;
  skipped: boolean;
  suggestions: string[];
  expressionOrigin?: "user" | "model_edited";
}

export type HopeAnchorType = "action" | "relationship" | "meaning" | "open";

export interface HopeAnchor {
  type: HopeAnchorType;
  detail: string;
}

export interface UserBranchVersion {
  id: string;
  version: number;
  parentVersionId?: string;
  selectedStoryVersionId: string;
  nodes: BranchNodeDraft[];
  hopeAnchor?: HopeAnchor;
  preview: string;
  status: "draft" | "approved";
  etag?: string;
  generationMode?: string;
  modelGenerated?: boolean;
}

export interface BranchSuggestionResponse {
  branch?: UserBranchVersion;
  nodeId?: BranchNodeId;
  suggestions: string[];
  assistantMessage?: string;
  nodeUpdates?: Array<{
    nodeId: BranchNodeId;
    value: string;
    rationale?: string;
  }>;
  suggestionSource?: string;
  modelGenerated?: boolean;
}

export interface TheatreAct {
  id: string;
  title: string;
  sceneTitle?: string;
  subtitle?: string;
  narration: string;
  dialogue?: string;
  stageDirection: string;
  durationSeconds: number;
  setting?: string;
  mood?: string;
  audioUrl?: string;
  sourceNodeIds?: string[];
}

export interface TheatreScript {
  id: string;
  version: number;
  branchVersionId: string;
  title: string;
  acts: TheatreAct[];
  totalDurationSeconds: number;
  finalLineSuggestions: string[];
}

export interface TheatreSceneImage {
  scriptId?: string;
  actId: string;
  status: "ready" | "fallback";
  imageUrl: string | null;
  altText: string;
  message: string | null;
  retryable: boolean;
  generationSource?: "aliyun_image_model" | "local_stage_fallback" | string;
  fallbackReason?: string | null;
  createdAt?: string;
}

/** The generated ink line-drawing that heads a story card. */
export interface StoryCoverImage {
  storyVersionId: string;
  status: "ready" | "fallback";
  imageUrl: string | null;
  altText: string;
  message: string | null;
  retryable: boolean;
  generationSource?: "aliyun_image_model" | "local_card_fallback" | string;
  fallbackReason?: string | null;
  createdAt?: string;
}

export type RitualGesture = "seal" | "light";

export interface RitualArtifact {
  id: string;
  title: string;
  finalLine: string;
  ritualGesture: RitualGesture;
  saved: boolean;
  createdAt: string;
  cardImageUrl?: string;
}

export type ProvenanceOrigin =
  | "source_canon"
  | "user_created"
  | "model_expression"
  | "interpretive_bridge";

export interface ProvenanceEntry {
  id: string;
  segment: string;
  text: string;
  origin: ProvenanceOrigin;
  sourceTitle?: string;
  sourceLocator?: string;
}

export interface ProvenanceLedger {
  sessionId: string;
  corpusVersion: string;
  modelVersion?: string;
  sourceCanon: SourceCanon | null;
  entries: ProvenanceEntry[];
}

export interface DeletionReceipt {
  sessionId: string;
  deleted: true;
  deletedAt: string;
  deletionProof: string;
}

export interface ApiProblem {
  code?: string;
  message?: string;
  detail?: unknown;
}

export interface TheatreActNarration {
  scriptId: string;
  actId: string;
  status: "ready" | "fallback";
  audioUrl: string | null;
  message: string | null;
  retryable: boolean;
  createdAt?: string;
}
