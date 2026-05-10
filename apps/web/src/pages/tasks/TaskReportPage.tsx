import React, { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import { PageLayout } from '../../components/layout/PageLayout';
import { LoadingState } from '../../components/common/LoadingState';
import { ErrorState } from '../../components/common/ErrorState';
import { EmptyState } from '../../components/common/EmptyState';
import { SectionCard } from '../../components/common/SectionCard';
import { Button } from '../../components/common/Button';
import { Badge } from '../../components/common/Badge';
import { useReport } from '../../features/report/hooks';
import { formatChinaDateTime } from '../../lib/datetime';

export const TaskReportPage: React.FC = () => {
  const { taskId } = useParams<{ taskId: string }>();
  const { report, isLoading, error, refetch } = useReport(taskId);
  const [viewMode, setViewMode] = useState<'html' | 'raw'>('html');
  const [copyStatus, setCopyStatus] = useState<string | null>(null);

  if (isLoading) return <PageLayout title="研究报告"><LoadingState message="正在生成报告视图..." /></PageLayout>;

  if (error) {
    return (
      <PageLayout title="研究报告">
        <ErrorState error={error} onRetry={refetch} />
        <div style={{ marginTop: '1rem', textAlign: 'center' }}>
          <Link to={`/tasks/${taskId}`}>返回研究详情</Link>
        </div>
      </PageLayout>
    );
  }

  if (!report) {
    return (
      <PageLayout title="研究报告">
        <EmptyState message="该任务尚未生成研究报告。请确保研究已顺利完成。" />
        <div style={{ marginTop: '1rem', textAlign: 'center' }}>
          <Link to={`/tasks/${taskId}`}>返回研究详情</Link>
        </div>
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
    link.download = `开源情报收集与溯源系统-Report-${taskId || report.report_artifact_id}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  };

  return (
    <PageLayout
      maxWidth="1200px"
      actions={
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <Button variant="outline" size="sm" onClick={copyMarkdown}>
            {copyStatus || '复制 Markdown'}
          </Button>
          <Button size="sm" onClick={downloadMarkdown}>
            下载 .md
          </Button>
        </div>
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: '2rem', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
            <Button
              variant={viewMode === 'html' ? 'primary' : 'ghost'}
              size="sm"
              onClick={() => setViewMode('html')}
            >
              网页预览
            </Button>
            <Button
              variant={viewMode === 'raw' ? 'primary' : 'ghost'}
              size="sm"
              onClick={() => setViewMode('raw')}
            >
              源代码
            </Button>
          </div>

          <div
            className="card-solid"
            style={{
              padding: '3rem',
              minHeight: '600px',
              boxShadow: '0 10px 25px -5px rgba(0,0,0,0.05)',
            }}
          >
            {viewMode === 'html' ? (
              <div className="markdown-body">
                <ReactMarkdown>{report.markdown}</ReactMarkdown>
              </div>
            ) : (
              <pre style={{
                margin: 0,
                whiteSpace: 'pre-wrap',
                overflowX: 'auto',
                fontSize: '0.9rem',
                lineHeight: 1.6,
                fontFamily: 'monospace',
                color: 'var(--text-secondary)'
              }}>
                {report.markdown}
              </pre>
            )}
          </div>
        </div>

        <aside style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', position: 'sticky', top: '100px' }}>
          <SectionCard title="报告信息">
            <div style={{ display: 'grid', gap: '1rem', fontSize: '0.875rem' }}>
              <div>
                <div style={{ color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>标题</div>
                <div style={{ fontWeight: 600 }}>{report.title}</div>
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                <Badge variant="info">{report.report_language}</Badge>
                <Badge variant="info">{report.writer_mode}</Badge>
                <Badge variant="secondary">v{report.version}</Badge>
              </div>
              <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '1rem' }}>
                <div style={{ color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>生成于</div>
                <div style={{ fontWeight: 500 }}>{formatChinaDateTime(report.created_at)}</div>
              </div>
            </div>
          </SectionCard>

          <SectionCard title="快速导航">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <Link to={`/tasks/${taskId}`} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                <span>←</span> 返回研究详情
              </Link>
              <Link to={`/tasks/${taskId}/sources`} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                <span>📄</span> 查看所有来源
              </Link>
              <Link to={`/tasks/${taskId}/claims`} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                <span>⚖️</span> 查看结论声明
              </Link>
            </div>
          </SectionCard>
        </aside>
      </div>

      <style>{`
        .markdown-body {
          color: var(--text-primary);
          line-height: 1.8;
          font-size: 1.1rem;
        }
        .markdown-body h1 { font-size: 2.25rem; margin-bottom: 2rem; border-bottom: 2px solid var(--primary-container); padding-bottom: 0.5rem; }
        .markdown-body h2 { font-size: 1.75rem; margin-top: 2.5rem; margin-bottom: 1.25rem; }
        .markdown-body h3 { font-size: 1.25rem; margin-top: 2rem; }
        .markdown-body p { margin-bottom: 1.25rem; }
        .markdown-body ul, .markdown-body ol { margin-bottom: 1.25rem; padding-left: 1.5rem; }
        .markdown-body li { margin-bottom: 0.5rem; }
        .markdown-body blockquote {
          margin: 1.5rem 0;
          padding: 1rem 1.5rem;
          border-left: 4px solid var(--primary-color);
          backgroundColor: var(--bg-color);
          border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
        }
        .markdown-body code {
          background-color: #f1f3f4;
          padding: 0.2rem 0.4rem;
          border-radius: 4px;
          font-family: monospace;
          font-size: 0.9em;
        }
        .markdown-body pre {
          background-color: #f8f9fa;
          padding: 1.5rem;
          border-radius: var(--radius-md);
          overflow-x: auto;
          margin-bottom: 1.5rem;
        }
        .markdown-body pre code {
          background-color: transparent;
          padding: 0;
        }
        .markdown-body table {
          width: 100%;
          border-collapse: collapse;
          margin-bottom: 1.5rem;
        }
        .markdown-body th, .markdown-body td {
          border: 1px solid var(--border-color);
          padding: 0.75rem;
          text-align: left;
        }
        .markdown-body th {
          background-color: var(--bg-color);
        }
        @media (max-width: 900px) {
          main > div > div {
            grid-template-columns: 1fr !important;
          }
          aside {
            position: static !important;
          }
        }
      `}</style>
    </PageLayout>
  );
};
