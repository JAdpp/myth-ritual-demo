import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { createActNarration, createTheatreSceneImage } from "../api";
import type {
  ProvenanceLedger,
  RitualArtifact,
  RitualGesture,
  StoryCard,
  TheatreAct,
  TheatreSceneImage,
  TheatreScript,
} from "../types";
import { boundedActIndex, BRANCH_NODE_DEFINITIONS } from "../lib/story";
import { toSimplifiedDisplay } from "../lib/display-text";
import { StatusMessage } from "./StoryLoom";
import "../ritual-media.css";

type SceneVisualState = Pick<
  TheatreSceneImage,
  "status" | "imageUrl" | "altText" | "message" | "retryable"
> | {
  status: "idle" | "loading";
  imageUrl: null;
  altText: string;
  message: string | null;
  retryable: boolean;
};

const idleSceneState: SceneVisualState = {
  status: "idle",
  imageUrl: null,
  altText: "本幕纸影舞台",
  message: null,
  retryable: false,
};

type SceneMotif = "opening" | "crack" | "threshold" | "allies" | "turning" | "return" | "closing";

const NODE_SCENE_MOTIFS: Readonly<Record<string, SceneMotif>> = {
  world_crack: "crack",
  cross_threshold: "threshold",
  allies_resources: "allies",
  new_understanding: "turning",
  bring_back: "return",
};

const MOOD_SCENE_MOTIFS: Readonly<Record<string, SceneMotif>> = {
  opening: "opening",
  threshold: "threshold",
  choice: "threshold",
  allies: "allies",
  turning: "turning",
  return: "return",
  closing: "closing",
};

const SCENE_TITLE_MOTIFS: ReadonlyArray<readonly [RegExp, SceneMotif]> = [
  [/尾声|终幕|收束|落幕|余音/, "closing"],
  [/带回|回返|归来|回到现实|回家/, "return"],
  [/理解|转向|醒悟|看见|重新/, "turning"],
  [/同行|盟友|支持|相助|资源/, "allies"],
  [/门槛|跨过|选择|启程|迈出/, "threshold"],
  [/裂缝|变化|失衡|破开|困局/, "crack"],
  [/序幕|开场|相遇|镜子|初见/, "opening"],
];

const SCENE_PERSON_OFFSETS: Readonly<Record<SceneMotif, number>> = {
  opening: -150,
  crack: -20,
  threshold: 90,
  allies: 80,
  turning: 180,
  return: 300,
  closing: 360,
};

function sceneMotifForAct(act: TheatreAct): SceneMotif {
  for (const nodeId of act.sourceNodeIds ?? []) {
    const motif = NODE_SCENE_MOTIFS[nodeId];
    if (motif) return motif;
  }

  const sceneTitle = toSimplifiedDisplay(`${act.sceneTitle ?? ""} ${act.title}`);
  const titleMatch = SCENE_TITLE_MOTIFS.find(([pattern]) => pattern.test(sceneTitle));
  if (titleMatch) return titleMatch[1];

  const mood = act.mood?.trim().toLowerCase();
  return (mood && MOOD_SCENE_MOTIFS[mood]) || "turning";
}

/* The local paper-shadow scene is always available. Generated imagery is a
   replaceable layer, never a gate that can stop the theatre. */
