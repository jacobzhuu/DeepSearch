import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { LoadingState } from '../../components/common/LoadingState';
import { PageLayout } from '../../components/layout/PageLayout';
import { StatusBadge } from '../../components/common/StatusBadge';
import { Button } from '../../components/common/Button';
import { ResearchTaskListItem } from '../../features/tasks/types';
import { useTasks } from '../../features/tasks/hooks';
import { formatChinaDateTime } from '../../lib/datetime';

export const TaskListPage: React.FC = () => {
  const { tasksData, isLoading, error, refetch } = useTasks();

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refetch(true);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refetch]);

  if (isLoading && !tasksData) {
    return (
      <PageLayout title="研究任务">
        <LoadingState />
      </PageLayout>
    );
  }

  const tasks = tasksData?.tasks || [];

  return (
    <PageLayout
      title="研究任务"
      actions={
        <Link to="/tasks/new">
          <Button size="sm">新建研究</Button>
        </Link>
      }
    >
      <ErrorState error={error} onRetry={() => void refetch()} />

      {tasks.length === 0 ? (
        <EmptyState message="目前没有任何研究任务。点击上方按钮开始您的第一次深度研究。" />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(100%, 1fr))', gap: '1rem' }}>
          <div style={{
            display: 'grid',
            gridTemplateColumns: '1fr 120px 180px 100px',
            padding: '0 1.5rem 0.75rem 1.5rem',
            color: 'var(--text-secondary)',
            fontSize: '0.75rem',
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.05em'
          }}>
            <div>研究问题</div>
            <div style={{ textAlign: 'center' }}>状态</div>
            <div style={{ textAlign: 'right' }}>更新时间</div>
            <div></div>
          </div>

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
    <div
      className="card-solid"
      style={{
        padding: '1.25rem 1.5rem',
        display: 'grid',
        gridTemplateColumns: '1fr 120px 180px 100px',
        alignItems: 'center',
        gap: '1rem',
        transition: 'transform 0.2s, box-shadow 0.2s',
        cursor: 'pointer'
      }}
      onClick={() => window.location.href = `/tasks/${task.task_id}`}
    >
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            fontWeight: 600,
            color: 'var(--text-primary)',
            fontSize: '1rem',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis'
          }}
        >
          {task.query}
        </div>
        <div style={{ marginTop: '0.25rem', color: 'var(--text-secondary)', fontSize: '0.75rem', fontFamily: 'monospace' }}>
          {task.task_id}
        </div>
      </div>

      <div style={{ textAlign: 'center' }}>
        <StatusBadge status={task.status} />
      </div>

      <div style={{ textAlign: 'right', color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
        {formatChinaDateTime(task.updated_at, {
          year: undefined,
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
          second: undefined,
        })}
      </div>

      <div style={{ textAlign: 'right' }}>
        <Link to={`/tasks/${task.task_id}`} onClick={(e) => e.stopPropagation()}>
          <Button variant="ghost" size="sm">打开</Button>
        </Link>
      </div>
    </div>
  );
};
