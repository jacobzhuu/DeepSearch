import { fetchApi } from '../../lib/http';
import { ResearchTask } from '../../types/api';
import {
  CreateTaskRequest,
  CreateTaskResponse,
  PipelineRunResponse,
  TaskEventListResponse,
} from './types';

export const taskApi = {
  createTask: async (data: CreateTaskRequest): Promise<CreateTaskResponse> => {
    return fetchApi<CreateTaskResponse>('/api/v1/research/tasks', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  getTask: async (taskId: string): Promise<ResearchTask> => {
    return fetchApi<ResearchTask>(`/api/v1/research/tasks/${taskId}`, {
      method: 'GET',
    });
  },

  getTaskEvents: async (taskId: string): Promise<TaskEventListResponse> => {
    return fetchApi<TaskEventListResponse>(`/api/v1/research/tasks/${taskId}/events`, {
      method: 'GET',
    });
  },

  runTask: async (taskId: string): Promise<PipelineRunResponse> => {
    return fetchApi<PipelineRunResponse>(`/api/v1/research/tasks/${taskId}/run`, {
      method: 'POST',
    });
  },
};
