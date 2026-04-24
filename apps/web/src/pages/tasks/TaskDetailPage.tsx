import React from 'react';
import { useParams, Link, useLocation, useNavigate } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { LoadingState } from '../../components/common/LoadingState';
import { ErrorState } from '../../components/common/ErrorState';
import { PipelineCounts, PipelineRunResponse, TaskEvent } from '../../features/tasks/types';
import { useRunTask, useTask, useTaskEvents } from '../../features/tasks/hooks';

export const TaskDetailPage: React.FC = () => {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { task, isLoading, error, refetch } = useTask(taskId);
  const { eventsData, refetch: refetchEvents } = useTaskEvents(taskId);
  const { runTask, isRunning, result: runResult, error: runError } = useRunTask();
  const initialPipelineResult = (location.state as { pipelineResult?: PipelineRunResponse } | null)?.pipelineResult || null;
  const pipelineResult = runResult || initialPipelineResult;

  const handleRun = async () => {
    if (!taskId) return;
    const result = await runTask(taskId);
    await refetch();
    await refetchEvents();
    if (result?.completed) {
      navigate(`/tasks/${taskId}/report`);
    }
  };

  if (isLoading) return <PageLayout title="Task Detail"><LoadingState /></PageLayout>;
  
  if (error) return (
    <PageLayout title="Task Detail">
      <ErrorState error={error} onRetry={refetch} />
      <Link to="/tasks/new">Back to Home</Link>
    </PageLayout>
  );

  if (!task) return <PageLayout title="Task Detail"><p>Task not found.</p></PageLayout>;

  return (
    <PageLayout 
      title="Task Detail"
      actions={
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <span style={{ padding: '0.25rem 0.75rem', backgroundColor: '#eee', borderRadius: '1rem', fontSize: '0.875rem', fontWeight: 'bold' }}>
            {task.status}
          </span>
          <button
            onClick={handleRun}
            disabled={isRunning || task.status !== 'PLANNED'}
            style={{ ...buttonStyle, border: 0, cursor: isRunning || task.status !== 'PLANNED' ? 'not-allowed' : 'pointer' }}
          >
            {isRunning ? 'Running...' : 'Run DeepSearch'}
          </button>
        </div>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
        <ErrorState error={runError} />
        <PipelineResultPanel result={pipelineResult} />
        
        <section style={{ backgroundColor: '#f9f9f9', padding: '1.5rem', borderRadius: '8px' }}>
          <h2 style={{ marginTop: 0, fontSize: '1.25rem' }}>Query</h2>
          <p style={{ margin: 0, fontSize: '1.1rem' }}>{task.query}</p>
        </section>

        <section>
          <h3 style={{ borderBottom: '1px solid #eee', paddingBottom: '0.5rem' }}>Metadata</h3>
          <ul style={{ listStyle: 'none', padding: 0, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <li><strong>Task ID:</strong> <br/><span style={{ fontFamily: 'monospace', fontSize: '0.9em' }}>{task.task_id}</span></li>
            <li><strong>Revision No:</strong> <br/>{task.revision_no}</li>
            <li><strong>Progress:</strong> <br/>{task.progress?.current_state || task.status}</li>
            <li><strong>Events:</strong> <br/>{task.progress?.events_total ?? 0}</li>
            <li><strong>Created At:</strong> <br/>{new Date(task.created_at).toLocaleString()}</li>
            <li><strong>Updated At:</strong> <br/>{new Date(task.updated_at).toLocaleString()}</li>
          </ul>
        </section>

        <section>
          <h3 style={{ borderBottom: '1px solid #eee', paddingBottom: '0.5rem' }}>Exploration</h3>
          <div style={{ display: 'flex', gap: '1rem' }}>
            <Link to={`/tasks/${taskId}/sources`} style={buttonStyle}>Sources</Link>
            <Link to={`/tasks/${taskId}/claims`} style={buttonStyle}>Claims</Link>
            <Link to={`/tasks/${taskId}/report`} style={buttonStyle}>Report</Link>
          </div>
        </section>

        <section>
          <h3 style={{ borderBottom: '1px solid #eee', paddingBottom: '0.5rem' }}>Events</h3>
          <TaskEventList events={eventsData?.events || []} />
        </section>

      </div>
    </PageLayout>
  );
};

const PipelineResultPanel: React.FC<{ result: PipelineRunResponse | null }> = ({ result }) => {
  if (!result) return null;

  return (
    <section style={{ border: '1px solid #ddd', borderRadius: '8px', padding: '1rem', backgroundColor: result.completed ? '#f1fff4' : '#fff8f1' }}>
      <h3 style={{ marginTop: 0 }}>Pipeline Result</h3>
      <p style={{ marginTop: 0 }}>
        <strong>Mode:</strong> {result.running_mode}
      </p>
      <CountsGrid counts={result.counts} />
      {result.failure && (
        <div style={{ marginTop: '1rem', borderTop: '1px solid #e0d0c0', paddingTop: '1rem' }}>
          <div><strong>Failed Stage:</strong> {result.failure.failed_stage}</div>
          <div><strong>Reason:</strong> {result.failure.reason}</div>
          <div><strong>Message:</strong> {result.failure.message}</div>
          <div><strong>Next Action:</strong> {result.failure.next_action}</div>
        </div>
      )}
    </section>
  );
};

const CountsGrid: React.FC<{ counts: PipelineCounts }> = ({ counts }) => (
  <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0.5rem' }}>
    {Object.entries(counts).map(([key, value]) => (
      <li key={key} style={{ backgroundColor: '#fff', border: '1px solid #eee', borderRadius: '4px', padding: '0.5rem' }}>
        <strong>{key}:</strong> {value}
      </li>
    ))}
  </ul>
);

const TaskEventList: React.FC<{ events: TaskEvent[] }> = ({ events }) => {
  if (events.length === 0) {
    return <p style={{ color: '#777' }}>No events recorded.</p>;
  }

  return (
    <ol style={{ margin: 0, paddingLeft: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {events.slice().reverse().slice(0, 12).map((event) => (
        <li key={event.event_id}>
          <div><strong>{event.event_type}</strong> #{event.sequence_no}</div>
          <div style={{ color: '#666', fontSize: '0.875rem' }}>
            {new Date(event.created_at).toLocaleString()}
            {event.payload?.stage ? ` | ${event.payload.stage}` : ''}
            {event.payload?.reason ? ` | ${event.payload.reason}` : ''}
          </div>
        </li>
      ))}
    </ol>
  );
};

const buttonStyle = {
  display: 'inline-block',
  padding: '0.5rem 1rem',
  backgroundColor: '#0066cc',
  color: 'white',
  textDecoration: 'none',
  borderRadius: '4px',
  fontWeight: 'bold',
};
