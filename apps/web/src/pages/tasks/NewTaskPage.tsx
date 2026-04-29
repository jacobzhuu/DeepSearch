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
    <PageLayout title="创建新任务">
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem', maxWidth: '500px' }}>
        <ErrorState error={error} />
        <ErrorState error={runError} />
        <PipelineRunSummary result={runResult} />
        
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <label htmlFor="query" style={{ fontWeight: 'bold' }}>研究问题</label>
          <textarea
            id="query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="例如：近30天 NVIDIA 在开源模型生态上的关键发布与影响"
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
          {isCreating ? '创建中...' : isRunning ? '运行 DeepSearch 中...' : '创建并运行 DeepSearch'}
        </button>
      </form>
    </PageLayout>
  );
};

const PipelineRunSummary: React.FC<{ result: PipelineRunResponse | null }> = ({ result }) => {
  if (!result) return null;

  return (
    <section style={{ border: '1px solid #ddd', borderRadius: '4px', padding: '1rem', backgroundColor: '#fafafa' }}>
      <strong>流程模式:</strong> {result.running_mode} <br />
      <strong>状态:</strong> {result.status} <br />
      <strong>已完成阶段:</strong> {result.stages_completed.join(' -> ') || '无'} <br />
      {result.failure && (
        <>
          <strong>失败阶段:</strong> {result.failure.failed_stage} <br />
          <strong>原因:</strong> {result.failure.reason} <br />
          <strong>下一步建议:</strong> {result.failure.next_action}
        </>
      )}
    </section>
  );
};
