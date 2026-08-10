import { describe, expect, it } from "vitest";

import { getSafetyStopRoute, isDeletionReceiptFor, isSafetyStopRoute } from "./contracts";

describe("runtime API contract guards", () => {
  it("recognizes the structured crisis stop returned by the API", () => {
    const route = {
      blocked: true as const,
      route: "crisis_stop" as const,
      categories: ["self_harm"],
      support: {
        chinaMentalHealthHotline: "12356",
        emergency: ["110", "120"],
        realTimeMonitoring: false,
      },
    };

    expect(isSafetyStopRoute(route)).toBe(true);
    expect(
      getSafetyStopRoute({
        id: "brief-1",
        version: 1,
        inputMode: "text",
        neutralSummary: "",
        confirmed: false,
        safetyRoute: route,
      }),
    ).toEqual(route);
    expect(isSafetyStopRoute("standard")).toBe(false);
  });

  it("accepts a deletion receipt only for the session that was deleted", () => {
    const receipt = {
      sessionId: "session-1",
      deleted: true,
      deletedAt: "2026-08-07T00:00:00Z",
      deletionProof: "proof-1",
    };

    expect(isDeletionReceiptFor(receipt, "session-1")).toBe(true);
    expect(isDeletionReceiptFor(receipt, "session-2")).toBe(false);
    expect(isDeletionReceiptFor({ ...receipt, deletionProof: "" }, "session-1")).toBe(false);
  });
});
