import { describe, expect, it } from "vitest";
import {
  acceptedUploadFormats,
  detectSourceTypeOption,
  supportedUploadFormatLabel,
} from "./SourcesPage.helpers";

describe("source upload formats", () => {
  it("supports PDF and PPTX in the Knowledge Upload card", () => {
    expect(acceptedUploadFormats.split(",")).toEqual(expect.arrayContaining([".pdf", ".pptx"]));
    expect(supportedUploadFormatLabel).toContain("PDF");
    expect(supportedUploadFormatLabel).toContain("PPTX");
  });

  it("detects PDF and PPTX uploads from file names", () => {
    expect(detectSourceTypeOption("architecture-overview.pdf")?.label).toBe("PDF");
    expect(detectSourceTypeOption("roadmap.PPTX")?.label).toBe("PPTX");
  });
});
