import { fetchApi } from '../../lib/http';
import { ClaimListResponse, ClaimEvidenceListResponse } from './types';

export const claimsApi = {
  getClaims: async (taskId: string): Promise<ClaimListResponse> => {
    return fetchApi<ClaimListResponse>(`/api/v1/research/tasks/${taskId}/claims`, {
      method: 'GET',
    });
  },

  getClaimEvidence: async (taskId: string): Promise<ClaimEvidenceListResponse> => {
    return fetchApi<ClaimEvidenceListResponse>(`/api/v1/research/tasks/${taskId}/claim-evidence`, {
      method: 'GET',
    });
  },
};
