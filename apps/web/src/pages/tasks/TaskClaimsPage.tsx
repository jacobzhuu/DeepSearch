import React from 'react';
import { useParams, Link } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { LoadingState } from '../../components/common/LoadingState';
import { ErrorState } from '../../components/common/ErrorState';
import { EmptyState } from '../../components/common/EmptyState';
import { useClaims } from '../../features/claims/hooks';

export const TaskClaimsPage: React.FC = () => {
  const { taskId } = useParams<{ taskId: string }>();
  const { claimsData, evidenceData, isLoading, error, refetch } = useClaims(taskId);

  if (isLoading) return <PageLayout title="任务结论与声明"><LoadingState message="正在加载结论和证据..." /></PageLayout>;
  
  if (error) return (
    <PageLayout title="任务结论与声明">
      <ErrorState error={error} onRetry={refetch} />
      <Link to={`/tasks/${taskId}`}>返回任务</Link>
    </PageLayout>
  );

  const claims = claimsData?.claims || [];
  const evidenceList = evidenceData?.claim_evidence || [];

  if (claims.length === 0) {
    return (
      <PageLayout title="任务结论与声明">
        <EmptyState message="此任务尚未生成任何结论声明。" />
        <Link to={`/tasks/${taskId}`}>返回任务</Link>
      </PageLayout>
    );
  }

  return (
    <PageLayout 
      title="任务结论与声明"
      actions={<Link to={`/tasks/${taskId}`}>返回任务</Link>}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
        {claims.map((claim) => {
          const claimEvidence = evidenceList.filter((e) => e.claim_id === claim.claim_id);

          return (
            <div key={claim.claim_id} style={{ border: '1px solid #e0e0e0', borderRadius: '8px', padding: '1.5rem' }}>
              <h3 style={{ marginTop: 0, color: '#333' }}>{claim.statement}</h3>
              
              <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem', fontSize: '0.9rem' }}>
                <span style={getStatusStyle(claim.verification_status)}>
                  状态: {claim.verification_status === 'supported' ? '已支持' : claim.verification_status === 'mixed' ? '混合' : claim.verification_status === 'unsupported' ? '未支持' : claim.verification_status}
                </span>
                <span>置信度: {claim.confidence !== null ? (claim.confidence * 100).toFixed(1) + '%' : '无'}</span>
                <span>支持证据: {claim.support_evidence_count}</span>
                <span>反驳证据: {claim.contradict_evidence_count}</span>
              </div>

              {claim.rationale && (
                <div style={{ backgroundColor: '#f9f9f9', padding: '0.75rem', borderRadius: '4px', marginBottom: '1rem' }}>
                  <strong>基本原理:</strong> {claim.rationale}
                </div>
              )}

              {claimEvidence.length > 0 ? (
                <div>
                  <h4 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>证据链接:</h4>
                  <ul style={{ margin: 0, paddingLeft: '1.5rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                    {claimEvidence.map((ev) => (
                      <li key={ev.claim_evidence_id} style={{ fontSize: '0.9rem' }}>
                        <strong style={{ color: ev.relation_type === 'support' ? 'green' : 'red' }}>
                          [{ev.relation_type === 'support' ? '支持' : '反驳'}]
                        </strong>{' '}
                        {ev.excerpt}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p style={{ color: '#888', fontSize: '0.9rem' }}>此声明没有绑定的证据。</p>
              )}
            </div>
          );
        })}
      </div>
    </PageLayout>
  );
};

const getStatusStyle = (status: string) => {
  const baseStyle = { fontWeight: 'bold', padding: '0.1rem 0.4rem', borderRadius: '4px' };
  switch (status) {
    case 'supported': return { ...baseStyle, backgroundColor: '#e6ffe6', color: '#006600' };
    case 'mixed': return { ...baseStyle, backgroundColor: '#fff0e6', color: '#b35900' };
    case 'unsupported': return { ...baseStyle, backgroundColor: '#ffe6e6', color: '#cc0000' };
    default: return { ...baseStyle, backgroundColor: '#f0f0f0', color: '#666' };
  }
};
