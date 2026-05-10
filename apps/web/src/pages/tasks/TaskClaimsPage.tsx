import React from 'react';
import { useParams, Link } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { LoadingState } from '../../components/common/LoadingState';
import { ErrorState } from '../../components/common/ErrorState';
import { EmptyState } from '../../components/common/EmptyState';
import { SectionCard } from '../../components/common/SectionCard';
import { Badge } from '../../components/common/Badge';
import { useClaims } from '../../features/claims/hooks';

export const TaskClaimsPage: React.FC = () => {
  const { taskId } = useParams<{ taskId: string }>();
  const { claimsData, evidenceData, isLoading, error, refetch } = useClaims(taskId);

  if (isLoading) return <PageLayout title="结论与声明"><LoadingState message="正在加载结论和证据..." /></PageLayout>;
  
  if (error) return (
    <PageLayout title="结论与声明">
      <ErrorState error={error} onRetry={refetch} />
      <div style={{ marginTop: '1rem', textAlign: 'center' }}>
        <Link to={`/tasks/${taskId}`}>返回研究详情</Link>
      </div>
    </PageLayout>
  );

  const claims = claimsData?.claims || [];
  const evidenceList = evidenceData?.claim_evidence || [];

  if (claims.length === 0) {
    return (
      <PageLayout title="结论与声明">
        <EmptyState message="此任务尚未生成任何结论声明。" />
        <div style={{ marginTop: '1rem', textAlign: 'center' }}>
          <Link to={`/tasks/${taskId}`}>返回研究详情</Link>
        </div>
      </PageLayout>
    );
  }

  return (
    <PageLayout 
      title="结论与声明"
      actions={<Link to={`/tasks/${taskId}`}>返回研究详情</Link>}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
        {claims.map((claim) => {
          const claimEvidence = evidenceList.filter((e) => e.claim_id === claim.claim_id);

          return (
            <SectionCard key={claim.claim_id}>
              <div style={{ marginBottom: '1rem' }}>
                <h3 style={{ fontSize: '1.25rem', marginBottom: '1rem', lineHeight: 1.4 }}>{claim.statement}</h3>
                
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
                  <Badge variant={getClaimStatusVariant(claim.verification_status)}>
                    {formatClaimStatus(claim.verification_status)}
                  </Badge>
                  {claim.confidence !== null && (
                    <Badge variant="info">置信度: {(claim.confidence * 100).toFixed(0)}%</Badge>
                  )}
                  <Badge variant="secondary">支持: {claim.support_evidence_count}</Badge>
                  {claim.contradict_evidence_count > 0 && (
                    <Badge variant="error">反驳: {claim.contradict_evidence_count}</Badge>
                  )}
                </div>

                {claim.rationale && (
                  <div style={{ backgroundColor: 'var(--bg-color)', padding: '1rem', borderRadius: 'var(--radius-sm)', marginBottom: '1.5rem', fontSize: '0.9375rem', borderLeft: '4px solid var(--border-color)' }}>
                    <div style={{ fontWeight: 600, fontSize: '0.75rem', textTransform: 'uppercase', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>推理过程</div>
                    {claim.rationale}
                  </div>
                )}

                <div>
                  <div style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.75rem', color: 'var(--text-secondary)' }}>证据来源:</div>
                  {claimEvidence.length > 0 ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                      {claimEvidence.map((ev) => (
                        <div key={ev.claim_evidence_id} className="card-solid" style={{ padding: '1rem', fontSize: '0.875rem' }}>
                          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
                            <Badge variant={getRelationVariant(ev.relation_type)}>
                              {formatEvidenceRelation(ev.relation_type)}
                            </Badge>
                            {ev.relation_detail && <span style={{ color: 'var(--text-secondary)' }}>{ev.relation_detail}</span>}
                          </div>
                          <p style={{ margin: 0, lineHeight: 1.5, color: 'var(--text-primary)' }}>
                            <span style={{ backgroundColor: 'var(--primary-container)', padding: '0 0.2rem' }}>{ev.excerpt}</span>
                          </p>
                          <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                            来源 ID: {ev.source_document_id.substring(0, 8)}...
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', fontStyle: 'italic' }}>此声明目前没有直接绑定的证据。</p>
                  )}
                </div>
              </div>
            </SectionCard>
          );
        })}
      </div>
    </PageLayout>
  );
};

const getClaimStatusVariant = (status: string): any => {
  switch (status) {
    case 'supported': return 'success';
    case 'mixed': return 'warning';
    case 'contradicted':
    case 'unsupported': return 'error';
    default: return 'default';
  }
};

const formatClaimStatus = (status: string) => {
  switch (status) {
    case 'supported': return '证据充分';
    case 'mixed': return '证据混合';
    case 'contradicted': return '证据反驳';
    case 'unsupported': return '缺乏证据';
    default: return status;
  }
};

const formatEvidenceRelation = (relation: string) => {
  switch (relation) {
    case 'support': return '直接支持';
    case 'weak_support': return '弱支持';
    case 'contradict': return '反驳';
    case 'candidate_support': return '候选支持';
    default: return relation;
  }
};

const getRelationVariant = (relation: string): any => {
  if (relation === 'support') return 'success';
  if (relation === 'weak_support') return 'info';
  if (relation === 'contradict') return 'error';
  return 'default';
};
