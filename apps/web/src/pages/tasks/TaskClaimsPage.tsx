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
                  状态: {formatClaimStatus(claim.verification_status)}
                </span>
                <span>置信度: {claim.confidence !== null ? (claim.confidence * 100).toFixed(1) + '%' : '无'}</span>
                <span>支持证据: {claim.support_evidence_count}</span>
                <span>弱证据: {claim.weak_support_evidence_count || 0}</span>
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
                        <strong style={{ color: getRelationColor(ev.relation_type) }}>
                          [{formatEvidenceRelation(ev.relation_type)}]
                        </strong>{' '}
                        {ev.relation_detail && <span>{ev.relation_detail} </span>}
                        {typeof ev.quality?.evidence_rank_score === 'number' && (
                          <span>score {ev.quality.evidence_rank_score.toFixed(2)} </span>
                        )}
                        {ev.citation_precision && <span>span {ev.citation_precision} </span>}
                        <span>{ev.excerpt}</span>
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
    case 'contradicted': return { ...baseStyle, backgroundColor: '#ffe6e6', color: '#990000' };
    case 'unsupported': return { ...baseStyle, backgroundColor: '#ffe6e6', color: '#cc0000' };
    default: return { ...baseStyle, backgroundColor: '#f0f0f0', color: '#666' };
  }
};

const formatClaimStatus = (status: string) => {
  switch (status) {
    case 'supported': return '已支持';
    case 'mixed': return '混合';
    case 'contradicted': return '被反驳';
    case 'unsupported': return '未支持';
    default: return status;
  }
};

const formatEvidenceRelation = (relation: string) => {
  switch (relation) {
    case 'support': return '支持';
    case 'weak_support': return '弱支持';
    case 'contradict': return '反驳';
    case 'candidate_support': return '候选';
    default: return relation;
  }
};

const getRelationColor = (relation: string) => {
  if (relation === 'support') return 'green';
  if (relation === 'weak_support') return '#997000';
  if (relation === 'contradict') return 'red';
  return '#666';
};
