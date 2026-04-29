import { TaskConstraints } from '../../types/api';

export interface CreateTaskRequest {
  query: string;
  constraints?: TaskConstraints;
  report_language?: string;
}

export interface CreateTaskResponse {
  task_id: string;
  status: string;
  revision_no: number;
  updated_at: string;
}

export interface PlanTaskRequest {
  research_plan?: Record<string, any>;
}

export type {
  PipelineCounts,
  PipelineFailure,
  PipelineRunResponse,
  ResearchPlanResponse,
  ResearchTask,
  TaskEvent,
  TaskEventListResponse,
} from '../../types/api';
