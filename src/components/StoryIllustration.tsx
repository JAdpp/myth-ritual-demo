import { useId } from "react";
import { toSimplifiedDisplay } from "../lib/display-text";

function sceneGlyph(family: string) {
  switch (family) {
    case "pangu_cosmogony":
      return <><ellipse cx="150" cy="92" rx="62" ry="76" /><path d="M101 50c28 24 68 25 101 3M150 18v150M122 93h56" /><circle cx="150" cy="82" r="9" /></>;
    case "kuafu_sun_chase":
      return <><circle cx="224" cy="48" r="30" /><path d="M55 143c33-28 66-21 91 8 27-61 73-64 112-10M125 119l24-43 22 20-18 18 31 39M145 77l-19-25M66 148v-42m-14 18h28" /></>;
    case "gun_yu_flood_control":
      return <><path d="M21 66c40-31 79 27 119-3s80 26 139-8M20 99c43-29 80 27 124-3s80 26 136-8M20 133c37-30 74 25 110-1 22-16 38-10 52 0 28 20 55 18 98-8" /><path d="M153 27c-7 35-3 70 13 104M169 55l20-15M163 86l-25-13" /></>;
    case "change_flight_to_moon":
      return <><circle cx="201" cy="77" r="55" /><path d="M199 31c-16 15-20 35-7 49 13 13 36 13 58 1M67 132c35-4 49-34 76-48 22-11 35-9 51-4M97 113c-2-30 14-49 37-58M116 58c1-13 7-23 19-31M124 59c-7-11-7-22 0-32" /></>;
    case "mulan_substitution":
      return <><path d="M73 140c23-21 38-54 38-94 24 7 42 4 59-10 19 17 38 18 59 10 0 42 14 73 38 94M83 140h174" /><path d="M133 74h74M170 47v76M139 108l31-34 31 34" /><circle cx="69" cy="53" r="14" /></>;
    case "yellow_millet_dream":
      return <><path d="M77 116h146c-8 29-32 42-73 42s-65-13-73-42ZM93 116c7-20 15-31 30-39M132 104c-8-39 12-63 45-77 17 27 15 51-7 72M179 97c9-16 21-25 37-29" /><path d="M101 163h98" /></>;
    case "white_snake_legend":
      return <><path d="M35 128c51-7 65-53 105-55 37-2 52 46 87 45 22-1 35-17 38-38" /><path d="M58 89c31-36 63-54 96-54 34 0 64 17 90 50M72 92h159M91 92l-9 50m128-50 9 50" /><circle cx="267" cy="69" r="7" /></>;
    case "ganjiang_moye":
      return <><path d="M82 33l130 125M217 28 88 156M74 29l24 3-15 17M227 25l-4 24-18-15M63 159h54m67 0h54" /><circle cx="150" cy="95" r="34" /></>;
    case "nuwa_mends_sky":
    case "nuwa_repairs_sky":
    case "nvwa_mends_sky":
      return <><path d="M35 47 87 28l40 27 43-31 45 28 51-18v115H35Z" /><path d="m127 55 14 24-18 20 17 27M87 28l7 35-20 18M215 52l-21 27 8 30" /><circle cx="150" cy="69" r="12" /></>;
    case "jingwei_fills_sea":
    case "jingwei_reclamation":
      return <><path d="M19 119c38-27 75 25 113-1 38-27 76 24 149-10M19 147c38-27 75 25 113-1 38-27 76 24 149-10" /><path d="M73 73c26-27 52-29 79-5-29 2-42 17-46 43M151 68c20-20 39-22 58-7M111 51l16-20 13 24M159 83l51 27" /></>;
    case "yugong_moves_mountains":
      return <><path d="M24 151 82 60l34 49 49-83 52 78 28-39 35 86Z" /><path d="M65 151c37-13 63-30 83-52 23-24 50-36 82-33M139 143l16-20 14 19" /></>;
    case "zhuangzi_butterfly_dream":
    case "zhuangzhou_butterfly_dream":
      return <><path d="M148 88c-33-43-74-47-91-21-15 23 7 54 66 50M152 88c33-43 74-47 91-21 15 23-7 54-66 50M150 84v60M150 89l-18-29m18 29 18-29" /><circle cx="150" cy="78" r="5" /></>;
    case "fox_borrows_tiger_might":
      return <><path d="M53 133c16-53 44-80 84-80 20 0 36 8 48 23 25-7 47 4 64 31M105 54 91 30m70 31 20-27M74 132h155M133 78l13 11 14-11M114 101c23 15 48 15 73 0" /><path d="M217 108c13 7 25 18 35 34M71 110 45 91" /></>;
    case "houyi_shoots_suns":
      return <><circle cx="225" cy="48" r="24" /><circle cx="195" cy="31" r="9" /><circle cx="252" cy="78" r="9" /><path d="M62 142 166 55M82 124l28 4m-9-24 5 28M151 75l50-19-31-20M69 144h90" /></>;
    case "nvwa_creates_humans":
      return <><path d="M54 143c31-50 60-81 89-91 32-11 68 5 102 52M122 77c3 28 1 51-7 69m61-76c-7 21-5 47 6 76" /><circle cx="120" cy="65" r="12" /><circle cx="178" cy="60" r="12" /><path d="M84 145c9-30 30-45 62-45 33 0 55 15 68 45" /></>;
    case "shennong_tastes_herbs":
      return <><path d="M150 151V39M148 74c-31-2-50-18-55-46 27 2 47 17 57 44Zm3 31c34-3 55-19 62-48-31 1-52 18-62 46Zm-2 24c-25-1-42-13-50-36 24 1 41 12 50 34Z" /><path d="M183 135c18-23 37-28 56-16-10 20-29 28-56 16ZM60 148h181" /></>;
    case "gonggong_hits_buzhou":
      return <><path d="M27 151 93 45l39 52 41-72 33 67 28-45 39 104" /><path d="m172 26-18 38 22 20-29 28 18 39M54 150c47-8 83-5 112 7" /><circle cx="232" cy="39" r="12" /></>;
    case "xingtian_dances":
      return <><path d="M112 67c20-19 57-18 77 2v58c-22 18-53 18-77 0Zm19 20 13 8 14-8M144 96v18M112 79 70 46m119 34 43-34M113 120l-39 32m114-32 38 32" /><path d="M61 35 81 55M225 38l20 17M54 29l33 33m126-1 39-37" /></>;
    case "wu_gang_cuts_osmanthus":
      return <><circle cx="222" cy="51" r="36" /><path d="M114 150V45m0 20-36-24m36 54-48-14m48 38 38-28m-38-17 41-30M81 149h76M174 136l35-72m-47 51 58 27" /><circle cx="81" cy="42" r="7" /><circle cx="151" cy="43" r="7" /></>;
    case "cowherd_weaver_girl":
      return <><path d="M24 91c40-22 69 21 104 0s66 18 148-5M24 108c42-20 67 21 104 1 36-20 68 15 148-7" /><circle cx="78" cy="52" r="8" /><circle cx="225" cy="47" r="8" /><path d="M83 58c32 9 52 19 66 31 19-15 40-25 68-34M63 143h45m83 0h48" /></>;
    case "mengjiangnu_great_wall":
      return <><path d="M31 139V78h44V56h43v22h44V53h45v25h61v61M31 105h237M75 78v61m43-61v61m44-61v61m45-61v61" /><path d="m140 79-13 23 18 17-21 20M53 156c49-18 91 5 121-2 34-8 66-18 94-9" /></>;
    case "butterfly_lovers":
      return <><path d="M146 86c-32-42-72-46-89-21-15 22 7 52 64 48M154 86c32-42 72-46 89-21 15 22-7 52-64 48M150 82v62M150 88l-19-31m19 31 19-31" /><path d="M64 150c40-17 74-15 102 5 21-12 44-15 70-7" /></>;
    case "snail_maiden":
      return <><path d="M69 137c-1-46 28-78 76-78 44 0 73 26 73 65 0 21-14 34-35 34-24 0-39-16-39-36 0-18 13-29 29-29 14 0 23 9 23 21 0 10-7 17-16 17" /><path d="M62 143h172M88 60c20-20 42-29 67-28" /></>;
    case "peach_blossom_spring":
      return <><path d="M151 155c-9-34-7-64 5-91 8-18 20-31 36-39M156 66c-27-8-47-4-62 13m64 8c24-10 47-8 67 6M136 112c-26-8-48-3-66 15" /><circle cx="92" cy="74" r="8" /><circle cx="217" cy="90" r="8" /><circle cx="67" cy="126" r="7" /><path d="M24 154c49-25 85-17 126 0 38-27 77-30 126-3" /></>;
    case "painted_skin":
      return <><path d="M87 43c36-25 90-25 126 0l-10 78c-14 26-32 39-53 39s-39-13-53-39Z" /><path d="M113 79c9-8 20-8 30 0m14 0c10-8 20-8 30 0M132 119c13 8 25 8 37 0M81 50l-31 26m169-26 31 26" /><path d="M150 43v67" /></>;
    case "liu_yi_delivers_letter":
      return <><path d="M21 125c41-26 75 23 115-2 41-25 75 23 143-8M21 150c41-26 75 23 115-2 41-25 75 23 143-8" /><path d="M83 49h94v67H83Zm0 0 47 37 47-37M189 111c9-37 29-58 59-62 15 24 9 46-18 64M228 49l4-24m7 27 20-18" /></>;
    case "old_man_lost_horse":
      return <><path d="M48 134c28-51 61-77 99-77 30 0 50 14 59 42l39 13-24 24h-60l-22 20m-20-45-9 45m69-60 4-24m-76 10-21-16" /><circle cx="183" cy="75" r="3" /><path d="M42 157h205" /></>;
    case "farmer_waits_for_rabbit":
      return <><path d="M121 151V55h50v96M105 55h83M201 133c11-24 30-35 54-30 9 20 3 37-18 50M221 104l-2-28m14 30 14-24M44 151c9-30 31-43 66-39" /><circle cx="230" cy="128" r="18" /></>;
    case "marking_boat_for_sword":
      return <><path d="M45 111h206c-12 32-45 48-101 48s-91-16-105-48ZM72 111l27-45h91l32 45" /><path d="M151 35v84m-10-75 20-9m-16 83 12 10M23 153c20-8 36-8 54 0m145 0c19-8 36-8 55 0" /></>;
    case "lord_ye_loves_dragons":
      return <><path d="M48 126c17-53 54-74 99-53 25 12 52 7 74-11 24-20 41-17 51 8-24-4-39 6-45 30-8 33-34 51-73 51-35 0-58-13-69-40" /><path d="M222 63l3-26m13 30 20-17M96 83 72 54m42 25-5-36M143 101c15 9 31 9 48 0" /><circle cx="239" cy="77" r="3" /></>;
    case "boya_breaks_strings":
      return <><path d="M51 142c35-45 70-65 105-60 29 5 60 26 93 62M86 127c45-22 87-25 127-8M101 94l19 48m16-57 10 57m18-55 4 55m22-48-3 48" /><path d="M51 152h198M115 50c20-23 43-31 69-23" /></>;
    case "nanke_dream":
      return <><path d="M43 145h214M73 145V78h41V55h72v23h41v67M114 78h72M96 97h18m20 0h32m20 0h18M96 119h18m20 0h32m20 0h18" /><path d="M63 55c21-28 47-40 79-34m81 25c18 7 31 20 39 40" /><circle cx="144" cy="35" r="7" /></>;
    default:
      return null;
  }
}

