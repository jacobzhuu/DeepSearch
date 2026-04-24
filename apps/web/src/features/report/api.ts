import { fetchApi } from '../../lib/http';
import { ReportArtifact } from '../../types/api';

export const reportApi = {
  getReport: async (taskId: string): Promise<ReportArtifact> => {
    return fetchApi<ReportArtifact>(`/api/v1/research/tasks/${taskId}/report`, {
      method: 'GET',
    });
  },
};
