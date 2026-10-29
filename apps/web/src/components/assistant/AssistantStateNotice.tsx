import React from "react";

type AssistantStateNoticeProps = {
  responseMode: "hybrid" | "lexical_only" | "search_only";
  answerUnavailableReason?: string | null;
  warnings?: string[];
};

const responseModeMessage = {
  hybrid: "Results combine lexical and semantic retrieval.",
  lexical_only: "Semantic retrieval is unavailable; results use lexical retrieval only.",
  search_only: "A generated answer is unavailable; the cited search results remain available.",
};

export function AssistantStateNotice({
  responseMode,
  answerUnavailableReason,
  warnings = [],
}: AssistantStateNoticeProps): React.JSX.Element {
  return (
    <section aria-live="polite" className="assistant-state-notice">
      <p className="subtle">{responseModeMessage[responseMode]}</p>
      {answerUnavailableReason ? (
        <p role="status">Answer unavailable: {answerUnavailableReason}</p>
      ) : null}
      {warnings.length > 0 ? (
        <ul className="subtle" aria-label="Operational warnings">
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
