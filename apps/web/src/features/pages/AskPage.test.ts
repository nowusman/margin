import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { splitAnswerInlineCitations } from "./AskPage";

describe("AskPage inline citation parser", () => {
  it("keeps plain text when no citations are present", () => {
    expect(splitAnswerInlineCitations("No cited answer")).toEqual([
      { kind: "text", value: "No cited answer" },
    ]);
  });

  it("extracts stable answer-synthesis citation IDs inline", () => {
    const ideaBlockCitation = `cit_${"a".repeat(32)}`;
    const sourceCitation = `cite-${"b".repeat(32)}`;
    const parts = splitAnswerInlineCitations(
      `Claim one [${ideaBlockCitation}] and claim two [${sourceCitation}]`
    );

    expect(parts).toEqual([
      { kind: "text", value: "Claim one " },
      { kind: "citation", citationId: ideaBlockCitation },
      { kind: "text", value: " and claim two " },
      { kind: "citation", citationId: sourceCitation },
    ]);
  });

  it("uses the governed retrieval contract", () => {
    const source = readFileSync(new URL("./AskPage.tsx", import.meta.url), "utf-8");

    expect(source).toContain("assistantClient.filterOptions");
    expect(source).toContain("responseMode");
  });

  it("opens cited evidence through the canonical exact source-element route", () => {
    const askSource = readFileSync(new URL("./AskPage.tsx", import.meta.url), "utf-8");
    const globalSearchSource = readFileSync(
      new URL("../../components/search/GlobalSearchCommand.tsx", import.meta.url),
      "utf-8"
    );

    expect(askSource).toContain('onNavigate("/sources/:sourceId/elements/:elementId"');
    expect(askSource).not.toContain("Exact source-element navigation requires");
    expect(globalSearchSource).toContain('route: "/sources/:sourceId/elements/:elementId"');
    expect(globalSearchSource).not.toContain("unavailableReason");
  });
});