function TheatreScene({
  act,
  actIndex,
  visual,
  onRetry,
  onImageError,
}: {
  act: TheatreAct;
  actIndex: number;
  visual: SceneVisualState;
  onRetry: () => void;
  onImageError: () => void;
}) {
  const motif = sceneMotifForAct(act);
  const sceneTitle = toSimplifiedDisplay(act.sceneTitle ?? act.title);
  const stageDirection = toSimplifiedDisplay(act.stageDirection);
  return (
    <div className={`theatre-scene-composite scene-${motif}`} data-scene-motif={motif}>
      {visual.status === "ready" && visual.imageUrl ? (
        <img
          className="generated-scene-image"
          src={visual.imageUrl}
          alt={toSimplifiedDisplay(visual.altText)}
          decoding="async"
          referrerPolicy="no-referrer"
          onError={onImageError}
        />
      ) : (
        <svg
          className="theatre-scene"
          viewBox="0 0 960 470"
          preserveAspectRatio="xMidYMid slice"
          role="img"
          aria-label={`第 ${actIndex + 1} 幕：${stageDirection}`}
        >
          <polygon className="mountain-back" points="0,330 168,196 316,296 468,164 638,308 812,206 960,312 960,470 0,470" />
          <polygon className="mountain-front" points="0,392 206,282 390,384 566,288 744,392 960,314 960,470 0,470" />
          {motif === "opening" && (
            <>
              <circle className="opening-light" cx="180" cy="132" r="48" />
              <path className="opening-rays" d="M180 58v34M106 132h34M220 132h34M128 80l24 24M232 80l-24 24" />
            </>
          )}
          {motif === "crack" && <path className="world-crack" d="M474 176l-35 62 42 38-52 67 39 69" />}
          {motif === "threshold" && <path className="threshold-gate" d="M392 400V262h176v138M360 262h240" />}
          {motif === "allies" && (
            <>
              <path className="allies-thread" d="M330 392c92-104 248-104 340 0" />
              <g className="stage-companions">
                <circle cx="620" cy="320" r="12" />
                <path d="M620 332v42M620 344l-20 15M620 344l20 15M620 374l-15 30M620 374l15 30" />
                <circle cx="714" cy="326" r="11" />
                <path d="M714 337v38M714 348l-18 13M714 348l18 13M714 375l-14 28M714 375l14 28" />
              </g>
            </>
          )}
          {motif === "turning" && <circle className="turning-moon" cx="710" cy="154" r="64" />}
          {motif === "return" && (
            <>
              <path className="return-water" d="M230 378h500M280 412h400M340 444h280" />
              <path className="home-bridge" d="M188 400c130-136 454-136 584 0M188 400h584" />
            </>
          )}
          {motif === "closing" && (
            <>
              <circle className="closing-lamp" cx="760" cy="162" r="34" />
              <path className="closing-horizon" d="M190 396h580M270 420h420" />
            </>
          )}
          <g className="stage-person" transform={`translate(${SCENE_PERSON_OFFSETS[motif]} 0)`}>
            <circle cx="300" cy="316" r="15" />
            <path d="M300 331v50M300 346l-24 18M300 346l24 18M300 381l-18 36M300 381l18 36" />
          </g>
        </svg>
      )}
      <div className="scene-image-status" role="status">
        {visual.status === "loading" && (
          <span><i aria-hidden="true" />正在绘制第 {actIndex + 1} 幕画面…</span>
        )}
        {visual.status === "ready" && <span className="scene-image-ready">第 {actIndex + 1} 幕画面已就位</span>}
        {visual.status === "fallback" && (
          <div>
            <span>{toSimplifiedDisplay(visual.message) || "本幕继续使用纸影舞台。"}</span>
            {visual.retryable && <button type="button" onClick={onRetry}>重试本幕画面</button>}
          </div>
        )}
        {visual.status === "idle" && <span>第 {actIndex + 1} 幕 · {sceneTitle}</span>}
      </div>
    </div>
  );
}

type NarrationState = "idle" | "loading" | "speaking" | "paused";

/** How long the page-turn runs before the next act is mounted. */
const ACT_TURN_MS = 760;

function narrationText(act: TheatreAct) {
  return toSimplifiedDisplay(
    [act.sceneTitle ?? act.title, act.narration, act.dialogue].filter(Boolean).join("。 "),
  );
}

