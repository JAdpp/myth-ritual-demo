import "../english-guide.css";

/* A reader's guide, not a translation of the product.
   The experience runs in Chinese by design; this page exists so someone who
   does not read Chinese can still judge what the thing is and how it works. */

function StageFigure({ variant }: { variant: "encounter" | "articulation" | "theatre" | "keepsake" }) {
  return (
    <svg className="guide-figure" viewBox="0 0 320 180" role="img" aria-hidden="true">
      <rect className="guide-figure-paper" width="320" height="180" rx="6" />
      {variant === "encounter" && (
        <>
          <rect className="guide-panel" x="16" y="18" width="176" height="144" rx="5" />
          <rect className="guide-panel guide-panel-alt" x="204" y="18" width="100" height="144" rx="5" />
          <rect className="guide-bubble" x="30" y="34" width="104" height="20" rx="10" />
          <rect className="guide-bubble guide-bubble-user" x="90" y="62" width="88" height="18" rx="9" />
          <rect className="guide-bubble" x="30" y="88" width="128" height="20" rx="10" />
          <rect className="guide-bubble guide-bubble-user" x="104" y="116" width="74" height="18" rx="9" />
          <g className="guide-rule">
            <path d="M216 40h76M216 54h60M216 68h76M216 82h48" />
          </g>
          <rect className="guide-chip" x="216" y="128" width="76" height="18" rx="9" />
        </>
      )}
      {variant === "articulation" && (
        <>
          <rect className="guide-panel guide-panel-alt" x="16" y="18" width="92" height="144" rx="5" />
          <g className="guide-rule"><path d="M28 40h68M28 54h52M28 68h68M28 82h40M28 96h60" /></g>
          {[0, 1, 2, 3, 4].map((index) => (
            <g key={index}>
              <rect className="guide-node" x="126" y={22 + index * 28} width="170" height="22" rx="4" />
              <circle className="guide-node-dot" cx="136" cy={33 + index * 28} r="3.5" />
              <path className="guide-rule" d={`M148 ${33 + index * 28}h${index % 2 ? 92 : 128}`} />
            </g>
          ))}
        </>
      )}
      {variant === "theatre" && (
        <>
          <rect className="guide-panel" x="16" y="16" width="288" height="106" rx="5" />
          <path className="guide-curtain" d="M16 16h34v106H16zM270 16h34v106h-34z" />
          <circle className="guide-moon" cx="212" cy="52" r="19" />
          <path className="guide-hill" d="M56 122c34-44 66-44 96-8 24-30 62-34 118 8z" />
          <g className="guide-person">
            <circle cx="118" cy="80" r="7" />
            <path d="M118 87v22M118 94l-11 8M118 94l11 8M118 109l-8 14M118 109l8 14" />
          </g>
          <rect className="guide-progress-track" x="16" y="134" width="288" height="5" rx="2.5" />
          <rect className="guide-progress-fill" x="16" y="134" width="176" height="5" rx="2.5" />
          <g className="guide-rule"><path d="M16 154h150M16 166h96" /></g>
        </>
      )}
      {variant === "keepsake" && (
        <>
          <rect className="guide-panel guide-panel-alt" x="52" y="14" width="216" height="90" rx="5" />
          <circle className="guide-seal" cx="160" cy="38" r="13" />
          <g className="guide-rule"><path d="M84 66h152M104 80h112" /></g>
          {[0, 1, 2].map((index) => (
            <g key={index}>
              <rect className="guide-ledger-tag" x="52" y={116 + index * 20} width="52" height="14" rx="7" />
              <path className="guide-rule" d={`M114 ${123 + index * 20}h${[154, 122, 140][index]}`} />
            </g>
          ))}
        </>
      )}
    </svg>
  );
}

