import React from 'react';
import { useParams, Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import { PageLayout } from '../../components/layout/PageLayout';
import { LoadingState } from '../../components/common/LoadingState';
import { ErrorState } from '../../components/common/ErrorState';
import { EmptyState } from '../../components/common/EmptyState';
import { useReport } from '../../features/report/hooks';

export const TaskReportPage: React.FC = () => {
  const { taskId } = useParams<{ taskId: string }>();
  const { report, isLoading, error, refetch } = useReport(taskId);

  if (isLoading) return <PageLayout title="Task Report"><LoadingState message="Loading report..." /></PageLayout>;
  
  if (error) {
    return (
      <PageLayout title="Task Report">
        <ErrorState error={error} onRetry={refetch} />
        <Link to={`/tasks/${taskId}`}>Back to Task</Link>
      </PageLayout>
    );
  }

  if (!report) {
    return (
      <PageLayout title="Task Report">
        <EmptyState message="No report has been generated for this task yet." />
        <Link to={`/tasks/${taskId}`}>Back to Task</Link>
      </PageLayout>
    );
  }

  return (
    <PageLayout 
      title="Task Report"
      actions={<Link to={`/tasks/${taskId}`}>Back to Task</Link>}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <section style={{ backgroundColor: '#f0f7ff', padding: '1rem', borderRadius: '4px' }}>
          <strong>Title:</strong> {report.title} <br />
          <strong>Format:</strong> {report.format} <br />
          <strong>Created At:</strong> {new Date(report.created_at).toLocaleString()}
        </section>

        <section style={{ backgroundColor: '#fff', border: '1px solid #eee', padding: '2rem', borderRadius: '8px' }}>
          <ReactMarkdown>{report.markdown}</ReactMarkdown>
        </section>
      </div>
    </PageLayout>
  );
};
