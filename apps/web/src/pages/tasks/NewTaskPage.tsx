import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { ErrorState } from '../../components/common/ErrorState';
import { RuntimeModeBanner } from '../../components/common/RuntimeModeBanner';
import { SectionCard } from '../../components/common/SectionCard';
import { Button } from '../../components/common/Button';
import { Badge } from '../../components/common/Badge';
import { LoadingState } from '../../components/common/LoadingState';
import { PipelineRunResponse, ResearchPlanResponse } from '../../features/tasks/types';
import { useCreateTask, usePlanTask, useRunTask } from '../../features/tasks/hooks';

export const NewTaskPage: React.FC = () => {
  const navigate = useNavigate();
  const { createTask, isCreating, error } = useCreateTask();
  const { planTask, isPlanning, error: planError } = usePlanTask();
  const { runTask, isRunning, result: runResult, error: runError } = useRunTask();

  const [query, setQuery] = useState('');
  const [reportLanguage, setReportLanguage] = useState('zh-CN');
  const [taskId, setTaskId] = useState<string | null>(null);
  const [planDraft, setPlanDraft] = useState('');
  const [activePlan, setActivePlan] = useState<ResearchPlanResponse | null>(null);
  const [planParseError, setPlanParseError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    const result = await createTask({
      query,
      report_language: reportLanguage,
    });
    if (result) {
      setTaskId(result.task_id);
      const plan = await planTask(result.task_id);
      if (plan) {
        setActivePlan(plan);
        setPlanDraft(JSON.stringify(plan.research_plan, null, 2));
      }
    }
  };

  const handleRunConfirmedPlan = async () => {
    if (!taskId) return;
    setPlanParseError(null);

    let parsedPlan: Record<string, any>;
    try {
      parsedPlan = JSON.parse(planDraft);
    } catch (err) {
      setPlanParseError(err instanceof Error ? err.message : '研究计划 JSON 无法解析');
      return;
    }

    const confirmedPlan = await planTask(taskId, { research_plan: parsedPlan });
    if (!confirmedPlan) return;
    setActivePlan(confirmedPlan);

    const pipelineResult = await runTask(taskId);
    if (pipelineResult?.completed) {
      navigate(`/tasks/${taskId}/report`);
      return;
    }
    navigate(`/tasks/${taskId}`, {
      state: { pipelineResult },
    });
  };

  const handleRegeneratePlan = async () => {
    if (!taskId) return;
    setPlanParseError(null);
    const plan = await planTask(taskId);
    if (plan) {
      setActivePlan(plan);
      setPlanDraft(JSON.stringify(plan.research_plan, null, 2));
    }
  };

  const resetTask = () => {
    setTaskId(null);
    setPlanDraft('');
    setActivePlan(null);
    setPlanParseError(null);
  };

  const busy = isCreating || isPlanning || isRunning;
  const displayedPlan = taskId ? activePlan : null;
  const isAwaitingInitialPlan = Boolean(taskId && isPlanning && !displayedPlan);
  const didPlanGenerationFail = Boolean(taskId && !isPlanning && !displayedPlan);

  return (
    <PageLayout maxWidth="800px">
      <div style={{ textAlign: 'center', marginBottom: '3rem', marginTop: '2rem' }}>
        <h1 style={{ fontSize: '2.5rem', marginBottom: '1rem', background: 'var(--accent-gradient)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
          开启深度研究
        </h1>
        <p style={{ fontSize: '1.125rem', color: 'var(--text-secondary)', maxWidth: '600px', margin: '0 auto' }}>
          开源情报收集与溯源系统将根据您的问题自主规划研究路径，搜集真实网页证据并生成专业报告。
        </p>
      </div>

      <SectionCard>
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          <ErrorState error={error} />
          <ErrorState error={planError} />
          <ErrorState error={runError} />
          <PipelineRunSummary result={runResult} />

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            <label htmlFor="query" style={{ fontWeight: 600, fontSize: '1rem' }}>您想研究什么？</label>
            <textarea
              id="query"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="例如：近30天 NVIDIA 在开源模型生态上的关键发布与影响"
              rows={4}
              style={{ 
                padding: '1rem', 
                fontFamily: 'inherit', 
                borderRadius: 'var(--radius-md)', 
                border: '1px solid var(--border-color)',
                fontSize: '1rem',
                resize: 'vertical',
                outline: 'none',
                transition: 'border-color 0.2s',
              }}
              onFocus={(e) => e.target.style.borderColor = 'var(--primary-color)'}
              onBlur={(e) => e.target.style.borderColor = 'var(--border-color)'}
              required
              disabled={busy || Boolean(taskId)}
            />
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <label htmlFor="report-language" style={{ fontWeight: 600, fontSize: '0.875rem' }}>报告语言</label>
              <select
                id="report-language"
                value={reportLanguage}
                onChange={(e) => setReportLanguage(e.target.value)}
                disabled={busy || Boolean(taskId)}
                style={{ 
                  padding: '0.5rem 2rem 0.5rem 0.75rem', 
                  fontFamily: 'inherit', 
                  borderRadius: 'var(--radius-sm)', 
                  border: '1px solid var(--border-color)',
                  backgroundColor: 'white',
                }}
              >
                <option value="zh-CN">中文 (简体)</option>
                <option value="en-US">English</option>
              </select>
            </div>

            {!taskId && (
              <Button
                type="submit"
                isLoading={isCreating || isPlanning}
                disabled={busy || !query.trim()}
                size="lg"
              >
                开始规划研究
              </Button>
            )}
          </div>
        </form>
      </SectionCard>

      {isAwaitingInitialPlan && (
        <SectionCard>
          <LoadingState message="正在生成研究计划..." />
        </SectionCard>
      )}

      {didPlanGenerationFail && (
        <SectionCard style={{ border: '1px solid #fce8e6', backgroundColor: '#fffbfa' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem' }}>
            <strong>任务已创建，但研究计划生成失败。</strong>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', textAlign: 'center' }}>
              可以重试计划生成，或放弃当前空任务后重新创建。
            </div>
            <div style={{ display: 'flex', gap: '0.75rem' }}>
              <Button onClick={handleRegeneratePlan} isLoading={isPlanning}>
                重新生成计划
              </Button>
              <Button variant="outline" onClick={resetTask}>
                放弃并重来
              </Button>
            </div>
          </div>
        </SectionCard>
      )}

      {displayedPlan && (
        <div style={{ marginTop: '2rem', animation: 'fadeIn 0.5s ease-out' }}>
          <style>{`
            @keyframes fadeIn {
              from { opacity: 0; transform: translateY(10px); }
              to { opacity: 1; transform: translateY(0); }
            }
          `}</style>
          
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
            <h2 style={{ fontSize: '1.5rem', margin: 0 }}>研究计划</h2>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <Badge variant="info">{displayedPlan.planner_mode}</Badge>
              <Badge variant="info">{displayedPlan.plan_source}</Badge>
            </div>
          </div>

          <RuntimeModeBanner
            runningMode={displayedPlan.running_mode}
            dependencies={displayedPlan.dependencies}
            warnings={displayedPlan.warnings}
          />

          <SectionCard>
            <PlanPreview plan={displayedPlan.research_plan || {}} />
            
            <details style={{ marginTop: '1.5rem' }}>
              <summary style={{ cursor: 'pointer', color: 'var(--text-secondary)', fontSize: '0.875rem', fontWeight: 600 }}>
                查看/编辑 计划 JSON
              </summary>
              <textarea
                id="plan-json"
                value={planDraft}
                onChange={(event) => setPlanDraft(event.target.value)}
                rows={12}
                disabled={busy}
                style={{ 
                  width: '100%', 
                  marginTop: '0.75rem', 
                  padding: '1rem', 
                  fontFamily: 'monospace', 
                  fontSize: '0.875rem',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-color)',
                  backgroundColor: '#f8f9fa'
                }}
              />
              {planParseError && <div style={{ marginTop: '0.5rem', color: '#d93025', fontSize: '0.875rem' }}>{planParseError}</div>}
            </details>

            <div style={{ display: 'flex', gap: '1rem', marginTop: '2rem' }}>
              <Button
                onClick={handleRunConfirmedPlan}
                isLoading={isRunning}
                disabled={busy || !planDraft.trim()}
                size="lg"
                style={{ flex: 1 }}
              >
                确认计划并开始研究
              </Button>
              <Button variant="outline" onClick={resetTask} size="lg">
                重新创建
              </Button>
            </div>
          </SectionCard>
        </div>
      )}
    </PageLayout>
  );
};

const PlanPreview: React.FC<{ plan: Record<string, any> }> = ({ plan }) => {
  const subquestions = Array.isArray(plan.subquestions) ? plan.subquestions : [];
  const searchQueries = Array.isArray(plan.search_queries) ? plan.search_queries : [];

  return (
    <div style={{ display: 'grid', gap: '1.5rem' }}>
      <div>
        <div style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', fontWeight: 600, textTransform: 'uppercase', marginBottom: '0.5rem' }}>
          研究意图
        </div>
        <div style={{ fontSize: '1.125rem', fontWeight: 500 }}>{plan.intent || '未记录'}</div>
      </div>
      
      {subquestions.length > 0 && (
        <div>
          <div style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', fontWeight: 600, textTransform: 'uppercase', marginBottom: '0.5rem' }}>
            分解子问题 ({subquestions.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {subquestions.map((item: string, i: number) => (
              <div key={i} style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
                <div style={{ color: 'var(--primary-color)', fontWeight: 700 }}>{i + 1}.</div>
                <div style={{ color: 'var(--text-primary)' }}>{item}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {searchQueries.length > 0 && (
        <div>
          <div style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', fontWeight: 600, textTransform: 'uppercase', marginBottom: '0.5rem' }}>
            计划搜索查询 ({searchQueries.length})
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
            {searchQueries.map((item: any, index: number) => (
              <Badge key={index} variant="secondary" style={{ padding: '0.5rem 0.85rem', fontSize: '0.875rem' }}>
                {item.query_text || String(item)}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

const PipelineRunSummary: React.FC<{ result: PipelineRunResponse | null }> = ({ result }) => {
  if (!result) return null;

  return (
    <div 
      className="card-solid" 
      style={{ 
        border: '1px solid var(--border-color)', 
        backgroundColor: '#f8f9fa', 
        marginBottom: '1rem',
        padding: '1rem'
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
        <strong>运行状态: {result.status}</strong>
        <Badge variant="info">{result.running_mode}</Badge>
      </div>
      <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
        已完成: {result.stages_completed.join(' → ') || '开始中'}
      </div>
      {result.failure && (
        <div style={{ marginTop: '0.75rem', color: '#d93025', fontSize: '0.875rem' }}>
          <strong>失败:</strong> {result.failure.reason} <br />
          <strong>建议:</strong> {result.failure.next_action}
        </div>
      )}
    </div>
  );
};