const STAGES = [
  {
    key: "encounter" as const,
    ordinal: "01",
    zh: "相遇",
    en: "Encounter",
    lede: "You tell Qidie something that actually happened to you.",
    body: [
      "Qidie (栖蝶, “the settling butterfly”) is the guide. You describe a small, real, recent episode from your own life — a move, an argument, a thing you kept putting off. Qidie answers in two beats: first it says back what it heard, in a way meant to show it landed rather than to summarise you; then it asks one open question that follows the detail you just gave.",
      "The conversation is capped at four turns on purpose, so it cannot turn into an interview. At the end Qidie writes a short neutral summary of what you said. You can edit that summary freely — it, and not the transcript, is what goes on to search the corpus.",
      "The system will not name a myth at this stage, will not diagnose you, and will not ask for your name or contact details.",
    ],
  },
  {
    key: "articulation" as const,
    ordinal: "02",
    zh: "共谱",
    en: "Co-authoring",
    lede: "You write a modern branch alongside the classical tale — not on top of it.",
    body: [
      "You are offered three stories from the corpus, each with the source work it comes from, an excerpt of the original, its original ending, and a plain statement of why it was recommended for what you said. You pick one.",
      "The chosen tale is then laid out beside five prompts — the crack in the ordinary world, crossing the threshold, allies and resources, the new understanding, and what you carry back. You fill them in about your own situation. Qidie can draft or revise any node on request, but every suggestion is shown as a preview that you accept or reject.",
      "The classical text sits in its own column throughout and is never edited. Your branch is stored as a separate versioned document with its own history.",
    ],
  },
  {
    key: "theatre" as const,
    ordinal: "03",
    zh: "再演",
    en: "Re-enactment",
    lede: "Your branch is performed back to you as a small illustrated theatre.",
    body: [
      "The classical tale and your five nodes are woven into one continuous five-act story set in your present-day circumstances. A character from the source canon appears in it as an actual character — either arriving across time, still carrying the objects and obsessions of the original, or as someone in modern life who echoes them in bearing and situation. A passing mention does not count; they get a position, actions, and at least one exchange with you.",
      "Each act is illustrated in gongbi lianhuanhua style — the meticulous ink-outline-and-mineral-colour painting of the Song and Yuan academies — and read aloud. The page turns when the reading of an act finishes, not on a fixed timer, so the pacing follows the voice.",
      "The original ending of the classical tale is never rewritten. The branch is yours; the canon stays as it was found.",
    ],
  },
  {
    key: "keepsake" as const,
    ordinal: "04",
    zh: "纪念卡",
    en: "Keepsake and ledger",
    lede: "You title the version, close it with a gesture, and see exactly where every line came from.",
    body: [
      "You name your branch, choose or rewrite its last line, and close with one of two gestures — lighting a lamp at the story's exit, or stamping the keepsake card with a seal.",
      "Alongside the card is a provenance ledger. Every segment is tagged with its origin: drawn from the source canon, written by you, phrased by the model, or an interpretive bridge between the two. Nothing is presented as coming from the classics that does not.",
      "You then decide whether the card is kept or discarded. Deleting the session removes your text, your branch, the script and the card, and returns a receipt you can verify; the corpus itself is untouched.",
    ],
  },
];

const WORKS = [
  { zh: "太平廣記", en: "Taiping Guangji", note: "Extensive Records of the Taiping Era, 978 CE", count: 6995 },
  { zh: "夷堅志", en: "Yijian Zhi", note: "Records of the Listener, 12th c.", count: 2646 },
  { zh: "閱微草堂筆記", en: "Yuewei Caotang Biji", note: "Jottings from the Thatched Abode of Close Observations, 1789–98", count: 1198 },
  { zh: "子不語", en: "Zi Bu Yu", note: "What the Master Would Not Discuss, 1788", count: 745 },
  { zh: "聊齋志異", en: "Liaozhai Zhiyi", note: "Strange Tales from a Chinese Studio, 17th c.", count: 492 },
  { zh: "續子不語", en: "Xu Zi Bu Yu", note: "Sequel to What the Master Would Not Discuss", count: 277 },
];

