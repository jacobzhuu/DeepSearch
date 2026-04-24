import { useState, useEffect, useCallback } from 'react';
import { sourcesApi } from './api';
import { SourceDocumentListResponse, SourceChunkListResponse } from './types';

export function useSources(taskId: string | undefined) {
  const [documentsData, setDocumentsData] = useState<SourceDocumentListResponse | null>(null);
  const [chunksData, setChunksData] = useState<SourceChunkListResponse | null>(null);
  
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchSourcesAndChunks = useCallback(async () => {
    if (!taskId) return;
    
    setIsLoading(true);
    setError(null);
    try {
      const [docsRes, chunksRes] = await Promise.all([
        sourcesApi.getSourceDocuments(taskId),
        sourcesApi.getSourceChunks(taskId)
      ]);
      setDocumentsData(docsRes);
      setChunksData(chunksRes);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to fetch sources and chunks'));
    } finally {
      setIsLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    fetchSourcesAndChunks();
  }, [fetchSourcesAndChunks]);

  return { documentsData, chunksData, isLoading, error, refetch: fetchSourcesAndChunks };
}
