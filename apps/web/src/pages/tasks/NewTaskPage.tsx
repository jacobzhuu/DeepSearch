import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { ErrorState } from '../../components/common/ErrorState';
import { PipelineRunResponse } from '../../features/tasks/types';
import { useCreateTask, useRunTask } from '../../features/tasks/hooks';

export const NewTaskPage: React.FC = () => {
  const navigate = useNavigate();
  const { createTask, isCreating, error } = useCreateTask();
  const { runTask, isRunning, result: runResult, error: runError } = useRunTask();
  
  const [query, setQuery] = useState('');
  // For simplicity in this version, we only handle the required query field.
  // Constraints could be added as more complex form fields later.

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    const result = await createTask({ query });
    if (result) {
      const pipelineResult = await runTask(result.task_id);
      if (pipelineResult?.completed) {
        navigate(`/tasks/${result.task_id}/report`);
        return;
      }
      navigate(`/tasks/${result.task_id}`, {
        state: { pipelineResult },
      });
    }
  };

  const busy = isCreating || isRunning;

  return (
    <PageLayout title="Create New Task">
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem', maxWidth: '500px' }}>
        <ErrorState error={error} />
        <ErrorState error={runError} />
        <PipelineRunSummary result={runResult} />
        
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <label htmlFor="query" style={{ fontWeight: 'bold' }}>Research Query</label>
          <textarea
            id="query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g., 近30天 NVIDIA 在开源模型生态上的关键发布与影响"
            rows={4}
            style={{ padding: '0.5rem', fontFamily: 'inherit' }}
            required
            disabled={busy}
          />
        </div>

        <button 
          type="submit" 
          disabled={busy || !query.trim()}
          style={{ padding: '0.75rem', cursor: busy ? 'not-allowed' : 'pointer', fontWeight: 'bold' }}
        >
          {isCreating ? 'Creating...' : isRunning ? 'Running DeepSearch...' : 'Create And Run DeepSearch'}
        </button>
      </form>
    </PageLayout>
  );
};

const PipelineRunSummary: React.FC<{ result: PipelineRunResponse | null }> = ({ result }) => {
  if (!result) return null;

  return (
    <section style={{ border: '1px solid #ddd', borderRadius: '4px', padding: '1rem', backgroundColor: '#fafafa' }}>
      <strong>Pipeline:</strong> {result.running_mode} <br />
      <strong>Status:</strong> {result.status} <br />
      <strong>Stages:</strong> {result.stages_completed.join(' -> ') || 'none'} <br />
      {result.failure && (
        <>
          <strong>Failed Stage:</strong> {result.failure.failed_stage} <br />
          <strong>Reason:</strong> {result.failure.reason} <br />
          <strong>Next Action:</strong> {result.failure.next_action}
        </>
      )}
    </section>
  );
};