const BOUNDARIES = [
  {
    title: "The source canon is read-only",
    body: "Every story record carries a hash of its source text. The hash is re-checked at each step that touches it, and a mismatch stops the session rather than proceeding quietly. Nothing you or the model writes is ever merged back into it.",
  },
  {
    title: "Origin is tracked line by line",
    body: "The provenance ledger marks each segment as source canon, user-created, model expression, or interpretive bridge. The distinction between “the classics say this” and “this was phrased for you just now” is kept visible instead of being smoothed away.",
  },
  {
    title: "Distress stops the personalised flow",
    body: "A safety router reads every message before anything else runs. If it finds an expression that may need immediate support, the personalised path halts and the page shows the national mental-health line and emergency numbers. There is no real-time human monitoring, and the demo says so rather than implying otherwise.",
  },
  {
    title: "This is not clinical",
    body: "Nothing here diagnoses, treats, or advises. Qidie is instructed not to praise, label, judge, or assert feelings you did not express, and not to claim that you must grow, forgive, or move on.",
  },
  {
    title: "The session is the unit of storage",
    body: "Text is held for the session and deleted with it. Your raw words are never written into the event log; only structural metadata is. Cloud processing is a separate, explicit consent — decline it and the whole flow still runs, on local deterministic wording.",
  },
];

const STACK = [
  { role: "Language", detail: "DeepSeek — the two-beat conversation, the neutral summary, node suggestions, and weaving the classical tale together with your branch into a five-act script." },
  { role: "Retrieval", detail: "Search across the segmented corpus, with a plan and a re-ranking pass, then a recommendation that states the cue in your words and the matching thread in the story." },
  { role: "Illustration", detail: "z-image-turbo — gongbi lianhuanhua scenes for the theatre, and pure ink baimiao (line-only, uncoloured) plates for the story cards." },
  { role: "Narration", detail: "CosyVoice (cosyvoice-v3-flash, voice longyue_v3 “Longyue”, 0.9× rate). Playback falls back to browser speech, and then to subtitles alone, without blocking the performance." },
];

