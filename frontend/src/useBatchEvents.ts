import { useEffect, useState } from "react";

import type { JobState } from "./api";

export interface JobEvent {
  job_id: string;
  file_id: string | null;
  type: string;
  status: JobState["status"];
  progress: number;
  stage: string | null;
  error_code: string | null;
  attempts: number;
}

export function toJobState(e: JobEvent): JobState {
  return {
    id: e.job_id,
    status: e.status,
    progress: e.progress,
    stage: e.stage,
    error_code: e.error_code,
    attempts: e.attempts,
  };
}

/** État des jobs du lot, par fichier, tenu à jour par le flux SSE /api/events. */
export function useBatchEvents(batchId: string): {
  jobsByFile: Record<string, JobState>;
  connected: boolean;
} {
  const [jobsByFile, setJobs] = useState<Record<string, JobState>>({});
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setJobs({});
    const source = new EventSource(`/api/events?batch=${encodeURIComponent(batchId)}`);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false); // EventSource se reconnecte seul
    source.addEventListener("job", (msg) => {
      const event = JSON.parse((msg as MessageEvent<string>).data) as JobEvent;
      if (event.type !== "process_file" || !event.file_id) return;
      const fileId = event.file_id;
      setJobs((prev) => ({ ...prev, [fileId]: toJobState(event) }));
    });
    return () => source.close();
  }, [batchId]);

  return { jobsByFile, connected };
}
