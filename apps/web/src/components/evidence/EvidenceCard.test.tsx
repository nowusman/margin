import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EvidenceCard } from "./EvidenceCard";

describe("EvidenceCard", () => {
  it("renders permissions state when provided", () => {
    const markup = renderToStaticMarkup(
      <EvidenceCard
        evidence={{
          sourceTitle: "ADR-001",
          permissionsState: "permission-limited",
        }}
      />
    );
    expect(markup).toContain("Permissions state");
    expect(markup).toContain("permission-limited");
  });

  it("makes restricted evidence explicit and hides the ambiguous empty quote state", () => {
    const markup = renderToStaticMarkup(
      <EvidenceCard
        evidence={{
          redacted: true,
          redactionReason: "source_access_restricted",
        }}
      />
    );

    expect(markup).toContain("Evidence details are restricted by your permissions");
    expect(markup).toContain("The quoted evidence is restricted.");
    expect(markup).not.toContain("No quoted evidence was returned.");
  });
});
