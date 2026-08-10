import type { ExperienceStage } from "../types";

const STAGES: ReadonlyArray<{ key: ExperienceStage; label: string; description: string }> = [
  { key: "encounter", label: "相遇", description: "聊经历，选故事" },
  { key: "articulation", label: "共谱", description: "看初稿，继续改" },
  { key: "ritualization", label: "再演", description: "逐幕打开故事" },
];

function stagePosition(stage: ExperienceStage) {
  if (stage === "artifact") return 3;
  return STAGES.findIndex((item) => item.key === stage);
}

export function StoryLoom({ stage }: { stage: ExperienceStage }) {
  const current = stagePosition(stage);

  return (
    <nav className="story-loom butterfly-binding-progress" aria-label="故事体验进度">
      <ol className="loom-stages">
        {STAGES.map((item, index) => (
          <li
            key={item.key}
            className={index === current ? "is-current" : index < current ? "is-past" : ""}
            aria-current={index === current ? "step" : undefined}
          >
            <strong>{item.label}</strong>
            <small>{item.description}</small>
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function SiteHeader({ stage }: { stage: ExperienceStage }) {
  const isLanding = stage === "welcome";

  return (
    <header className={`site-header${isLanding ? " site-header-landing" : ""}`} data-variant={isLanding ? "landing" : "experience"}>
      <a className="brand" href="#main" aria-label="梦蝶记体验首页">
        <img className="brand-mark" src="/assets/mengdie-logo-mark-side-v5-transparent.png" alt="" />
        <span><strong className="brand-wordmark">梦蝶记</strong><small>中国古典神话传说与个人经历共谱</small></span>
      </a>
      {isLanding ? (
        <nav className="landing-nav" aria-label="首页导航">
          <a href="#case-showcase">案例</a>
          <a href="#how-it-works">功能</a>
          <a href="#technology">技术</a>
          <a href="#corpus">故事来源</a>
          <a className="landing-nav-cta" href="#hero-actions">开始 <span aria-hidden="true">↗</span></a>
        </nav>
      ) : null}
    </header>
  );
}

export function StatusMessage({ error, notice }: { error?: string | null; notice?: string | null }) {
  if (!error && !notice) return null;
  return (
    <div className={error ? "status-message status-error" : "status-message"} role={error ? "alert" : "status"}>
      <strong>{error ? "需要处理" : "提示"}</strong>
      <p>{error ?? notice}</p>
    </div>
  );
}
