import { useState } from "react";

export const QIDIE_AVATAR_SRC = "/assets/qidie-guide-avatar-chibi-v1.webp";

export function QidieAvatar({
  size = 36,
  className,
}: {
  size?: number;
  className?: string;
}) {
  const [imageMissing, setImageMissing] = useState(false);

  if (imageMissing) {
    return (
      <span
        className={[className, "qidie-avatar-fallback"].filter(Boolean).join(" ")}
        role="img"
        aria-label="栖蝶头像"
        style={{ width: size, height: size }}
      >
        栖蝶
      </span>
    );
  }

  return (
    <img
      className={className}
      src={QIDIE_AVATAR_SRC}
      alt="栖蝶头像"
      width={size}
      height={size}
      decoding="async"
      draggable={false}
      onError={() => setImageMissing(true)}
    />
  );
}
