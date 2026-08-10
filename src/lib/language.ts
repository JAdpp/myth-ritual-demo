import { useCallback, useEffect, useState } from "react";

export type SiteLanguage = "zh" | "en";

const STORAGE_KEY = "mengdie.language";

function readStoredLanguage(): SiteLanguage {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "zh";
  } catch {
    // Private-mode or blocked storage is not a reason to fail; default to 中文.
    return "zh";
  }
}

/** The site language, persisted so a reader who switched stays switched.
 *
 * Only the guide is translated: the experience itself runs in Chinese, because
 * the corpus is classical Chinese and the co-authoring turns on nuance in the
 * visitor's own words. `en` therefore selects the English guide rather than an
 * English build of the experience.
 */
export function useSiteLanguage(): [SiteLanguage, (next: SiteLanguage) => void] {
  const [language, setLanguage] = useState<SiteLanguage>(readStoredLanguage);

  useEffect(() => {
    document.documentElement.lang = language === "en" ? "en" : "zh-Hans";
  }, [language]);

  const choose = useCallback((next: SiteLanguage) => {
    setLanguage(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Losing the preference is survivable; blocking the switch is not.
    }
  }, []);

  return [language, choose];
}
