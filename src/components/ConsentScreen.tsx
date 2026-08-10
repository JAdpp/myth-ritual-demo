import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import type { HealthStatus, SessionConsent } from "../types";
import { toSimplifiedDisplay } from "../lib/display-text";
import { StatusMessage } from "./StoryLoom";

const FAMILY_TITLES: Record<string, string> = {
  pangu_cosmogony: "盘古开天",
  kuafu_sun_chase: "夸父逐日",
  gun_yu_flood_control: "大禹治水",
  change_flight_to_moon: "嫦娥奔月",
  mulan_substitution: "花木兰从军",
  white_snake_legend: "白蛇传",
  nvwa_mends_sky: "女娲补天",
  jingwei_fills_sea: "精卫填海",
  yugong_moves_mountains: "愚公移山",
  zhuangzhou_butterfly_dream: "庄周梦蝶",
  houyi_shoots_suns: "后羿射日",
  cowherd_weaver_girl: "牛郎织女",
  mengjiangnu_great_wall: "孟姜女哭长城",
  butterfly_lovers: "梁山伯与祝英台",
  peach_blossom_spring: "桃花源",
  painted_skin: "画皮",
  boya_breaks_strings: "伯牙绝弦",
  nanke_dream: "南柯一梦",
};

const FALLBACK_FAMILIES = [
  "大禹治水",
  "嫦娥奔月",
  "盘古开天",
  "夸父逐日",
  "女娲补天",
  "精卫填海",
  "庄周梦蝶",
  "白蛇传",
];

const DEMO_CASES = [
  {
    id: "new-team",
    tab: "接下新任务",
    label: "新团队里的临时任务",
    input: "第一次参加新团队例会，我主动接下合作任务，回去后却有点不安。",
    question: "你主动答应时，最先在意的是什么？后来又是什么让你开始不安？",
    story: "大禹治水",
    source: "采用《吴越春秋·越王无余外传》",
    reason: "把接手重担、重新选择方法与辨认责任边界放在一起比较——不是赞美过劳。",
    branch: "先问清共同责任与可用方法，再决定自己愿意承担到哪里。",
    finalLine: "承担，不等于独自扛下所有。",
  },
  {
    id: "plan-changed",
    tab: "计划忽然落空",
    label: "一个没有按计划发生的结果",
    input: "准备很久的机会突然取消，我知道还会有下一次，还是很难立刻放下。",
    question: "如果不急着把这件事说成好或坏，此刻最确定的损失是什么？仍未结束的变化又是什么？",
    story: "塞翁失马",
    source: "采用《淮南子·人间训》",
    reason: "它反复改变对同一事件的判断，但不会用未来的可能性否认当下真实的失落。",
    branch: "先承认已经落空的部分，再给尚未发生的变化留一处空白。",
    finalLine: "我可以暂时不知道，它最终意味着什么。",
  },
  {
    id: "long-project",
    tab: "长期推进受阻",
    label: "一件迟迟没有进展的长期工作",
    input: "这件事做了很久，眼前仍像一座山。我开始怀疑，坚持是不是只剩下消耗。",
    question: "你想继续的究竟是哪一部分？如果不再一个人搬动它，谁或什么可以加入？",
    story: "愚公移山",
    source: "采用《列子·汤问篇》愚公移山段",
    reason: "它不只讲坚持，也写家人、邻人与代际协作，适合重新拆分“独自坚持”这件事。",
    branch: "把遥远目标拆成可交接的小段，并允许同行者改变原来的路径。",
    finalLine: "山没有立刻变小，但路不再只由我一个人开。",
  },
] as const;

