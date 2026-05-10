import React from 'react';
import { useParams, Link } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { LoadingState } from '../../components/common/LoadingState';
import { ErrorState } from '../../components/common/ErrorState';
import { EmptyState } from '../../components/common/EmptyState';
import { SectionCard } from '../../components/common/SectionCard';
import { Badge } from '../../components/common/Badge';
import { useSources } from '../../features/sources/hooks';
import { formatChinaDateTime } from '../../lib/datetime';

export const TaskSourcesPage: React.FC = () => {
  const { taskId } = useParams<{ taskId: string }>();
  const { documentsData, chunksData, isLoading, error, refetch } = useSources(taskId);

  if (isLoading) return <PageLayout title="研究来源"><LoadingState message="正在加载文档和分块..." /></PageLayout>;

  if (error) return (
    <PageLayout title="研究来源">
      <ErrorState error={error} onRetry={refetch} />
      <div style={{ marginTop: '1rem', textAlign: 'center' }}>
        <Link to={`/tasks/${taskId}`}>返回研究详情</Link>
      </div>
    </PageLayout>
  );

  const documents = documentsData?.source_documents || [];
  const allChunks = chunksData?.source_chunks || [];

  if (documents.length === 0) {
    return (
      <PageLayout title="研究来源">
        <EmptyState message="此任务尚未处理任何来源文档。" />
        <div style={{ marginTop: '1rem', textAlign: 'center' }}>
          <Link to={`/tasks/${taskId}`}>返回研究详情</Link>
        </div>
      </PageLayout>
    );
  }

  return (
    <PageLayout
      title="研究来源"
      actions={<Link to={`/tasks/${taskId}`}>返回研究详情</Link>}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
        {documents.map((doc) => {
          const docChunks = allChunks.filter((c) => c.source_document_id === doc.source_document_id);

          return (
            <SectionCard key={doc.source_document_id}>
              <div style={{ marginBottom: '1.5rem' }}>
                <h3 style={{ fontSize: '1.25rem', marginBottom: '0.75rem', wordBreak: 'break-all' }}>
                  <a href={doc.canonical_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--primary-color)' }}>
                    {doc.title || doc.canonical_url}
                  </a>
                </h3>

                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
                  <Badge variant="info">{doc.domain}</Badge>
                  <Badge variant="secondary">{doc.source_type}</Badge>
                  <Badge variant="secondary">{String(doc.parser_metadata?.parser_kind || doc.source_type)}</Badge>
                  <Badge variant={doc.final_source_score && doc.final_source_score > 0.7 ? 'success' : 'default'}>
                    质量: {formatScore(doc.final_source_score)}
                  </Badge>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                  <div>
                    <strong>解析状态:</strong> {String(doc.parser_metadata?.parser_status || 'unknown')}
                  </div>
                  <div>
                    <strong>获取时间:</strong> {formatChinaDateTime(doc.fetched_at)}
                  </div>
                </div>

                {doc.quality?.reasons && Array.isArray(doc.quality.reasons) && (
                  <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginTop: '0.75rem', padding: '0.75rem', backgroundColor: 'var(--bg-color)', borderRadius: 'var(--radius-sm)' }}>
                    <strong>评估理由:</strong> {doc.quality.reasons.join(', ')}
                  </div>
                )}
              </div>

              <div>
                <div style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  提取的分块 <Badge variant="secondary">{docChunks.length}</Badge>
                </div>

                {docChunks.length > 0 ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                    {docChunks.map((chunk) => (
                      <div key={chunk.source_chunk_id} className="card-solid" style={{ padding: '1.25rem' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.75rem', textTransform: 'uppercase', fontWeight: 600, letterSpacing: '0.05em' }}>
                          <div style={{ display: 'flex', gap: '1rem' }}>
                            <span># {chunk.chunk_no}</span>
                            <span>Tokens: {chunk.token_count}</span>
                            {typeof chunk.metadata?.content_quality_score === 'number' && (
                              <span>质量: {formatScore(chunk.metadata.content_quality_score)}</span>
                            )}
                          </div>
                          <div>
                            {formatChunkLocator(chunk.metadata)}
                          </div>
                        </div>
                        <p style={{ margin: 0, fontSize: '0.95rem', lineHeight: '1.6', whiteSpace: 'pre-wrap', color: 'var(--text-primary)' }}>
                          {chunk.text}
                        </p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState message="未能从该文档中提取有效分块。" />
                )}
              </div>
            </SectionCard>
          );
        })}
      </div>
    </PageLayout>
  );
};

const formatScore = (value: number | null | undefined) => (
  typeof value === 'number' ? value.toFixed(2) : '-'
);

const formatChunkLocator = (metadata: Record<string, any>) => {
  if (metadata.page_range) return `Page ${metadata.page_range.join('-')}`;
  if (metadata.slide_range) return `Slide ${metadata.slide_range.join('-')}`;
  if (Array.isArray(metadata.sheet_names) && metadata.sheet_names.length > 0) {
    return `Sheet ${metadata.sheet_names.join(', ')}`;
  }
  return 'Content Chunk';
};
