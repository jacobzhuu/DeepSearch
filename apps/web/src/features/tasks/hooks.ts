import { useState, useEffect, useCallback } from 'react';
import { taskApi } from './api';
import { ResearchTask } from '../../types/api';
import {
  CreateTaskRequest,
  CreateTaskResponse,
  PipelineRunResponse,
  TaskEventListResponse,
} from './types';

export function useTask(taskId: string | undefined) {
  const [task, setTask] = useState<ResearchTask | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchTask = useCallback(async () => {
    if (!taskId) return;
    
    setIsLoading(true);
    setError(null);
    try {
      const data = await taskApi.getTask(taskId);
      setTask(data);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to fetch task'));
    } finally {
      setIsLoading(false);
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

  const fetchEvents = useCallback(async () => {
    if (!taskId) return;

    setIsLoading(true);
    setError(null);
    try {
      const data = await taskApi.getTaskEvents(taskId);
      setEventsData(data);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to fetch task events'));
    } finally {
      setIsLoading(false);
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
