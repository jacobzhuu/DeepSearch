import React, { useState } from 'react';
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
  const [viewMode, setViewMode] = useState<'html' | 'raw'>('html');
  const [copyStatus, setCopyStatus] = useState<string | null>(null);

  if (isLoading) return <PageLayout title="任务报告"><LoadingState message="正在加载报告..." /></PageLayout>;
  
  if (error) {
    return (
      <PageLayout title="任务报告">
        <ErrorState error={error} onRetry={refetch} />
        <Link to={`/tasks/${taskId}`}>返回任务</Link>
      </PageLayout>
    );
  }

  if (!report) {
    return (
      <PageLayout title="任务报告">
        <EmptyState message="此任务尚未生成任何报告。" />
        <Link to={`/tasks/${taskId}`}>返回任务</Link>
      </PageLayout>
    );
  }

  const copyMarkdown = async () => {
    await navigator.clipboard.writeText(report.markdown);
    setCopyStatus('已复制');
    window.setTimeout(() => setCopyStatus(null), 1800);
  };

  const downloadMarkdown = () => {
    const blob = new Blob([report.markdown], { type: 'text/markdown;charset=utf-8' });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `deepsearch-report-${taskId || report.report_artifact_id}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  };

  return (
    <PageLayout 
      title="任务报告"
      actions={
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            type="button"
            onClick={() => setViewMode('html')}
            style={viewMode === 'html' ? activeButtonStyle : secondaryButtonStyle}
          >
            网页视图 (HTML)
          </button>
          <button
            type="button"
            onClick={() => setViewMode('raw')}
            style={viewMode === 'raw' ? activeButtonStyle : secondaryButtonStyle}
          >
            原始 Markdown
          </button>
          <button type="button" onClick={copyMarkdown} style={secondaryButtonStyle}>
            {copyStatus || '复制 Markdown'}
          </button>
          <button type="button" onClick={downloadMarkdown} style={secondaryButtonStyle}>
            下载 .md
          </button>
          <Link to={`/tasks/${taskId}`}>返回任务</Link>
        </div>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <section style={{ backgroundColor: '#f0f7ff', padding: '1rem', borderRadius: '4px' }}>
          <strong>标题:</strong> {report.title} <br />
          <strong>格式:</strong> {report.format} <br />
          <strong>创建时间:</strong> {new Date(report.created_at).toLocaleString()}
        </section>

        <section style={{ backgroundColor: '#fff', border: '1px solid #eee', padding: '2rem', borderRadius: '8px' }}>
          {viewMode === 'html' ? (
            <ReactMarkdown>{report.markdown}</ReactMarkdown>
          ) : (
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', overflowX: 'auto', fontSize: '0.9rem', lineHeight: 1.5 }}>
              {report.markdown}
            </pre>
          )}
        </section>
      </div>
    </PageLayout>
  );
};

const activeButtonStyle = {
  padding: '0.45rem 0.75rem',
  border: '1px solid #0059b8',
  borderRadius: '4px',
  backgroundColor: '#0066cc',
  color: '#fff',
  cursor: 'pointer',
};

const secondaryButtonStyle = {
  padding: '0.45rem 0.75rem',
  border: '1px solid #cbd5e1',
  borderRadius: '4px',
  backgroundColor: '#fff',
  color: '#1f2937',
  cursor: 'pointer',
};