function TheatrePlayer({
  sessionId,
  script,
  onReachedEnd,
  onSceneImage,
}: {
  sessionId?: string;
  script: TheatreScript;
  onReachedEnd: () => void;
  onSceneImage?: (actId: string, imageUrl: string) => void;
}) {
  const [actIndex, setActIndex] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [curtainOpen, setCurtainOpen] = useState(false);
  const [narrationState, setNarrationState] = useState<NarrationState>("idle");
  const [narrationMessage, setNarrationMessage] = useState<string | null>(null);
  const [sceneVisuals, setSceneVisuals] = useState<Record<string, SceneVisualState>>({});
  // Cached CosyVoice clips, one per act id. `null` means "asked, unavailable".
  const [narrationClips, setNarrationClips] = useState<Record<string, string | null>>({});
  // True only while the page-turn animation runs, between one act's narration
  // ending and the next act being mounted.
  const [turningPage, setTurningPage] = useState(false);
  const speechUtteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const narrationAudioRef = useRef<HTMLAudioElement | null>(null);
  const turnTimerRef = useRef<number | null>(null);
  const acts = script.acts;
  const act = acts[boundedActIndex(actIndex, acts.length)];
  const duration = Math.max(1, act?.durationSeconds ?? 1);
  const progress = Math.min(100, (elapsed / duration) * 100);
  // While the voice is still working through this act, the clock must not turn
  // the page out from under it. The act's own durationSeconds is only a
  // stand-in for when there is no voice at all.
  const narrationHoldsPage = narrationState !== "idle";
  const speechSupported = typeof window !== "undefined"
    && "speechSynthesis" in window
    && "SpeechSynthesisUtterance" in window;

  const requestSceneForAct = useCallback(async (targetAct: TheatreAct) => {
    const loadingState: SceneVisualState = {
      status: "loading",
      imageUrl: null,
      altText: `${toSimplifiedDisplay(targetAct.sceneTitle ?? targetAct.title)}的本幕画面`,
      message: null,
      retryable: false,
    };
    setSceneVisuals((current) => ({ ...current, [targetAct.id]: loadingState }));

    if (!sessionId) {
      setSceneVisuals((current) => ({
        ...current,
        [targetAct.id]: {
          status: "fallback",
          imageUrl: null,
          altText: loadingState.altText,
          message: "本幕继续使用纸影舞台。",
          retryable: false,
        },
      }));
      return;
    }

    try {
      const response = await createTheatreSceneImage(sessionId, script.id, targetAct.id);
      setSceneVisuals((current) => ({ ...current, [targetAct.id]: response }));
      if (response.status === "ready" && response.imageUrl) {
        onSceneImage?.(targetAct.id, response.imageUrl);
      }
    } catch {
      setSceneVisuals((current) => ({
        ...current,
        [targetAct.id]: {
          status: "fallback",
          imageUrl: null,
          altText: loadingState.altText,
          message: "本幕画面暂时没有连上，可以继续观看或重试。",
          retryable: true,
        },
      }));
    }
  }, [onSceneImage, script.id, sessionId]);

  function markSceneImageFailed(targetAct: TheatreAct) {
    setSceneVisuals((current) => ({
      ...current,
      [targetAct.id]: {
        status: "fallback",
        imageUrl: null,
        altText: `${toSimplifiedDisplay(targetAct.sceneTitle ?? targetAct.title)}的本幕画面`,
        message: "本幕画面链接暂时无法载入，纸影舞台已接替显示。",
        retryable: true,
      },
    }));
  }

  // The finish handlers fire from audio/speech callbacks that captured an old
  // render, so the decision to turn the page reads live values from refs.
  const playingRef = useRef(playing);
  const actIndexRef = useRef(actIndex);
  playingRef.current = playing;
  actIndexRef.current = actIndex;

  /** Turn to the next act, with the page-turn in between. */
  const advanceAct = useCallback(() => {
    if (turnTimerRef.current !== null) return;
    if (actIndexRef.current >= acts.length - 1) {
      setPlaying(false);
      onReachedEnd();
      return;
    }
    setTurningPage(true);
    turnTimerRef.current = window.setTimeout(() => {
      turnTimerRef.current = null;
      setTurningPage(false);
      setActIndex((index) => boundedActIndex(index + 1, acts.length));
      setElapsed(0);
    }, ACT_TURN_MS);
  }, [acts.length, onReachedEnd]);

  /** Called only when narration reaches its own end, never when we cancel it. */
  const handleNarrationFinished = useCallback(() => {
    setNarrationState("idle");
    if (playingRef.current) advanceAct();
  }, [advanceAct]);

  const stopNarration = useCallback(() => {
    const audio = narrationAudioRef.current;
    if (audio) {
      // Detach first: a paused clip must not look like a finished one.
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      narrationAudioRef.current = null;
    }
    if (speechUtteranceRef.current) {
      speechUtteranceRef.current.onend = null;
      speechUtteranceRef.current.onerror = null;
    }
    if (speechSupported) window.speechSynthesis.cancel();
    speechUtteranceRef.current = null;
    setNarrationState("idle");
  }, [speechSupported]);

  /** Browser speech is only a fallback for when CosyVoice is unavailable. */
  const speakLocally = useCallback((targetAct: TheatreAct) => {
    if (!speechSupported) {
      setNarrationState("idle");
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(narrationText(targetAct));
    const voice = window.speechSynthesis.getVoices().find((item) => item.lang.toLowerCase().startsWith("zh"));
    if (voice) utterance.voice = voice;
    utterance.lang = voice?.lang ?? "zh-CN";
    utterance.rate = 0.9;
    utterance.pitch = 0.94;
    utterance.onend = () => {
      speechUtteranceRef.current = null;
      handleNarrationFinished();
    };
    utterance.onerror = () => {
      speechUtteranceRef.current = null;
      setNarrationState("idle");
    };
    speechUtteranceRef.current = utterance;
    window.speechSynthesis.speak(utterance);
    setNarrationState("speaking");
  }, [handleNarrationFinished, speechSupported]);

  /** Fetch (once) and play this act's CosyVoice narration. */
  const playNarration = useCallback(async (targetAct: TheatreAct) => {
    stopNarration();
    setNarrationState("loading");

    let clip = narrationClips[targetAct.id];
    if (clip === undefined) {
      if (!sessionId) {
        clip = null;
      } else {
        try {
          const result = await createActNarration(sessionId, script.id, targetAct.id);
          clip = result.status === "ready" ? result.audioUrl : null;
          if (result.status !== "ready" && result.message) setNarrationMessage(result.message);
        } catch {
          clip = null;
        }
      }
      setNarrationClips((current) => ({ ...current, [targetAct.id]: clip ?? null }));
    }

    if (!clip) {
      speakLocally(targetAct);
      return;
    }

    const audio = new Audio(clip);
    audio.onended = () => {
      narrationAudioRef.current = null;
      handleNarrationFinished();
    };
    audio.onerror = () => {
      narrationAudioRef.current = null;
      speakLocally(targetAct);
    };
    narrationAudioRef.current = audio;
    try {
      await audio.play();
      setNarrationState("speaking");
    } catch {
      narrationAudioRef.current = null;
      speakLocally(targetAct);
    }
  }, [handleNarrationFinished, narrationClips, script.id, sessionId, speakLocally, stopNarration]);

  function toggleNarration() {
    if (!act || !curtainOpen) return;
    const audio = narrationAudioRef.current;
    if (narrationState === "speaking") {
      if (audio) audio.pause();
      else if (speechSupported) window.speechSynthesis.pause();
      setNarrationState("paused");
      return;
    }
    if (narrationState === "paused") {
      if (audio) void audio.play().catch(() => undefined);
      else if (speechSupported) window.speechSynthesis.resume();
      setNarrationState("speaking");
      return;
    }
    void playNarration(act);
  }

  function cancelPageTurn() {
    if (turnTimerRef.current !== null) {
      window.clearTimeout(turnTimerRef.current);
      turnTimerRef.current = null;
    }
    setTurningPage(false);
  }

  function jumpTo(index: number) {
    cancelPageTurn();
    stopNarration();
    setActIndex(boundedActIndex(index, acts.length));
    setElapsed(0);
  }

  function restart() {
    cancelPageTurn();
    stopNarration();
    setActIndex(0);
    setElapsed(0);
    setPlaying(true);
  }

  function toggleCurtain() {
    if (curtainOpen) {
      setPlaying(false);
      cancelPageTurn();
      stopNarration();
      setCurtainOpen(false);
      return;
    }
    // Opening the curtain starts the show; the per-act effect below then
    // narrates each act as it comes up.
    setCurtainOpen(true);
    setPlaying(true);
  }

  useEffect(() => {
    setSceneVisuals({});
  }, [script.id]);

  useEffect(() => {
    if (!act || acts.length === 0) return;
    if (Object.values(sceneVisuals).some((visual) => visual.status === "loading")) return;
    const priority = [act, ...acts.filter((item) => item.id !== act.id)];
    const nextAct = priority.find((item) => {
      const visual = sceneVisuals[item.id];
      return visual === undefined || visual.status === "idle";
    });
    if (nextAct) void requestSceneForAct(nextAct);
  }, [act, acts, requestSceneForAct, sceneVisuals]);

  // Narrate each act as it opens, so the voice tracks the performance instead
  // of needing a separate click. Keyed on act id: re-narrates on act change,
  // not on every render.
  const narratedActRef = useRef<string | null>(null);
  useEffect(() => {
    if (!curtainOpen || !playing || !act) {
      if (!curtainOpen) narratedActRef.current = null;
      return;
    }
    if (narratedActRef.current === act.id) return;
    narratedActRef.current = act.id;
    void playNarration(act);
    // playNarration is intentionally omitted: it changes identity whenever a
    // clip is cached, which would re-trigger narration for the same act.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [act, curtainOpen, playing]);

  useEffect(() => {
    if (!playing || !act || !curtainOpen || turningPage) return;
    const timer = window.setInterval(() => {
      setElapsed((current) => Math.min(duration, current + 0.25));
    }, 250);
    return () => window.clearInterval(timer);
  }, [act, curtainOpen, duration, playing, turningPage]);

  // The clock is now only the understudy. When an act has narration, the page
  // turns as the reading ends (see handleNarrationFinished); durationSeconds
  // only decides the pace for acts whose voice never arrived.
  useEffect(() => {
    if (!playing || turningPage || narrationHoldsPage) return;
    if (elapsed < duration) return;
    advanceAct();
  }, [advanceAct, duration, elapsed, narrationHoldsPage, playing, turningPage]);

  useEffect(() => () => {
    if (turnTimerRef.current !== null) window.clearTimeout(turnTimerRef.current);
    if (speechSupported) window.speechSynthesis.cancel();
    if (narrationAudioRef.current) {
      narrationAudioRef.current.onended = null;
      narrationAudioRef.current.pause();
      narrationAudioRef.current = null;
    }
  }, [speechSupported]);

  if (!act) return <div className="theatre-empty" role="alert">剧本暂时没有可播放的内容。空舞台会记为未完成，可以返回上一步重试。</div>;

  const narrationButtonLabel = narrationState === "speaking"
    ? "暂停旁白"
    : narrationState === "paused"
      ? "继续旁白"
      : narrationState === "loading" ? "正在准备旁白…" : "播放本幕旁白";
  const actVisual = sceneVisuals[act.id] ?? idleSceneState;

  return (
    <section className="theatre-player theatre-player-with-media" aria-labelledby="theatre-title">
      <div className="theatre-title-row">
        <h2 id="theatre-title">把这条支线演给自己看</h2>
        <p className="curtain-status" role="status">共 {acts.length} 幕 · {curtainOpen ? "幕布已拉开" : "幕布已合上"}</p>
      </div>

      <div className={`curtain-frame ${turningPage ? "act-is-turning" : ""}`}>
        <div className={`curtain-stage ${curtainOpen ? "curtain-is-open" : "curtain-is-closed"}`}>
          <div className="stage-scene-layer" aria-hidden={!curtainOpen}>
            <TheatreScene
              act={act}
              actIndex={actIndex}
              visual={actVisual}
              onRetry={() => void requestSceneForAct(act)}
              onImageError={() => markSceneImageFailed(act)}
            />
          </div>
          <div className="curtain curtain-left" aria-hidden="true" />
          <div className="curtain curtain-right" aria-hidden="true" />
          {!curtainOpen && (
            <button type="button" className="curtain-pull" onClick={toggleCurtain}>
              <span aria-hidden="true">开</span>
              拉开幕布
            </button>
          )}
        </div>
        <div className="act-caption" aria-live="polite">
          <span>第 {actIndex + 1} 幕 · {toSimplifiedDisplay(act.sceneTitle ?? act.title)}</span>
          <p>{toSimplifiedDisplay(act.narration)}</p>
          {act.dialogue && <blockquote>{toSimplifiedDisplay(act.dialogue)}</blockquote>}
        </div>
      </div>

      <div
        className="theatre-timeline"
        role="progressbar"
        aria-label={`第 ${actIndex + 1} 幕播放进度`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress)}
      >
        <div style={{ width: `${progress}%` }} />
      </div>

      <div className="theatre-controls theatre-transport">
        <div className="act-switcher" aria-label="选择幕次">
          {acts.map((item, index) => (
            <button type="button" key={item.id} className={index === actIndex ? "active" : ""} aria-current={index === actIndex ? "step" : undefined} onClick={() => jumpTo(index)}>
              {index + 1}<span className="sr-only">第 {index + 1} 幕</span>
            </button>
          ))}
        </div>
        <div>
          <button type="button" className="curtain-control" aria-pressed={curtainOpen} onClick={toggleCurtain}>{curtainOpen ? "合上幕布" : "拉开幕布"}</button>
          <button type="button" disabled={!curtainOpen} onClick={() => setPlaying((current) => !current)}>{playing ? "暂停演出" : elapsed >= duration && actIndex === acts.length - 1 ? "再看一次" : "开始演出"}</button>
          <button type="button" disabled={!curtainOpen} onClick={restart}>从第一幕重播</button>
          <button type="button" disabled={!curtainOpen} onClick={() => { jumpTo(acts.length - 1); setElapsed(Math.max(0, (acts.at(-1)?.durationSeconds ?? 1) - 0.5)); setPlaying(true); }}>前往终幕</button>
        </div>
      </div>

      <div className="theatre-media-desk" aria-label="剧场声音与录音控制">
        <section className="media-channel" aria-labelledby="narration-channel-title">
          <header><h3 id="narration-channel-title">旁白</h3><p>朗读本幕字幕；读完自动翻到下一幕。</p></header>
          {speechSupported ? (
            <div className="media-control-row">
              <button type="button" disabled={!curtainOpen} aria-pressed={narrationState === "speaking"} onClick={toggleNarration}>{narrationButtonLabel}</button>
              <button type="button" disabled={narrationState === "idle"} onClick={stopNarration}>停止旁白</button>
              <span className="media-state" role="status">{narrationState === "speaking" ? "正在朗读" : narrationState === "paused" ? "旁白已暂停" : "旁白待命"}</span>
            </div>
          ) : (
            <p className="media-fallback" role="status">当前设备无法朗读旁白；字幕完整保留。</p>
          )}
        </section>

      </div>

      <p className="theatre-direction"><strong>舞台调度：</strong>{toSimplifiedDisplay(act.stageDirection)}</p>
    </section>
  );
}

export function RitualizationStage({
  sessionId,
  story,
  script,
  busy,
  error,
  notice,
  onComplete,
  onSceneImage,
}: {
  sessionId?: string;
  story: StoryCard;
  script: TheatreScript;
  busy: boolean;
  error: string | null;
  notice: string | null;
  onComplete: (payload: { title: string; finalLine: string; gesture: RitualGesture; save: boolean }) => Promise<void>;
  onSceneImage?: (actId: string, imageUrl: string) => void;
}) {
  const [reachedEnd, setReachedEnd] = useState(false);
  const [title, setTitle] = useState(script.title ?? "");
  const [finalLine, setFinalLine] = useState(script.finalLineSuggestions[0] ?? "");
  const [gesture, setGesture] = useState<RitualGesture>("light");
  const [save, setSave] = useState(true);
  const totalDuration = useMemo(
    () => script.totalDurationSeconds || script.acts.reduce((sum, act) => sum + act.durationSeconds, 0),
    [script],
  );

  return (
    <main id="main" className="stage-page ritual-page">
      <header className="stage-intro ritual-intro">
        <h1>再演</h1>
        <p>把这条支线演给自己看。全卷约 {Math.max(1, Math.round(totalDuration / 60))} 分钟；拉开幕布后旁白会随每一幕自动朗读，随时可暂停。</p>
      </header>
      <StatusMessage error={error} notice={notice} />
      <TheatrePlayer sessionId={sessionId} script={script} onReachedEnd={() => setReachedEnd(true)} onSceneImage={onSceneImage} />

      <section className={`ritual-console ${reachedEnd ? "is-ready" : ""}`} aria-labelledby="ritual-title">
        <div className="ritual-heading">
          <span className="ritual-orbit" aria-hidden="true" />
          <h2 id="ritual-title">为这版故事落款</h2>
          <p>{reachedEnd ? "终幕已到。请留下标题、末句与一个收束动作。" : "看完全部幕次或直接前往终幕后，这里会点亮。"}</p>
        </div>
        <div className="ritual-fields">
          <label className="field-block">
            <span>为这条现代支线命名</span>
            <input type="text" maxLength={80} value={title} onChange={(event) => setTitle(event.target.value)} placeholder={`与《${toSimplifiedDisplay(story.title)}》相遇之后`} />
          </label>
          <div className="final-line-field">
            <label className="field-block">
              <span>选择或改写最后一句</span>
              <textarea rows={3} maxLength={180} value={finalLine} onChange={(event) => setFinalLine(event.target.value)} />
            </label>
            {script.finalLineSuggestions.length > 1 && (
              <div className="line-suggestions">
                {script.finalLineSuggestions.slice(0, 3).map((line) => <button type="button" key={line} onClick={() => setFinalLine(line)}>{line}</button>)}
              </div>
            )}
          </div>
          <fieldset className="gesture-options">
            <legend>选择一个终幕动作</legend>
            <label className={gesture === "light" ? "selected" : ""}>
              <input type="radio" name="gesture" value="light" checked={gesture === "light"} onChange={() => setGesture("light")} />
              <span className="gesture-light" aria-hidden="true" /><strong>点亮终幕</strong><small>让一盏灯留在故事出口。</small>
            </label>
            <label className={gesture === "seal" ? "selected" : ""}>
              <input type="radio" name="gesture" value="seal" checked={gesture === "seal"} onChange={() => setGesture("seal")} />
              <span className="gesture-seal" aria-hidden="true">蝶</span><strong>为纪念卡盖印</strong><small>确认这是你认可的版本。</small>
            </label>
          </fieldset>
          <label className="save-control">
            <input type="checkbox" checked={save} onChange={(event) => setSave(event.target.checked)} />
            <span><strong>暂存纪念卡</strong><small>{save ? "按当前服务的数据期限保存，随时可以删除。" : "完成后立即丢弃纪念卡。"}</small></span>
          </label>
        </div>
        <button className="primary-action ritual-action" type="button" disabled={!reachedEnd || !title.trim() || !finalLine.trim() || busy} onClick={() => void onComplete({ title: title.trim(), finalLine: finalLine.trim(), gesture, save })}>
          {busy ? "正在完成终幕…" : gesture === "seal" ? "盖下这一印" : "点亮这一幕"}<span aria-hidden="true">→</span>
        </button>
      </section>
    </main>
  );
}

function originLabel(origin: ProvenanceLedger["entries"][number]["origin"]) {
  const labels = {
    source_canon: "原典支持",
    user_created: "用户创作",
    model_expression: "智能整理",
    interpretive_bridge: "解释性桥接",
  } as const;
  return labels[origin];
}

function segmentLabel(segment: string) {
  if (segment === "source_canon") return "原典摘录";
  return BRANCH_NODE_DEFINITIONS.find((definition) => definition.id === segment)?.title
    ?? "共谱片段";
}

export function ArtifactView({
  artifact,
  ledger,
  script,
  sceneImages,
  busy,
  error,
  onDelete,
}: {
  artifact: RitualArtifact;
  ledger: ProvenanceLedger;
  script?: TheatreScript | null;
  sceneImages?: Record<string, string | null>;
  busy: boolean;
  error: string | null;
  onDelete: () => Promise<void>;
}) {
  const recapActs = script?.acts ?? [];

  return (
    <main id="main" className="artifact-page">
      <section className="artifact-card" aria-labelledby="artifact-title">
        <div className={`artifact-gesture artifact-${artifact.ritualGesture}`} aria-hidden="true">
          {artifact.ritualGesture === "seal" ? "蝶" : <i />}
        </div>
        <p className="section-code">当代改编</p>
        <h1 id="artifact-title">{toSimplifiedDisplay(artifact.title)}</h1>
        <blockquote>{toSimplifiedDisplay(artifact.finalLine)}</blockquote>
        <div className="artifact-lines" aria-label="原典与现代支线仍保持分离">
          <span className="key-source"><i />原典依据</span>
          <span className="key-branch"><i />你的支线</span>
        </div>
        <small>完成于 {new Date(artifact.createdAt).toLocaleString("zh-CN")} · {artifact.saved ? "纪念卡已按设置暂存" : "纪念卡已随会话丢弃"}</small>
      </section>

      {recapActs.length > 0 && (
        <section className="artifact-recap" aria-labelledby="recap-title">
          <header>
            <p className="section-label">故事小结</p>
            <h2 id="recap-title">这条支线走过的每一幕</h2>
          </header>
          <ol className="recap-list">
            {recapActs.map((act, index) => {
              const image = sceneImages?.[act.id] ?? null;
              return (
                <li key={act.id} className="recap-row">
                  <div className="recap-art">
                    {image ? (
                      <img src={image} alt={`第 ${index + 1} 幕画面`} loading="lazy" referrerPolicy="no-referrer" />
                    ) : (
                      <div className="recap-art-empty" role="img" aria-label={`第 ${index + 1} 幕暂无画面`}>
                        <span>第 {index + 1} 幕</span>
                      </div>
                    )}
                  </div>
                  <div className="recap-copy">
                    <h3>{toSimplifiedDisplay(act.sceneTitle ?? act.title)}</h3>
                    <p>{toSimplifiedDisplay(act.narration)}</p>
                  </div>
                </li>
              );
            })}
          </ol>
        </section>
      )}

      <section className="provenance-ledger" aria-labelledby="ledger-title">
        <header>
          <h2 id="ledger-title">这段故事从哪里来</h2>
          <span>{ledger.modelVersion ? "含智能整理" : "只含本地整理"}</span>
        </header>
        {ledger.sourceCanon ? (
          <div className="ledger-source">
            <span className="origin-badge origin-source">原典只读</span>
            <h3>{toSimplifiedDisplay(ledger.sourceCanon.title)}</h3>
            <p>{toSimplifiedDisplay(ledger.sourceCanon.sourceTitle)}</p>
          </div>
        ) : (
          <div className="ledger-source ledger-source-missing" role="status">
            <span className="origin-badge origin-source">来源未返回</span>
            <h3>暂时无法显示原典条目</h3>
            <p>缺失的来源数据会保持空缺。可以删除会话后重新开始。</p>
          </div>
        )}
        <ol>
          {ledger.entries.map((entry) => (
            <li key={entry.id} className={`ledger-${entry.origin}`}>
              <span className={`origin-badge origin-${entry.origin}`}>{originLabel(entry.origin)}</span>
              <div>
                <strong>{segmentLabel(entry.segment)}</strong>
                <p>{toSimplifiedDisplay(entry.text)}</p>
                {entry.sourceTitle && (
                  <small>
                    {toSimplifiedDisplay(entry.sourceTitle)}
                    {entry.sourceLocator ? ` · ${toSimplifiedDisplay(entry.sourceLocator)}` : ""}
                  </small>
                )}
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="artifact-exit">
        <div><h2>保留故事，或删除这次会话。</h2><p>删除会清除你这次留下的文字、支线、剧本与纪念卡；故事库原文不受影响。</p></div>
        <StatusMessage error={error} />
        <button type="button" className="danger-action" disabled={busy} onClick={() => window.confirm("确定删除本次会话、支线、剧本与纪念卡吗？此操作不可撤回。") && void onDelete()}>
          {busy ? "正在删除并验证…" : "删除本次会话"}
        </button>
      </section>
    </main>
  );
}
