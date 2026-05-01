import { useState, useEffect, useCallback } from 'react';
import { taskApi } from './api';
import { ResearchTask } from '../../types/api';
import {
  CreateTaskRequest,
  CreateTaskResponse,
  PlanTaskRequest,
  PipelineRunResponse,
  ResearchPlanResponse,
  ResearchTaskListResponse,
  TaskEventListResponse,
  TaskMutationResponse,
} from './types';

export function useTasks() {
  const [tasksData, setTasksData] = useState<ResearchTaskListResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchTasks = useCallback(async (background = false) => {
    if (!background) setIsLoading(true);
    setError(null);
    try {
      const data = await taskApi.listTasks();
      setTasksData(data);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to fetch tasks'));
    } finally {
      if (!background) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  return { tasksData, isLoading, error, refetch: fetchTasks };
}

export function useTask(taskId: string | undefined) {
  const [task, setTask] = useState<ResearchTask | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchTask = useCallback(async (background = false) => {
    if (!taskId) return;
    
    if (!background) setIsLoading(true);
    setError(null);
    try {
      const data = await taskApi.getTask(taskId);
      setTask(data);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to fetch task'));
    } finally {
      if (!background) setIsLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    fetchTask();
  }, [fetchTask]);

  return { task, isLoading, error, refetch: fetchTask };
}

export function useTaskEvents(taskId: string | undefined) {
  const [eventsData, setEventsData] = useState<TaskEventListResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchEvents = useCallback(async (background = false) => {
    if (!taskId) return;

    if (!background) setIsLoading(true);
    setError(null);
    try {
      const data = await taskApi.getTaskEvents(taskId);
      setEventsData(data);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to fetch task events'));
    } finally {
      if (!background) setIsLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  return { eventsData, isLoading, error, refetch: fetchEvents };
}

export function useRunTask() {
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [result, setResult] = useState<PipelineRunResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const runTask = async (taskId: string): Promise<PipelineRunResponse | null> => {
    setIsRunning(true);
    setError(null);
    setResult(null);
    try {
      const data = await taskApi.runTask(taskId);
      setResult(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to run DeepSearch'));
      return null;
    } finally {
      setIsRunning(false);
    }
  };

  return { runTask, isRunning, result, error };
}

export function useTaskAction() {
  const [isMutating, setIsMutating] = useState<boolean>(false);
  const [error, setError] = useState<Error | null>(null);

  const mutateTask = async (
    taskId: string,
    action: 'pause' | 'resume' | 'cancel',
  ): Promise<TaskMutationResponse | null> => {
    setIsMutating(true);
    setError(null);
    try {
      const result = action === 'pause'
        ? await taskApi.pauseTask(taskId)
        : action === 'resume'
          ? await taskApi.resumeTask(taskId)
          : await taskApi.cancelTask(taskId);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err : new Error(`Failed to ${action} task`));
      return null;
    } finally {
      setIsMutating(false);
    }
  };

  return { mutateTask, isMutating, error };
}

export function usePlanTask() {
  const [isPlanning, setIsPlanning] = useState<boolean>(false);
  const [result, setResult] = useState<ResearchPlanResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const planTask = async (
    taskId: string,
    data: PlanTaskRequest = {},
  ): Promise<ResearchPlanResponse | null> => {
    setIsPlanning(true);
    setError(null);
    try {
      const result = await taskApi.planTask(taskId, data);
      setResult(result);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to generate research plan'));
      return null;
    } finally {
      setIsPlanning(false);
    }
  };

  return { planTask, isPlanning, result, error };
}

export function useCreateTask() {
  const [isCreating, setIsCreating] = useState<boolean>(false);
  const [error, setError] = useState<Error | null>(null);

  const createTask = async (data: CreateTaskRequest): Promise<CreateTaskResponse | null> => {
    setIsCreating(true);
    setError(null);
    try {
      const result = await taskApi.createTask(data);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to create task'));
      return null;
    } finally {
      setIsCreating(false);
    }
  };

  return { createTask, isCreating, error };
}
