import React from 'react';

import { PipelineCounts } from '../../types/api';

interface Stage {
  id: string;
  label: string;
}

interface PipelineStepperProps {
  currentStatus: string;
  dependencies?: Record<string, any> | null;
  counts?: PipelineCounts | null;
}

export const PipelineStepper: React.FC<PipelineStepperProps> = ({ currentStatus, dependencies, counts }) => {
  const getStages = (): Stage[] => {
    const stages: Stage[] = [];
    
    if (dependencies?.research_planner_enabled !== false) {
      stages.push({ id: 'RUNNING', label: '研究规划' });
    }

    stages.push({ id: 'SEARCHING', label: '搜索发现' });

    if (dependencies?.llm_source_judge_enabled) {
      stages.push({ id: 'SOURCE_JUDGE', label: '来源评价' });
    }

    if (dependencies?.llm_source_triage_active) {
      stages.push({ id: 'SOURCE_TRIAGE', label: '深度分流' });
    }

    stages.push(
      { id: 'ACQUIRING', label: '网页获取' },
      { id: 'PARSING', label: '解析提取' },
      { id: 'INDEXING', label: '索引构建' },
      { id: 'DRAFTING_CLAIMS', label: '结论起草' },
      { id: 'VERIFYING', label: '证据验证' },
      { id: 'REPORTING', label: '报告生成' }
    );

    return stages;
  };

  const stages = getStages();

  const getStageIndex = (status: string) => {
    const index = stages.findIndex(s => s.id === status);
    
    // Custom logic for sub-stages during ACQUIRING
    if (status === 'ACQUIRING') {
      const triageIndex = stages.findIndex(s => s.id === 'SOURCE_TRIAGE');
      const acquireIndex = stages.findIndex(s => s.id === 'ACQUIRING');
      // If we are in ACQUIRING status but haven't started fetching, we are likely in Triage
      if (triageIndex !== -1 && counts && counts.fetch_attempts === 0) {
        return triageIndex;
      }
      return acquireIndex !== -1 ? acquireIndex : index;
    }

    // Custom logic for sub-stages during SEARCHING
    if (status === 'SEARCHING') {
      const searchingIndex = stages.findIndex(s => s.id === 'SEARCHING');
      // We don't have a great way to tell if we are judging vs searching without more events,
      // but if we have candidate URLs and are still in SEARCHING, we might be judging.
      // However, candidate_urls might only update after the whole stage.
      // For now, stick to SEARCHING.
      return searchingIndex !== -1 ? searchingIndex : index;
    }

    if (index !== -1) return index;
    
    // Fallback logic for intermediate or terminal states
    if (status === 'COMPLETED') return stages.length - 1;
    if (status === 'FAILED') return -1; 
    if (status === 'RESEARCHING_MORE') {
      const verifyIndex = stages.findIndex(s => s.id === 'VERIFYING');
      return verifyIndex !== -1 ? verifyIndex : -1;
    }
    if (status === 'RUNNING') {
      const planningIndex = stages.findIndex(s => s.id === 'RUNNING');
      return planningIndex !== -1 ? planningIndex : 0;
    }
    if (status === 'QUEUED') return -1;
    if (status === 'PLANNED') return -1;
    
    return -1;
  };

  const currentIndex = getStageIndex(currentStatus);

  return (
    <div style={{ display: 'flex', alignItems: 'center', width: '100%', padding: '1rem 0', overflowX: 'auto' }}>
      {stages.map((stage, index) => {
        const isCompleted = index < currentIndex || currentStatus === 'COMPLETED';
        const isCurrent = index === currentIndex && currentStatus !== 'COMPLETED';
        
        return (
          <React.Fragment key={stage.id}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: '80px', flex: 1 }}>
              <div
                style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '0.875rem',
                  fontWeight: 600,
                  backgroundColor: isCompleted || isCurrent ? 'var(--primary-color)' : '#e0e0e0',
                  color: isCompleted || isCurrent ? 'white' : '#757575',
                  transition: 'all 0.3s ease',
                  zIndex: 1,
                  boxShadow: isCurrent ? '0 0 0 4px var(--primary-container)' : 'none',
                }}
              >
                {isCompleted ? '✓' : index + 1}
              </div>
              <span
                style={{
                  marginTop: '0.5rem',
                  fontSize: '0.75rem',
                  fontWeight: isCurrent ? 700 : 500,
                  color: isCurrent ? 'var(--primary-color)' : 'var(--text-secondary)',
                  textAlign: 'center',
                  whiteSpace: 'nowrap'
                }}
              >
                {stage.label}
              </span>
            </div>
            {index < stages.length - 1 && (
              <div
                style={{
                  height: '2px',
                  flex: 1,
                  backgroundColor: isCompleted ? 'var(--primary-color)' : '#e0e0e0',
                  alignSelf: 'flex-start',
                  marginTop: '16px',
                  marginRight: '-40px',
                  marginLeft: '-40px',
                }}
              />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
};
