import { fetchApi } from '../../lib/http';
import { SourceDocumentListResponse, SourceChunkListResponse } from './types';

export const sourcesApi = {
  getSourceDocuments: async (taskId: string): Promise<SourceDocumentListResponse> => {
    return fetchApi<SourceDocumentListResponse>(`/api/v1/research/tasks/${taskId}/source-documents`, {
      method: 'GET',
    });
  },

  getSourceChunks: async (taskId: string): Promise<SourceChunkListResponse> => {
    return fetchApi<SourceChunkListResponse>(`/api/v1/research/tasks/${taskId}/source-chunks`, {
      method: 'GET',
    });
  },
};
