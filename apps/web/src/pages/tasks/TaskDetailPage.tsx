import React from 'react';
import { useParams, Link, useLocation, useNavigate } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { LoadingState } from '../../components/common/LoadingState';
import { ErrorState } from '../../components/common/ErrorState';
import { PipelineCounts, PipelineRunResponse, ResearchTask, TaskEvent } from '../../features/tasks/types';
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
    if (!taskId || task?.status !== 'PLANNED') return;
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

  const canRun = task.status === 'PLANNED';
  const runDisabledReason = canRun
    ? null
    : 'Only PLANNED tasks can be run. Create a new task or revise this task before running.';

  return (
    <PageLayout 
      title="Task Detail"
      actions={
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', alignItems: 'flex-end' }}>
          <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
            <span style={{ padding: '0.25rem 0.75rem', backgroundColor: '#eee', borderRadius: '1rem', fontSize: '0.875rem', fontWeight: 'bold' }}>
              {task.status}
            </span>
            <button
              onClick={handleRun}
              disabled={isRunning || !canRun}
              aria-describedby={runDisabledReason ? 'run-disabled-reason' : undefined}
              style={{ ...buttonStyle, border: 0, opacity: canRun ? 1 : 0.65, cursor: isRunning || !canRun ? 'not-allowed' : 'pointer' }}
            >
              {isRunning ? 'Running...' : 'Run DeepSearch'}
            </button>
          </div>
          {runDisabledReason && (
            <div id="run-disabled-reason" style={{ maxWidth: '22rem', fontSize: '0.8rem', color: '#7a4b00', textAlign: 'right' }}>
              {runDisabledReason}
            </div>
          )}
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

        <TaskObservabilityPanel observability={task.progress?.observability || null} />

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

type TaskObservability = NonNullable<NonNullable<ResearchTask['progress']>['observability']>;

const TaskObservabilityPanel: React.FC<{ observability: TaskObservability | null }> = ({ observability }) => {
  if (!observability) return null;

  const selectedSources = Array.isArray(observability.selected_sources) ? observability.selected_sources : [];
  const attemptedSources = Array.isArray(observability.attempted_sources) ? observability.attempted_sources : [];
  const unattemptedSources = Array.isArray(observability.unattempted_sources) ? observability.unattempted_sources : [];
  const failedSources = Array.isArray(observability.failed_sources) ? observability.failed_sources : [];
  const parseDecisions = Array.isArray(observability.parse_decisions) ? observability.parse_decisions : [];
  const warnings = Array.isArray(observability.warnings) ? observability.warnings : [];

  return (
    <section style={{ border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1rem', backgroundColor: '#fbfdff' }}>
      <h3 style={{ marginTop: 0 }}>Run Observability</h3>
      <ul style={{ listStyle: 'none', padding: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '0.5rem' }}>
        <li><strong>Search Results:</strong><br />{observability.search_result_count ?? 'n/a'}</li>
        <li><strong>Fetch Succeeded:</strong><br />{observability.fetch_succeeded ?? 'n/a'}</li>
        <li><strong>Fetch Failed:</strong><br />{observability.fetch_failed ?? 'n/a'}</li>
      </ul>
      {warnings.length > 0 && (
        <div style={{ marginTop: '0.75rem', color: '#7a4b00' }}>
          {warnings.map((warning: string) => (
            <div key={warning}><strong>Warning:</strong> {warning}</div>
          ))}
        </div>
      )}
      {selectedSources.length > 0 && (
        <div style={{ marginTop: '0.75rem' }}>
          <strong>Selected Sources</strong>
          <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
            {selectedSources.slice(0, 5).map((source: any) => (
              <li key={source.candidate_url_id || source.canonical_url}>
                <span>{source.domain || 'unknown'}</span>{' '}
                <a href={source.canonical_url} target="_blank" rel="noreferrer">{source.canonical_url}</a>
                {' '}<span style={{ color: '#64748b' }}>
                  {source.fetch_status ? `(${source.fetch_status})` : source.fetch_attempted === false ? '(UNATTEMPTED)' : ''}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {attemptedSources.length > 0 && (
        <div style={{ marginTop: '0.75rem' }}>
          <strong>Attempted Fetches</strong>
          <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
            {attemptedSources.slice(0, 5).map((source: any) => (
              <li key={source.fetch_attempt_id || source.canonical_url}>
                <a href={source.canonical_url} target="_blank" rel="noreferrer">{source.canonical_url}</a>
                {' '}status {source.fetch_status || 'unknown'}
                {source.error_code ? ` / ${source.error_code}` : ''}
                {source.error_reason ? ` / ${source.error_reason}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      {failedSources.length > 0 && (
        <div style={{ marginTop: '0.75rem' }}>
          <strong>Failed Fetches</strong>
          <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
            {failedSources.slice(0, 5).map((source: any) => (
              <li key={source.fetch_attempt_id || source.canonical_url}>
                <a href={source.canonical_url} target="_blank" rel="noreferrer">{source.canonical_url}</a>
                {' '}status {source.http_status ?? 'n/a'} / {source.error_code || source.error_reason || 'unknown'}
                {source.trace?.exception_type ? ` / ${source.trace.exception_type}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      {unattemptedSources.length > 0 && (
        <div style={{ marginTop: '0.75rem' }}>
          <strong>Unattempted Sources</strong>
          <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
            {unattemptedSources.slice(0, 5).map((source: any) => (
              <li key={source.candidate_url_id || source.canonical_url}>
                <a href={source.canonical_url} target="_blank" rel="noreferrer">{source.canonical_url}</a>
                {' '}rank {source.rank ?? 'n/a'}
              </li>
            ))}
          </ul>
        </div>
      )}
      {parseDecisions.length > 0 && (
        <div style={{ marginTop: '0.75rem' }}>
          <strong>Parse Decisions</strong>
          <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
            {parseDecisions.slice(0, 5).map((decision: any) => (
              <li key={decision.snapshot_id || decision.content_snapshot_id}>
                <span style={{ fontFamily: 'monospace' }}>{decision.decision || 'unknown'}</span>
                {' '}for <a href={decision.canonical_url} target="_blank" rel="noreferrer">{decision.canonical_url || 'unknown URL'}</a>
                {' '}({decision.mime_type || 'unknown mime'}, body {decision.body_length ?? 'n/a'} bytes)
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
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