export function ConsentScreen({
  busy,
  error,
  health,
  onBegin,
}: {
  busy: boolean;
  error: string | null;
  health: HealthStatus | null;
  onBegin: (consent: SessionConsent) => Promise<void>;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [activeCaseId, setActiveCaseId] = useState<(typeof DEMO_CASES)[number]["id"]>(DEMO_CASES[0].id);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const overview = health?.corpusOverview;
  const recommendationCount = overview?.totalRecommendationCandidates
    ?? overview?.recommendationPoolStories
    ?? overview?.catalogUniqueStories
    ?? overview?.catalogEntries
    ?? 12_383;
  const sourceWorkCount = overview?.catalogSourceWorks
    || overview?.catalogWorks?.length
    || undefined;
  const featuredWorks = overview?.catalogWorks?.slice(0, 6) ?? [];
  const familyTitles = useMemo(() => {
    const resolved = (overview?.familyIds ?? [])
      .map((familyId) => FAMILY_TITLES[familyId])
      .filter((title): title is string => Boolean(title));
    return [...new Set(resolved.length ? resolved : FALLBACK_FAMILIES)].slice(0, 12);
  }, [overview?.familyIds]);
  const activeCase = DEMO_CASES.find((item) => item.id === activeCaseId) ?? DEMO_CASES[0];
  const snapshotDate = overview?.catalogSnapshotDate ?? "2026-08-09";

  useEffect(() => {
    if (!modalOpen) return;
    closeButtonRef.current?.focus();
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) setModalOpen(false);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [busy, modalOpen]);

  useEffect(() => {
    const targets = [...document.querySelectorAll<HTMLElement>("[data-reveal]")];
    if (!("IntersectionObserver" in window)) {
      targets.forEach((target) => target.classList.add("is-visible"));
      return undefined;
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -12%", threshold: 0.12 });
    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, []);

  function moveHero(event: ReactPointerEvent<HTMLElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - bounds.left) / bounds.width - 0.5) * 18;
    const y = ((event.clientY - bounds.top) / bounds.height - 0.5) * 12;
    event.currentTarget.style.setProperty("--hero-shift-x", `${x.toFixed(2)}px`);
    event.currentTarget.style.setProperty("--hero-shift-y", `${y.toFixed(2)}px`);
  }

  function resetHero(event: ReactPointerEvent<HTMLElement>) {
    event.currentTarget.style.setProperty("--hero-shift-x", "0px");
    event.currentTarget.style.setProperty("--hero-shift-y", "0px");
  }

  function begin() {
    const bundledConsent: SessionConsent = {
      adultConfirmed: accepted,
      nonClinicalAcknowledged: accepted,
      cloudProcessingAccepted: accepted,
      adultContentOptIn: accepted,
    };
    void onBegin(bundledConsent);
  }

  return (
    <main id="main" className="landing-page landing-page-v2">
      <section
        className="landing-hero landing-hero-cinematic"
        aria-labelledby="welcome-title"
        onPointerMove={moveHero}
        onPointerLeave={resetHero}
      >
        <div className="hero-art" aria-hidden="true">
          <div className="hero-art-image" />
          <div className="hero-ink-veil" />
          <div className="hero-mist hero-mist-a" />
          <div className="hero-mist hero-mist-b" />
          <img className="hero-floating-mark hero-floating-mark-a" src="/assets/mengdie-logo-mark-side-v5-transparent.png" alt="" />
          <img className="hero-floating-mark hero-floating-mark-b" src="/assets/mengdie-logo-mark-side-v5-transparent.png" alt="" />
        </div>

        <div className="landing-hero-copy">
          <p className="hero-edition"><span>中国古典故事</span><i aria-hidden="true" /><span>个人经历</span><i aria-hidden="true" /><span>AI 共谱</span></p>
          <p className="hero-wordmark">梦蝶记</p>
          <h1 id="welcome-title">古典故事，<br /><em>在你的此刻<br />生出一条新支线。</em></h1>
          <p className="welcome-lead">
            <strong>栖蝶——梦蝶记中的故事向导 AI。</strong>把一件最近发生、你愿意讲的小事告诉它；它会先整理并请你确认，再从可追溯的古籍候选中寻找一则可供比较的故事。原典留在原典，你的选择写成新的现代支线。
          </p>
          <div id="hero-actions" className="landing-actions">
            <button id="hero-start" className="primary-action landing-primary" type="button" onClick={() => setModalOpen(true)}>
              进入体验 <span aria-hidden="true">↗</span>
            </button>
            <a className="landing-text-link" href="#case-showcase">先看一次示例 <span aria-hidden="true">↓</span></a>
          </div>
          <div className="hero-trust" aria-label="体验边界">
            <span>来源可追溯</span><span>映照可修改</span><span>会话可删除</span>
          </div>
          <p className="landing-meta">约 12–15 分钟 · 面向成年用户 · 当前为技术演示</p>
        </div>

        <aside className="hero-signal-card" aria-label="栖蝶工作示意">
          <header>
            <div className="hero-guide-identity">
              <img src="/assets/qidie-assistant-avatar-transparent.png" alt="" />
              <p><strong>栖蝶</strong><span>故事向导 AI</span></p>
            </div>
            <span className="hero-signal-status">整理过程示意</span>
          </header>
          <div className="hero-signal-thread">
            <p className="hero-signal-user"><span>你刚刚说</span>“我主动接下任务，回去后却有点不安。”</p>
            <div className="hero-signal-route" aria-hidden="true"><i /><i /><i /></div>
            <div className="hero-signal-result">
              <span className="hero-result-seal">水</span>
              <p><small>有出处的故事回应</small><strong>大禹治水</strong><span>接手重担，也重新选择方法。</span></p>
            </div>
          </div>
          <footer><span>推荐理由可读</span><span>故事可以拒绝</span></footer>
        </aside>

        <a className="hero-scroll-cue" href="#case-showcase"><span>SCROLL</span><i aria-hidden="true" /></a>
      </section>

      <section id="case-showcase" className="landing-section case-showcase" aria-labelledby="case-title" data-reveal>
        <header className="landing-section-heading">
          <p className="section-label">案例 · 一次完整来回</p>
          <h2 id="case-title">一件真实小事，怎样走到一场可以改写的再演？</h2>
          <p>下面是体验路径示意，不是固定答案。你说的内容、选择的故事与最后一句，始终可以更改或拒绝。</p>
        </header>

        <div className="case-tabs" role="tablist" aria-label="切换示例体验">
          {DEMO_CASES.map((item, index) => (
            <button
              key={item.id}
              id={`case-tab-${item.id}`}
              type="button"
              role="tab"
              aria-selected={item.id === activeCase.id}
              aria-controls="case-demo-panel"
              onClick={() => setActiveCaseId(item.id)}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>{item.tab}
            </button>
          ))}
        </div>

        <div
          key={activeCase.id}
          id="case-demo-panel"
          className="case-demo-panel"
          role="tabpanel"
          aria-live="polite"
          aria-labelledby={`case-tab-${activeCase.id}`}
        >
          <article className="case-moment case-moment-user">
            <p className="case-step">你说 · {activeCase.label}</p>
            <blockquote>“{activeCase.input}”</blockquote>
            <p className="case-note"><span>栖蝶先问</span>{activeCase.question}</p>
          </article>

          <article className="case-moment case-moment-source">
            <p className="case-step">有出处的故事回应</p>
            <div className="case-story-lockup">
              <span aria-hidden="true">典</span>
              <div><h3>{activeCase.story}</h3><p>{activeCase.source}</p></div>
            </div>
            <p>{activeCase.reason}</p>
            <small>推荐用于比较，不替你解释人生。</small>
          </article>

          <article className="case-moment case-moment-branch">
            <p className="case-step">你们共谱 · 仍由你批准</p>
            <p>{activeCase.branch}</p>
            <div className="case-final-line"><span>终幕收束句</span>“{activeCase.finalLine}”</div>
            <ul aria-label="用户可控操作"><li>修改五个映照节点</li><li>拒绝或换故事</li><li>命名、保存或删除</li></ul>
          </article>
        </div>
      </section>

      <section id="how-it-works" className="landing-section journey-section feature-section" aria-labelledby="journey-title" data-reveal>
        <div className="section-heading-block landing-section-heading">
          <h2 id="journey-title">从相遇到再演，决定权始终在你。</h2>
          <p>栖蝶沿着你分享的那件事整理时间、人物与转折，说明推荐并起草映照；故事选择、修改与最终表达仍由你确认。</p>
        </div>
        <ol className="journey-list feature-chapters">
          <li>
            <span className="chapter-mark">相遇</span><small>ENCOUNTER</small>
            <h3>先回应，再追问</h3>
            <p>从一件真实小事开始。只有你确认摘要后，系统才会给出带真实题名、采用文本与理由的候选。</p>
            <div className="feature-tags"><span>自然对话</span><span>摘要确认</span><span>可换一批</span></div>
          </li>
          <li>
            <span className="chapter-mark">共谱</span><small>ARTICULATION</small>
            <h3>原典在左，你执笔在右</h3>
            <p>五节点映照先形成可编辑初稿；你可以直接改，也可以请栖蝶修改。任何变化都先预览再应用。</p>
            <div className="feature-tags"><span>五节点画布</span><span>版本保留</span><span>逐处确认</span></div>
          </li>
          <li>
            <span className="chapter-mark">再演</span><small>RITUALIZATION</small>
            <h3>把批准的支线演出来</h3>
            <p>4–7 幕场景、幕布、系统旁白、环境音与可选录音共同组成再演；最后由你完成落款与收束动作。</p>
            <div className="feature-tags"><span>逐幕场景</span><span>声音控制</span><span>终幕落款</span></div>
          </li>
        </ol>
        <div className="choice-strip" aria-label="体验中的退出与控制选项">
          <span>不想继续？</span><p>可以拒绝推荐、跳过节点、关闭声音、不保存作品，或删除整次会话。</p>
        </div>
      </section>

      <section id="technology" className="technology-section" aria-labelledby="technology-title" data-reveal>
        <div className="technology-inner">
          <header className="technology-heading">
            <h2 id="technology-title">一条有来源、有边界、<br />也有退路的生成链路。</h2>
            <p>模型不是故事库，也不拥有最后决定权。检索、生成、验证与本地回退各自承担清楚的职责。</p>
          </header>

          <ol className="technology-pipeline" aria-label="梦蝶记技术链路">
            <li><span>01</span><strong>确认摘要</strong><p>只使用你明确确认的这次经历。</p></li>
            <li><span>02</span><strong>受限召回</strong><p>SQLite FTS5 / BM25 从古籍来源池取回候选。</p></li>
            <li><span>03</span><strong>证据内重排</strong><p>DeepSeek 规划检索表达、重排并说明理由。</p></li>
            <li><span>04</span><strong>四类校验</strong><p>结构、长度、来源边界与安全检查同时通过。</p></li>
            <li><span>05</span><strong>确定性回退</strong><p>模型或生图不可用，流程仍能继续完成。</p></li>
          </ol>

          <div className="technology-proof-grid">
            <article className="proof-source"><span>矿物绿 · SOURCE CANON</span><h3>原典保持只读</h3><p><code>source_canon</code> 锁定来源与采用文本；AI 改写不会冒充原典。</p></article>
            <article className="proof-branch"><span>灯火金 · USER BRANCH</span><h3>现代支线独立版本化</h3><p><code>user_branch</code> 记录你的选择；只有批准版本才会进入剧场。</p></article>
            <article className="proof-seal"><span>印章红 · HUMAN DECISION</span><h3>印章只代表你的确认</h3><p>它标记批准、来源揭示或警示，不代表“AI 一定正确”。</p></article>
          </div>

          <div className="technology-stats" aria-label="当前实现数字">
            <p><strong>{recommendationCount.toLocaleString("zh-CN")}</strong>{" "}<span>条可推荐候选</span></p>
            <p><strong>{sourceWorkCount ?? 6}</strong>{" "}<span>部开放古籍来源</span></p>
            <p><strong>30</strong>{" "}<span>则编辑深标主文本</span></p>
            <p><strong>4–7</strong>{" "}<span>幕动态再演</span></p>
          </div>
        </div>
      </section>

      <section id="corpus" className="landing-section corpus-section" aria-labelledby="corpus-title" data-reveal>
        <div className="corpus-stat-panel">
          <p className="section-label">故事来源 · {snapshotDate} 数据快照</p>
          <h2 id="corpus-title"><strong>{recommendationCount.toLocaleString("zh-CN")}</strong> 条候选，<br />从出处开始。</h2>
          <p className="corpus-status">
            当前候选包含规范化古籍来源分段与编辑深标主文本，不等同于同等数量的独立故事或专家标注语料。选中后会依据采用原文整理讲解与映照，再交给你核对；推荐卡显示真实题名与出处{sourceWorkCount ? `，目前汇集 ${sourceWorkCount} 部开放古籍` : ""}。
          </p>
          {featuredWorks.length ? (
            <ul className="corpus-group-list" aria-label="部分来源古籍">
              {featuredWorks.map((work) => (
                <li key={work.sourceWorkId}>
                  <span>{toSimplifiedDisplay(work.title)}</span><strong>{work.count.toLocaleString("zh-CN")} 条</strong>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="corpus-shelf-wrap">
          <p className="shelf-title">其中也有你熟悉的名字</p>
          <div className="corpus-shelf" aria-label="部分熟悉故事">
            {familyTitles.map((title) => <span key={title}>{title}</span>)}
          </div>
          <p className="shelf-footnote">题名熟悉，不代表版本混写；每次体验只采用一份明确主文本。</p>
        </div>
      </section>

      <section className="landing-final-cta" data-reveal>
        <div>
          <h2>你不必先懂神话，<br />只要带来一件愿意讲的小事。</h2>
          <p>栖蝶会陪你走完相遇、共谱与再演；原典不会被改写，最终留下什么由你决定。</p>
          <button className="primary-action" type="button" onClick={() => setModalOpen(true)}>和栖蝶开始 <span aria-hidden="true">↗</span></button>
        </div>
      </section>

      <footer className="landing-footer">
        <p><strong>梦蝶记</strong><span>中国古典故事与个人经历共谱</span></p>
        <p>成年用户文化叙事体验 · 非诊疗产品 · 本地技术演示</p>
      </footer>

      {modalOpen && (
        <div className="consent-modal-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !busy) setModalOpen(false);
        }}>
          <section className="consent-modal" role="dialog" aria-modal="true" aria-labelledby="consent-title" aria-describedby="consent-description">
            <button ref={closeButtonRef} className="modal-close" type="button" aria-label="关闭确认弹窗" disabled={busy} onClick={() => setModalOpen(false)}>关闭</button>
            <p className="section-label">进入前确认</p>
            <h2 id="consent-title">请先了解这次体验</h2>
            <p id="consent-description">梦蝶记面向成年用户，是文化叙事体验而非诊疗。你输入的文字会发送到云端处理；故事库可能涉及死亡、身体伤害等敏感材料；本次会话可随时删除。</p>
            <label className="consent-row consent-bundle">
              <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />
              <span><strong>我已年满十八岁，并理解和接受以上说明</strong></span>
            </label>
            <StatusMessage error={error} />
            <button className="primary-action consent-submit" type="button" disabled={!accepted || busy} onClick={begin}>
              {busy ? "正在建立会话…" : "确认并进入相遇"}
            </button>
          </section>
        </div>
      )}
    </main>
  );
}
