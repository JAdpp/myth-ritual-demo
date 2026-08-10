import type {
  DeletionReceipt,
  ExperienceBrief,
  ExperienceBriefSafetyRoute,
  SafetyStopRoute,
} from "../types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isSafetyStopRoute(
  route: ExperienceBriefSafetyRoute | undefined,
): route is SafetyStopRoute {
  return (
    isRecord(route)
    && route.blocked === true
    && route.route === "crisis_stop"
    && Array.isArray(route.categories)
  );
}

export function getSafetyStopRoute(brief: ExperienceBrief | null): SafetyStopRoute | null {
  return isSafetyStopRoute(brief?.safetyRoute) ? brief.safetyRoute : null;
}

export function isDeletionReceiptFor(
  value: unknown,
  sessionId: string,
): value is DeletionReceipt {
  return (
    isRecord(value)
    && value.sessionId === sessionId
    && value.deleted === true
    && typeof value.deletedAt === "string"
    && value.deletedAt.length > 0
    && typeof value.deletionProof === "string"
    && value.deletionProof.length > 0
  );
}
