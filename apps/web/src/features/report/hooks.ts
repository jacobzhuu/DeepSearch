import { useState, useEffect, useCallback } from 'react';
import { reportApi } from './api';
import { ReportArtifact } from './types';
import { ApiError } from '../../lib/http';

export function useReport(taskId: string | undefined) {
  const [report, setReport] = useState<ReportArtifact | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchReport = useCallback(async () => {
    if (!taskId) return;
    
    setIsLoading(true);
    setError(null);
    try {
      const data = await reportApi.getReport(taskId);
      setReport(data);
    } catch (err) {
      if (
        err instanceof ApiError &&
        err.status === 404 &&
        err.detail.includes('no markdown report artifact was found')
      ) {
        setReport(null);
        return;
      }
      setError(err instanceof Error ? err : new Error('Failed to fetch report'));
    } finally {
      setIsLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    fetchReport();
  }, [fetchReport]);

  return { report, isLoading, error, refetch: fetchReport };
}