function StoryTitleLeaf({ id, title }: { id: string; title: string }) {
  return (
    <figure className="story-illustration story-title-leaf" data-illustration-state="fallback">
      <div
        className="story-title-leaf__body"
        data-illustration-kind="title-leaf"
        role="img"
        aria-labelledby={`${id}-title ${id}-desc`}
      >
        <div className="story-title-leaf__copy">
          <span aria-hidden="true">来源库题名</span>
          <strong id={`${id}-title`}>{title}</strong>
          <span id={`${id}-desc`}>依据题名排版，非古籍原图。</span>
        </div>
        <span className="story-title-leaf__folio" aria-hidden="true">叶签</span>
      </div>
      <figcaption>题名叶签 · 非古籍原图</figcaption>
    </figure>
  );
}

export function StoryIllustration({
  family,
  title,
  imageUrl,
  imageAlt,
  generationSource,
  isLoading = false,
  onImageError,
}: {
  family: string;
  title: string;
  /** Generated ink line-drawing. The local glyph stands in until it arrives. */
  imageUrl?: string | null;
  imageAlt?: string;
  generationSource?: string | null;
  /** The cover request is in flight.  This has its own quiet plate so the
      local fallback never masquerades as the generated white drawing. */
  isLoading?: boolean;
  onImageError?: () => void;
}) {
  const id = useId().replace(/:/g, "");
  const displayTitle = toSimplifiedDisplay(title).trim() || "无题";
  const glyph = sceneGlyph(family);

  if (imageUrl) {
    return (
      <figure className={`story-illustration story-illustration-generated illustration-${family}`} data-illustration-state="ready">
        <img
          src={imageUrl}
          alt={toSimplifiedDisplay(imageAlt ?? `${displayTitle}白描题图`)}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={onImageError}
        />
        <figcaption>{generationSource === "aliyun_image_model" ? "阿里云模型生成白描题图" : "模型生成白描题图"}</figcaption>
      </figure>
    );
  }

  if (isLoading) {
    return (
      <figure className="story-illustration story-illustration-pending" data-illustration-state="loading" aria-busy="true">
        <div className="story-illustration-pending__plate" role="img" aria-label={`正在绘制《${displayTitle}》的白描题图`}>
          <span className="story-illustration-pending__brush story-illustration-pending__brush--one" aria-hidden="true" />
          <span className="story-illustration-pending__brush story-illustration-pending__brush--two" aria-hidden="true" />
          <span className="story-illustration-pending__butterfly" aria-hidden="true" />
          <p>正在绘制白描题图</p>
        </div>
        <figcaption>题图准备中</figcaption>
      </figure>
    );
  }

  if (!glyph) return <StoryTitleLeaf id={id} title={displayTitle} />;

  return (
    <figure className={`story-illustration illustration-${family}`} data-illustration-state="fallback">
      <svg viewBox="0 0 300 180" role="img" aria-labelledby={`${id}-title ${id}-desc`}>
        <title id={`${id}-title`}>{`${displayTitle}象征插画`}</title>
        <desc id={`${id}-desc`}>依据故事核心意象绘制的编辑性叙事线描，不是古籍原图。</desc>
        <rect width="300" height="180" rx="2" fill="var(--illustration-paper, #dfe9e6)" />
        <g className="story-illustration-glyph">{glyph}</g>
      </svg>
      <figcaption>叙事线描</figcaption>
    </figure>
  );
}
