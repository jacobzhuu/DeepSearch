import React from 'react';
import { useParams, Link } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { LoadingState } from '../../components/common/LoadingState';
import { ErrorState } from '../../components/common/ErrorState';
import { EmptyState } from '../../components/common/EmptyState';
import { useSources } from '../../features/sources/hooks';

export const TaskSourcesPage: React.FC = () => {
  const { taskId } = useParams<{ taskId: string }>();
  const { documentsData, chunksData, isLoading, error, refetch } = useSources(taskId);

  if (isLoading) return <PageLayout title="任务来源"><LoadingState message="正在加载文档和分块..." /></PageLayout>;
  
  if (error) return (
    <PageLayout title="任务来源">
      <ErrorState error={error} onRetry={refetch} />
      <Link to={`/tasks/${taskId}`}>返回任务</Link>
    </PageLayout>
  );

  const documents = documentsData?.source_documents || [];
  const allChunks = chunksData?.source_chunks || [];

  if (documents.length === 0) {
    return (
      <PageLayout title="任务来源">
        <EmptyState message="此任务尚未处理任何来源文档。" />
        <Link to={`/tasks/${taskId}`}>返回任务</Link>
      </PageLayout>
    );
  }

  return (
    <PageLayout 
      title="任务来源"
      actions={<Link to={`/tasks/${taskId}`}>返回任务</Link>}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
        {documents.map((doc) => {
          const docChunks = allChunks.filter((c) => c.source_document_id === doc.source_document_id);

          return (
            <div key={doc.source_document_id} style={{ border: '1px solid #e0e0e0', borderRadius: '8px', padding: '1.5rem', backgroundColor: '#fafafa' }}>
              <h3 style={{ marginTop: 0, color: '#0056b3', wordBreak: 'break-all' }}>
                <a href={doc.canonical_url} target="_blank" rel="norenoopener noreferrer" style={{ textDecoration: 'none', color: 'inherit' }}>
                  {doc.title || doc.canonical_url}
                </a>
              </h3>
              
              <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 1rem 0', fontSize: '0.9rem', color: '#555', display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                <li><strong>域名:</strong> {doc.domain}</li>
                <li><strong>类型:</strong> {doc.source_type}</li>
                <li><strong>获取时间:</strong> {new Date(doc.fetched_at).toLocaleString()}</li>
              </ul>

              <div>
                <h4 style={{ fontSize: '1rem', marginBottom: '0.75rem', borderBottom: '1px solid #ccc', paddingBottom: '0.25rem' }}>
                  提取的分块 ({docChunks.length})
                </h4>
                
                {docChunks.length > 0 ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                    {docChunks.map((chunk) => (
                      <div key={chunk.source_chunk_id} style={{ backgroundColor: '#fff', border: '1px solid #ddd', borderRadius: '4px', padding: '1rem' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', color: '#888', marginBottom: '0.5rem' }}>
                          <span><strong>分块编号:</strong> {chunk.chunk_no}</span>
                          <span><strong>词元数:</strong> {chunk.token_count}</span>
                          {chunk.metadata?.strategy && (
                            <span><strong>策略:</strong> {chunk.metadata.strategy}</span>
                          )}
                        </div>
                        <p style={{ margin: 0, fontSize: '0.95rem', lineHeight: '1.5', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                          {chunk.text}
                        </p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p style={{ color: '#888', fontSize: '0.9rem' }}>未能从该文档中提取分块。</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </PageLayout>
  );
};
