import { useEffect } from "react";

import { personalClient } from "./apiPersonal";

/**
 * Record that the reader opened a record.
 *
 * The personal feed and the recency signal both read from what a reader has
 * actually opened, so the record surfaces report it. A failed record is not
 * worth interrupting the reader for.
 */
export function useRecordView(targetType: string, targetId: string, domainId = ""): void {
  useEffect(() => {
    if (!targetId) return;
    void personalClient
      .recordView(targetType, targetId, domainId || undefined)
      .catch(() => undefined);
  }, [targetType, targetId, domainId]);
}
