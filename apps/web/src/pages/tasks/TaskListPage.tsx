import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { LoadingState } from '../../components/common/LoadingState';
import { PageLayout } from '../../components/layout/PageLayout';
import { ResearchTaskListItem } from '../../features/tasks/types';
import { useTasks } from '../../features/tasks/hooks';

export const TaskListPage: React.FC = () => {
  const { tasksData, isLoading, error, refetch } = useTasks();

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refetch(true);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refetch]);

  if (isLoading) {
    return (
      <PageLayout title="任务列表">
        <LoadingState />
      </PageLayout>
    );
  }

  const tasks = tasksData?.tasks || [];

  return (
    <PageLayout
      title="任务列表"
      actions={<Link to="/tasks/new" style={buttonStyle}>新建任务</Link>}
    >
      <ErrorState error={error} onRetry={() => void refetch()} />
      {tasks.length === 0 ? (
        <EmptyState message="还没有 research_task。" />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {tasks.map((task) => (
            <TaskListRow key={task.task_id} task={task} />
          ))}
        </div>
      )}
    </PageLayout>
  );
};

const TaskListRow: React.FC<{ task: ResearchTaskListItem }> = ({ task }) => {
  return (
    <section style={{ border: '1px solid #e5e5e5', borderRadius: '8px', padding: '1rem', backgroundColor: '#fff' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'flex-start' }}>
        <div style={{ minWidth: 0 }}>
          <Link
            to={`/tasks/${task.task_id}`}
            style={{
              fontWeight: 700,
              color: '#111',
              textDecoration: 'none',
              overflowWrap: 'anywhere',
            }}
          >
            {task.query}
          </Link>
          <div style={{ marginTop: '0.5rem', color: '#666', fontSize: '0.875rem' }}>
            更新 {new Date(task.updated_at).toLocaleString()} · 事件 {task.events_total}
          </div>
          <div style={{ marginTop: '0.25rem', color: '#888', fontFamily: 'monospace', fontSize: '0.8rem' }}>
            {task.task_id}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.5rem', flexShrink: 0 }}>
          <span style={statusStyle}>{task.status}</span>
          <Link to={`/tasks/${task.task_id}`} style={smallButtonStyle}>打开</Link>
        </div>
      </div>
    </section>
  );
};

const buttonStyle: React.CSSProperties = {
  padding: '0.5rem 1rem',
  backgroundColor: '#111',
  color: 'white',
  textDecoration: 'none',
  borderRadius: '6px',
  fontWeight: 700,
};

const smallButtonStyle: React.CSSProperties = {
  padding: '0.35rem 0.75rem',
  border: '1px solid #ddd',
  color: '#111',
  textDecoration: 'none',
  borderRadius: '6px',
  fontSize: '0.875rem',
};

const statusStyle: React.CSSProperties = {
  padding: '0.25rem 0.65rem',
  backgroundColor: '#f1f1f1',
  borderRadius: '999px',
  fontSize: '0.75rem',
  fontWeight: 700,
};
