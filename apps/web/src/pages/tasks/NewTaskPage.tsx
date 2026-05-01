import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { ErrorState } from '../../components/common/ErrorState';
import { RuntimeModeBanner } from '../../components/common/RuntimeModeBanner';
import { PipelineRunResponse, ResearchPlanResponse } from '../../features/tasks/types';
import { useCreateTask, usePlanTask, useRunTask } from '../../features/tasks/hooks';

export const NewTaskPage: React.FC = () => {
  const navigate = useNavigate();
  const { createTask, isCreating, error } = useCreateTask();
  const { planTask, isPlanning, result: planResult, error: planError } = usePlanTask();
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
      constraints: {
        language: reportLanguage,
        report_language: reportLanguage,
      },
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
  const displayedPlan = activePlan || planResult;

  return (
    <PageLayout title="创建新任务">
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem', maxWidth: '760px' }}>
        <ErrorState error={error} />
        <ErrorState error={planError} />
        <ErrorState error={runError} />
        <PipelineRunSummary result={runResult} />

        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <label htmlFor="query" style={{ fontWeight: 'bold' }}>研究问题</label>
          <textarea
            id="query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="例如：近30天 NVIDIA 在开源模型生态上的关键发布与影响"
            rows={4}
            style={{ padding: '0.5rem', fontFamily: 'inherit' }}
            required
            disabled={busy || Boolean(taskId)}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <label htmlFor="report-language" style={{ fontWeight: 'bold' }}>报告语言</label>
          <select
            id="report-language"
            value={reportLanguage}
            onChange={(e) => setReportLanguage(e.target.value)}
            disabled={busy || Boolean(taskId)}
            style={{ padding: '0.5rem', fontFamily: 'inherit', maxWidth: '220px' }}
          >
            <option value="zh-CN">中文（简体）</option>
            <option value="en-US">English</option>
          </select>
        </div>

        {!taskId && (
          <button
            type="submit"
            disabled={busy || !query.trim()}
            style={{ padding: '0.75rem', cursor: busy ? 'not-allowed' : 'pointer', fontWeight: 'bold' }}
          >
            {isCreating ? '创建中...' : isPlanning ? '生成研究计划中...' : '创建任务并生成研究计划'}
          </button>
        )}
      </form>

      {taskId && !displayedPlan && (
        <section style={{ marginTop: '1.5rem', maxWidth: '760px', border: '1px solid #f3c27a', borderRadius: '8px', padding: '1rem', backgroundColor: '#fff8ed' }}>
          <strong>任务已创建，研究计划尚未生成。</strong>
          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1rem', flexWrap: 'wrap' }}>
            <button
              type="button"
              onClick={handleRegeneratePlan}
              disabled={busy}
              style={{ padding: '0.75rem 1rem', cursor: busy ? 'not-allowed' : 'pointer', fontWeight: 'bold' }}
            >
              {isPlanning ? '生成研究计划中...' : '重新生成研究计划'}
            </button>
            <button type="button" onClick={resetTask} disabled={busy} style={{ padding: '0.75rem 1rem' }}>
              创建另一个任务
            </button>
          </div>
        </section>
      )}

      {displayedPlan && (
        <section style={{ marginTop: '1.5rem', display: 'grid', gap: '1rem', maxWidth: '920px' }}>
          <RuntimeModeBanner
            runningMode={displayedPlan.running_mode}
            dependencies={displayedPlan.dependencies}
            warnings={displayedPlan.warnings}
          />
          <section style={{ border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1rem', backgroundColor: '#fff' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
              <strong>研究计划</strong>
              <span style={{ color: '#64748b' }}>
                {displayedPlan.planner_mode} / {displayedPlan.plan_source}
              </span>
            </div>
            <PlanPreview plan={displayedPlan.research_plan || {}} />
            <label htmlFor="plan-json" style={{ display: 'block', marginTop: '1rem', fontWeight: 'bold' }}>
              可编辑计划 JSON
            </label>
            <textarea
              id="plan-json"
              value={planDraft}
              onChange={(event) => setPlanDraft(event.target.value)}
              rows={16}
              disabled={busy}
              style={{ width: '100%', marginTop: '0.5rem', padding: '0.75rem', fontFamily: 'monospace', fontSize: '0.9rem' }}
            />
            {planParseError && <div style={{ marginTop: '0.5rem', color: '#b91c1c' }}>{planParseError}</div>}
            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1rem', flexWrap: 'wrap' }}>
              <button
                type="button"
                onClick={handleRunConfirmedPlan}
                disabled={busy || !planDraft.trim()}
                style={{ padding: '0.75rem 1rem', cursor: busy ? 'not-allowed' : 'pointer', fontWeight: 'bold' }}
              >
                {isPlanning ? '保存计划中...' : isRunning ? '运行 DeepSearch 中...' : '确认计划并开始研究'}
              </button>
              <button type="button" onClick={resetTask} disabled={busy} style={{ padding: '0.75rem 1rem' }}>
                创建另一个任务
              </button>
            </div>
          </section>
        </section>
      )}
    </PageLayout>
  );
};

const PlanPreview: React.FC<{ plan: Record<string, any> }> = ({ plan }) => {
  const subquestions = Array.isArray(plan.subquestions) ? plan.subquestions : [];
  const searchQueries = Array.isArray(plan.search_queries) ? plan.search_queries : [];

  return (
    <div style={{ marginTop: '0.75rem', display: 'grid', gap: '0.75rem' }}>
      <div><strong>意图:</strong> {plan.intent || '未记录'}</div>
      {subquestions.length > 0 && (
        <div>
          <div style={{ color: '#475569' }}>子问题</div>
          <ul style={{ marginTop: '0.35rem', paddingLeft: '1.25rem' }}>
            {subquestions.map((item: string) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
      {searchQueries.length > 0 && (
        <div>
          <div style={{ color: '#475569' }}>计划搜索查询</div>
          <ol style={{ marginTop: '0.35rem', paddingLeft: '1.25rem' }}>
            {searchQueries.map((item: any, index: number) => (
              <li key={`${index}-${item.query_text || item}`}>
                {item.query_text || String(item)}
                {item.expected_source_type ? <span style={{ color: '#64748b' }}> ({item.expected_source_type})</span> : null}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
};

const PipelineRunSummary: React.FC<{ result: PipelineRunResponse | null }> = ({ result }) => {
  if (!result) return null;

  return (
    <section style={{ border: '1px solid #ddd', borderRadius: '4px', padding: '1rem', backgroundColor: '#fafafa' }}>
      <strong>流程模式:</strong> {result.running_mode} <br />
      <strong>状态:</strong> {result.status} <br />
      <strong>已完成阶段:</strong> {result.stages_completed.join(' -> ') || '无'} <br />
      {result.failure && (
        <>
          <strong>失败阶段:</strong> {result.failure.failed_stage} <br />
          <strong>原因:</strong> {result.failure.reason} <br />
          <strong>下一步建议:</strong> {result.failure.next_action}
        </>
      )}
    </section>
  );
};