export function EnglishGuide({ onBackToExperience }: { onBackToExperience: () => void }) {
  return (
    <main id="main" className="guide-page" lang="en">
      <header className="guide-hero">
        <p className="guide-kicker">A reader’s guide · ICHEC 2026 demonstration</p>
        <h1>
          梦蝶记
          <span>Dream-Butterfly Record</span>
        </h1>
        <p className="guide-lede">
          A person describes something that recently happened to them. The system finds classical
          Chinese tales that rhyme with it, the person co-writes a modern branch of the tale they
          choose, and then watches that branch performed back as a small illustrated theatre with
          narration. What they keep at the end is a titled card and a ledger of where every line
          came from.
        </p>
        <p className="guide-note">
          The experience itself runs in Chinese. The corpus is classical Chinese, and the
          co-authoring turns on nuance in the visitor’s own words, so translating the flow would
          change what it is. This guide is here so the work can be understood and judged without
          reading Chinese.
        </p>
        <div className="guide-hero-actions">
          <button type="button" className="guide-primary" onClick={onBackToExperience}>
            前往中文体验 · Go to the Chinese experience
          </button>
        </div>
      </header>

      <section className="guide-section" aria-labelledby="guide-name">
        <h2 id="guide-name">Where the name comes from</h2>
        <blockquote className="guide-quote">
          <p>
            Zhuang Zhou dreamt he was a butterfly. Waking, he did not know whether he was Zhuang
            Zhou who had dreamt he was a butterfly, or a butterfly now dreaming it was Zhuang Zhou.
          </p>
          <cite>— 庄子·齐物论, Zhuangzi, “On the Equality of Things”, 4th c. BCE</cite>
        </blockquote>
        <p>
          梦蝶记 means “a record of dreaming the butterfly”. The demo sits in the gap that parable
          opens — between a life as it was lived and a story it can be told through — and tries to
          keep both sides of the gap legible rather than collapsing one into the other. The guide
          who accompanies the visitor is called 栖蝶 <em>Qīdié</em>, “the settling butterfly”.
        </p>
      </section>

      <section className="guide-section" aria-labelledby="guide-stages">
        <h2 id="guide-stages">Walking through it</h2>
        <ol className="guide-stages">
          {STAGES.map((stage) => (
            <li key={stage.key} className="guide-stage">
              <div className="guide-stage-art">
                <StageFigure variant={stage.key} />
              </div>
              <div className="guide-stage-copy">
                <p className="guide-stage-ordinal">
                  <span>{stage.ordinal}</span>
                  <b lang="zh-Hans">{stage.zh}</b>
                  {stage.en}
                </p>
                <h3>{stage.lede}</h3>
                {stage.body.map((paragraph) => (
                  <p key={paragraph.slice(0, 24)}>{paragraph}</p>
                ))}
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="guide-section" aria-labelledby="guide-corpus">
        <h2 id="guide-corpus">Where the stories come from</h2>
        <p>
          The recommendation pool is <strong>12,353 tales</strong> segmented from six classical
          collections of the strange and the anecdotal, spanning roughly the 10th to the 18th
          century. Thirty of them are additionally annotated in depth — with an excerpt, the
          original ending, the motifs, and an explicit statement of what an adaptation must
          preserve and what it may change — and it is these that drive the recommendation
          explanations.
        </p>
        <ul className="guide-works">
          {WORKS.map((work) => (
            <li key={work.en}>
              <span className="guide-work-title" lang="zh-Hant">{work.zh}</span>
              <span className="guide-work-roman">{work.en}</span>
              <span className="guide-work-note">{work.note}</span>
              <span className="guide-work-count">{work.count.toLocaleString("en-US")}</span>
            </li>
          ))}
        </ul>
        <p className="guide-note">
          Every card shows its source work and locator, and links out to the text where one is
          available. Illustrations are generated for the demo and are labelled as such — none of
          them is presented as a historical image.
        </p>
      </section>

      <section className="guide-section" aria-labelledby="guide-boundaries">
        <h2 id="guide-boundaries">What it deliberately will not do</h2>
        <p>
          The design problem here is not “can a model retell a myth about you”. It is what a system
          owes someone who has just handed it a piece of their life, and what it owes a text that
          has survived a thousand years. Most of the engineering is in the refusals.
        </p>
        <dl className="guide-boundaries">
          {BOUNDARIES.map((item) => (
            <div key={item.title}>
              <dt>{item.title}</dt>
              <dd>{item.body}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="guide-section" aria-labelledby="guide-stack">
        <h2 id="guide-stack">How it is built</h2>
        <ul className="guide-stack">
          {STACK.map((item) => (
            <li key={item.role}>
              <span>{item.role}</span>
              <p>{item.detail}</p>
            </li>
          ))}
        </ul>
        <p className="guide-note">
          Every generated layer degrades rather than blocks. If an illustration fails the act falls
          back to a locally drawn paper-shadow stage; if narration fails the subtitles remain; if
          the language model is unavailable the conversation continues on deterministic wording
          built from the visitor’s own phrases. No provider key or prompt ever reaches the browser.
        </p>
      </section>

      <section className="guide-section guide-closing" aria-labelledby="guide-try">
        <h2 id="guide-try">Trying it yourself</h2>
        <p>
          The flow takes about ten minutes. If you do not read Chinese, the shape is still legible:
          four numbered stages across the top, a chat on the left and an editable summary on the
          right, then three story cards, then a five-row co-authoring table, then a curtain that
          opens onto the performance.
        </p>
        <button type="button" className="guide-primary" onClick={onBackToExperience}>
          前往中文体验 · Go to the Chinese experience
        </button>
      </section>
    </main>
  );
}
