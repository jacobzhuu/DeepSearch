import { useState, useEffect, useCallback } from 'react';
import { claimsApi } from './api';
import { ClaimListResponse, ClaimEvidenceListResponse } from './types';

export function useClaims(taskId: string | undefined) {
  const [claimsData, setClaimsData] = useState<ClaimListResponse | null>(null);
  const [evidenceData, setEvidenceData] = useState<ClaimEvidenceListResponse | null>(null);
  
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchClaimsAndEvidence = useCallback(async () => {
    if (!taskId) return;
    
    setIsLoading(true);
    setError(null);
    try {
      const [claimsRes, evidenceRes] = await Promise.all([
        claimsApi.getClaims(taskId),
        claimsApi.getClaimEvidence(taskId)
      ]);
      setClaimsData(claimsRes);
      setEvidenceData(evidenceRes);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to fetch claims and evidence'));
    } finally {
      setIsLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    fetchClaimsAndEvidence();
  }, [fetchClaimsAndEvidence]);

  return { claimsData, evidenceData, isLoading, error, refetch: fetchClaimsAndEvidence };
}
