import { TaskConstraints } from '../../types/api';

export interface CreateTaskRequest {
  query: string;
  constraints?: TaskConstraints;
}

export interface CreateTaskResponse {
  task_id: string;
  status: string;
  revision_no: number;
  updated_at: string;
}

export type {
  PipelineCounts,
  PipelineFailure,
  PipelineRunResponse,
  ResearchTask,
  TaskEvent,
  TaskEventListResponse,
} from '../../types/api';
