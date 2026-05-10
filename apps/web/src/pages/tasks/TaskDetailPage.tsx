import React, { useEffect } from 'react';
import { useParams, Link, useLocation, useNavigate } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { LoadingState } from '../../components/common/LoadingState';
import { ErrorState } from '../../components/common/ErrorState';
import { RuntimeModeBanner } from '../../components/common/RuntimeModeBanner';
import { SectionCard } from '../../components/common/SectionCard';
import { MetricCard } from '../../components/common/MetricCard';
import { StatusBadge } from '../../components/common/StatusBadge';
import { Button } from '../../components/common/Button';
import { Badge } from '../../components/common/Badge';
import { PipelineStepper } from '../../components/common/PipelineStepper';
import { PipelineCounts, PipelineFailure, PipelineRunResponse, ResearchTask, TaskEvent } from '../../features/tasks/types';
import { useCreateTask, useRunTask, useTask, useTaskAction, useTaskEvents } from '../../features/tasks/hooks';
import { formatChinaDateTime, formatChinaTime } from '../../lib/datetime';

export const TaskDetailPage: React.FC = () => {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { task, isLoading, error, refetch } = useTask(taskId);
  const { eventsData, refetch: refetchEvents } = useTaskEvents(taskId);
  const { runTask, isRunning, result: runResult, error: runError } = useRunTask();
  const { mutateTask, isMutating, error: actionError } = useTaskAction();
  const { createTask, isCreating, error: createError } = useCreateTask();
  const initialPipelineResult = (location.state as { pipelineResult?: PipelineRunResponse } | null)?.pipelineResult || null;
  const queuedPipelineResult = runResult || initialPipelineResult;

  useEffect(() => {
    if (!taskId || !task || !activeTaskStatuses.has(task.status)) return;
    const timer = window.setInterval(() => {
      void refetch(true);
      void refetchEvents(true);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [taskId, task?.status, refetch, refetchEvents]);

  const handleRun = async () => {
    if (!taskId || task?.status !== 'PLANNED') return;
    const result = await runTask(taskId);
    await refetch();
    await refetchEvents();
    if (result?.completed) {
      navigate(`/tasks/${taskId}/report`);
    }
  };

  const handleCreateReplacementAndRun = async () => {
    if (!task) return;
    const replacement = await createTask({ query: task.query, constraints: task.constraints });
    if (!replacement) return;

    const result = await runTask(replacement.task_id);
    if (result?.completed) {
      navigate(`/tasks/${replacement.task_id}/report`);
      return;
    }
    navigate(`/tasks/${replacement.task_id}`, {
      state: { pipelineResult: result },
    });
  };

  const handleTaskAction = async (action: 'pause' | 'resume' | 'cancel') => {
    if (!taskId) return;
    const result = await mutateTask(taskId, action);
    if (!result) return;
    await refetch();
    await refetchEvents();
  };

  if (isLoading) return <PageLayout title="研究详情"><LoadingState /></PageLayout>;

  if (error) return (
    <PageLayout title="研究详情">
      <ErrorState error={error} onRetry={refetch} />
      <div style={{ marginTop: '1rem', textAlign: 'center' }}>
        <Link to="/tasks">返回任务列表</Link>
      </div>
    </PageLayout>
  );

  if (!task) return <PageLayout title="研究详情"><p>未找到任务。</p></PageLayout>;

  const canRun = task.status === 'PLANNED';
  const canPause = task.status === 'PLANNED' || activeTaskStatuses.has(task.status);
  const canResume = task.status === 'PAUSED';
  const canCancel = task.status === 'PLANNED' || task.status === 'PAUSED' || activeTaskStatuses.has(task.status);
  const canCreateReplacement = task.status === 'FAILED';
  const actionBusy = isRunning || isCreating || isMutating;

  const observability = task.progress?.observability || null;
  const authoritativePipelineResult = pipelineResultFromTaskDetail(task, observability, eventsData?.events || []);
  const pipelineResult = authoritativePipelineResult || queuedPipelineResult;
  const latestPipelineFailure = pipelineResult?.failure || latestFailureFromEvents(eventsData?.events || []);

  const primaryActionLabel = canCreateReplacement
    ? isCreating || isRunning
      ? '新任务运行中...'
      : '重新创建并运行'
    : isRunning
      ? '运行中...'
      : '开始研究';

  const primaryActionDisabled = actionBusy || (!canRun && !canCreateReplacement);
  const handlePrimaryAction = canCreateReplacement ? handleCreateReplacementAndRun : handleRun;

  const counts = pipelineResult?.counts || emptyCounts;

  return (
    <PageLayout
      title="研究详情"
      actions={
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          {canPause && (
            <Button variant="outline" size="sm" onClick={() => void handleTaskAction('pause')} disabled={actionBusy}>
              暂停
            </Button>
          )}
          {canResume && (
            <Button variant="outline" size="sm" onClick={() => void handleTaskAction('resume')} disabled={actionBusy}>
              继续
            </Button>
          )}
          {canCancel && (
            <Button variant="danger" size="sm" onClick={() => void handleTaskAction('cancel')} disabled={actionBusy}>
              取消
            </Button>
          )}
          {(canRun || canCreateReplacement) && (
            <Button size="sm" onClick={handlePrimaryAction} disabled={primaryActionDisabled} isLoading={isRunning || isCreating}>
              {primaryActionLabel}
            </Button>
          )}
        </div>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
        <ErrorState error={createError} />
        <ErrorState error={runError} />
        <ErrorState error={actionError} />

        <SectionCard>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '2rem', marginBottom: '1.5rem' }}>
            <div style={{ flex: 1 }}>
              <div style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', fontWeight: 600, textTransform: 'uppercase', marginBottom: '0.5rem' }}>
                研究课题
              </div>
              <h2 style={{ fontSize: '1.5rem', fontWeight: 700, margin: 0, lineHeight: 1.3 }}>{task.query}</h2>
            </div>
            <div style={{ textAlign: 'right' }}>
              <StatusBadge status={task.status} />
              <div style={{ marginTop: '0.5rem', color: 'var(--text-secondary)', fontSize: '0.75rem', fontFamily: 'monospace' }}>
                {task.task_id}
              </div>
            </div>
          </div>

          <PipelineStepper 
            currentStatus={task.status} 
            dependencies={pipelineResult?.dependencies || observability?.dependencies}
            counts={counts}
          />
        </SectionCard>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '1rem' }}>
          <MetricCard label="搜索查询" value={counts.search_queries} />
          <MetricCard label="发现 URL" value={counts.candidate_urls} />
          <MetricCard label="获取尝试" value={counts.fetch_attempts} />
          <MetricCard label="源码文档" value={counts.source_documents} />
          <MetricCard label="结论声明" value={counts.claims} />
          <MetricCard label="支持证据" value={counts.claim_evidence} />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '1.5rem' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
            <RuntimeModeBanner
              runningMode={pipelineResult?.running_mode || observability?.running_mode}
              dependencies={pipelineResult?.dependencies || observability?.dependencies || null}
              warnings={observability?.warnings || []}
            />

            <PipelineFailureHelp failure={latestPipelineFailure} dependencies={pipelineResult?.dependencies || null} />

            <SectionCard title="研究工具箱">
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem' }}>
                <Link
                  to={`/tasks/${taskId}/sources`}
                  className="card-solid"
                  style={{ display: 'flex', alignItems: 'center', gap: '1rem', textDecoration: 'none', color: 'inherit' }}
                >
                  <div style={{ fontSize: '1.5rem' }}>📄</div>
                  <div>
                    <div style={{ fontWeight: 600 }}>研究来源</div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>查看网页源码与抓取详情</div>
                  </div>
                </Link>
                <Link
                  to={`/tasks/${taskId}/claims`}
                  className="card-solid"
                  style={{ display: 'flex', alignItems: 'center', gap: '1rem', textDecoration: 'none', color: 'inherit' }}
                >
                  <div style={{ fontSize: '1.5rem' }}>⚖️</div>
                  <div>
                    <div style={{ fontWeight: 600 }}>结论声明</div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>查看提取的声明与验证逻辑</div>
                  </div>
                </Link>
                <Link
                  to={`/tasks/${taskId}/report`}
                  className="card-solid"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '1rem',
                    textDecoration: 'none',
                    color: 'inherit',
                    backgroundColor: task.status === 'COMPLETED' ? 'var(--primary-container)' : 'white',
                    borderColor: task.status === 'COMPLETED' ? 'var(--primary-color)' : 'var(--border-color)',
                  }}
                >
                  <div style={{ fontSize: '1.5rem' }}>📊</div>
                  <div>
                    <div style={{ fontWeight: 600 }}>完整报告</div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>阅读最终生成的研究报告</div>
                  </div>
                </Link>
              </div>
            </SectionCard>

            <TaskObservabilityPanel observability={observability} taskId={taskId!} counts={counts} />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
            <SectionCard title="研究进度日志">
              <TaskEventList events={eventsData?.events || []} />
            </SectionCard>

            <SectionCard title="元数据">
              <div style={{ display: 'grid', gap: '1rem', fontSize: '0.875rem' }}>
                <div>
                  <div style={{ color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>修订版本</div>
                  <div style={{ fontWeight: 500 }}>v{task.revision_no}</div>
                </div>
                <div>
                  <div style={{ color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>创建时间</div>
                  <div style={{ fontWeight: 500 }}>{formatChinaDateTime(task.created_at)}</div>
                </div>
                <div>
                  <div style={{ color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>最后更新</div>
                  <div style={{ fontWeight: 500 }}>{formatChinaDateTime(task.updated_at)}</div>
                </div>
                {task.started_at && (
                  <div>
                    <div style={{ color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>开始时间</div>
                    <div style={{ fontWeight: 500 }}>{formatChinaDateTime(task.started_at)}</div>
                  </div>
                )}
                {task.ended_at && (
                  <div>
                    <div style={{ color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>结束时间</div>
                    <div style={{ fontWeight: 500 }}>{formatChinaDateTime(task.ended_at)}</div>
                  </div>
                )}
              </div>
            </SectionCard>
          </div>
        </div>
      </div>
    </PageLayout>
  );
};

const emptyCounts: PipelineCounts = {
  search_queries: 0,
  candidate_urls: 0,
  fetch_attempts: 0,
  content_snapshots: 0,
  source_documents: 0,
  source_chunks: 0,
  indexed_chunks: 0,
  claims: 0,
  claim_evidence: 0,
  report_artifacts: 0,
};

const activeTaskStatuses = new Set<ResearchTask['status']>([
  'QUEUED',
  'RUNNING',
  'SEARCHING',
  'ACQUIRING',
  'PARSING',
  'INDEXING',
  'DRAFTING_CLAIMS',
  'VERIFYING',
  'RESEARCHING_MORE',
  'REPORTING',
]);

const latestFailureFromEvents = (events: TaskEvent[]): PipelineFailure | null => {
  const failedEvent = events
    .slice()
    .reverse()
    .find((event) => event.event_type === 'pipeline.failed' || event.event_type === 'debug.pipeline.failed');
  const payload = failedEvent?.payload;
  if (!payload) return null;

  return {
    failed_stage: String(payload.failed_stage || payload.stage || 'UNKNOWN'),
    reason: String(payload.reason || 'unknown'),
    exception: typeof payload.exception === 'string' ? payload.exception : null,
    message: String(payload.message || payload.reason || 'Pipeline failed'),
    next_action: String(payload.next_action || 'Inspect task events and server logs.'),
    counts: (payload.counts && typeof payload.counts === 'object') ? payload.counts as PipelineCounts : emptyCounts,
    details: (payload.details && typeof payload.details === 'object') ? payload.details : null,
  };
};

const pipelineResultFromTaskDetail = (
  task: ResearchTask,
  observability: TaskObservability | null,
  events: TaskEvent[],
): PipelineRunResponse | null => {
  const terminal = task.status === 'COMPLETED' || task.status === 'FAILED' || task.status === 'CANCELLED';
  const latestCountEvent = events
    .slice()
    .reverse()
    .find((event) => event.payload?.counts && typeof event.payload.counts === 'object');
  const rawCounts = observability?.pipeline_counts || latestCountEvent?.payload?.counts;
  if (!terminal && !rawCounts) return null;
  const completedEvent = events
    .slice()
    .reverse()
    .find((event) => event.event_type === 'pipeline.completed' || event.event_type === 'debug.pipeline.completed');
  const reportingEvent = events
    .slice()
    .reverse()
    .find((event) => event.payload?.stage === 'REPORTING' && event.payload?.result && typeof event.payload.result === 'object');
  const reportingResult = reportingEvent?.payload?.result || {};
  return {
    task_id: task.task_id,
    status: task.status,
    completed: task.status === 'COMPLETED',
    running_mode: observability?.running_mode || latestCountEvent?.payload?.running_mode || 'unknown-search+unknown-index+unknown-llm',
    stages_completed: completedEvent ? ['SEARCHING', 'ACQUIRING', 'PARSING', 'INDEXING', 'DRAFTING_CLAIMS', 'VERIFYING', 'REPORTING'] : [],
    counts: asPipelineCounts(rawCounts),
    report_artifact_id: typeof reportingResult.report_artifact_id === 'string' ? reportingResult.report_artifact_id : null,
    report_version: typeof reportingResult.report_version === 'number' ? reportingResult.report_version : null,
    report_markdown_preview: typeof reportingResult.report_markdown_preview === 'string' ? reportingResult.report_markdown_preview : null,
    failure: task.status === 'FAILED' ? latestFailureFromEvents(events) : null,
    dependencies: observability?.dependencies || latestCountEvent?.payload?.dependencies || {},
  };
};

const asPipelineCounts = (value: unknown): PipelineCounts => {
  const source = value && typeof value === 'object' ? value as Partial<PipelineCounts> : {};
  return {
    search_queries: numberOrZero(source.search_queries),
    candidate_urls: numberOrZero(source.candidate_urls),
    fetch_attempts: numberOrZero(source.fetch_attempts),
    content_snapshots: numberOrZero(source.content_snapshots),
    source_documents: numberOrZero(source.source_documents),
    source_chunks: numberOrZero(source.source_chunks),
    indexed_chunks: numberOrZero(source.indexed_chunks),
    claims: numberOrZero(source.claims),
    claim_evidence: numberOrZero(source.claim_evidence),
    report_artifacts: numberOrZero(source.report_artifacts),
  };
};

const numberOrZero = (value: unknown): number => typeof value === 'number' ? value : 0;

const PipelineFailureHelp: React.FC<{ failure: PipelineFailure | null; dependencies: Record<string, any> | null }> = ({ failure, dependencies }) => {
  if (!failure) return null;

  const isSearxngHtmlResponse = failure.reason === 'searxng_html_response';

  return (
    <SectionCard title="诊断与帮助" style={{ border: '1px solid #fce8e6', backgroundColor: '#fffbfa' }}>
      <div style={{ color: '#d93025', fontWeight: 600, marginBottom: '0.5rem' }}>
        研究中断: {failure.failed_stage} - {failure.reason}
      </div>
      <p style={{ margin: '0.5rem 0', fontSize: '0.875rem' }}>{failure.message}</p>

      {isSearxngHtmlResponse && (
        <div style={{ marginTop: '1rem', padding: '1rem', backgroundColor: 'white', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)' }}>
          <p style={{ marginTop: 0, fontSize: '0.875rem' }}>
            搜索阶段收到 HTML 页面，而不是 SearXNG JSON。常见原因是 SEARXNG_BASE_URL 配置错误。
          </p>
          <div style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
            当前搜索配置: {dependencies?.search_provider || 'searxng'} / {dependencies?.searxng_base_url || '未记录'}
          </div>
        </div>
      )}

      <div style={{ marginTop: '1rem', fontSize: '0.875rem', fontWeight: 600 }}>
        后端建议: <span style={{ fontWeight: 400 }}>{failure.next_action}</span>
      </div>
    </SectionCard>
  );
};

type TaskObservability = NonNullable<NonNullable<ResearchTask['progress']>['observability']>;

const TaskObservabilityPanel: React.FC<{ observability: TaskObservability | null, taskId: string, counts: PipelineCounts }> = ({ observability, taskId, counts }) => {
  if (!observability) return null;

  const researchPlan = observability.research_plan || null;
  const planSubquestions = Array.isArray(researchPlan?.subquestions) ? researchPlan.subquestions : [];
  const selectedSources = Array.isArray(observability.selected_sources) ? observability.selected_sources : [];
  const attemptedSources = Array.isArray(observability.attempted_sources) ? observability.attempted_sources : [];
  const unattemptedSources = Array.isArray(observability.unattempted_sources) ? observability.unattempted_sources : [];
  const failedSources = Array.isArray(observability.failed_sources) ? observability.failed_sources : [];

  const sourceRows = buildSourceSelectionRows({
    selectedSources,
    attemptedSources,
    unattemptedSources,
    failedSources,
  });

  const evidenceYield = observability.evidence_yield_summary;
  const verification = observability.verification_summary;
  const gapAnalysis = observability.gap_analysis;

  // Use pipeline counts for the top 3 metrics as they are more reliable after completion
  const searchFoundCount = counts.candidate_urls || observability.search_result_count || 0;
  const fetchSucceededCount = observability.fetch_succeeded ?? counts.source_documents ?? 0;
  // If fetch_failed is not in counts, we use observability or derived value
  const fetchFailedCount = observability.fetch_failed ?? Math.max(0, counts.fetch_attempts - fetchSucceededCount);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <SectionCard title="实时观测器">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '1rem' }}>
          <div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase' }}>搜索发现</div>
            <div style={{ fontSize: '1.25rem', fontWeight: 700 }}>{searchFoundCount}</div>
          </div>
          <div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase' }}>获取成功</div>
            <div style={{ fontSize: '1.25rem', fontWeight: 700, color: '#1e8e3e' }}>{fetchSucceededCount}</div>
          </div>
          <div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase' }}>获取失败</div>
            <div style={{ fontSize: '1.25rem', fontWeight: 700, color: '#d93025' }}>{fetchFailedCount}</div>
          </div>
        </div>

        {researchPlan && (
          <div style={{ marginTop: '1.5rem', borderTop: '1px solid var(--border-color)', paddingTop: '1.5rem' }}>
            <div style={{ fontWeight: 600, marginBottom: '0.75rem' }}>研究规划</div>
            <div style={{ fontSize: '0.875rem', backgroundColor: '#f8f9fa', padding: '1rem', borderRadius: 'var(--radius-sm)' }}>
              <div style={{ marginBottom: '0.5rem' }}><span style={{ color: 'var(--text-secondary)' }}>意图:</span> {researchPlan.intent}</div>
              {planSubquestions.length > 0 && (
                <div style={{ marginBottom: '0.5rem' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>分解:</span> {planSubquestions.join('; ')}
                </div>
              )}
            </div>
          </div>
        )}
      </SectionCard>

      {(evidenceYield || verification || gapAnalysis) && (
        <SectionCard title="研究深度指标">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1.5rem' }}>
            {evidenceYield && (
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>证据产出</div>
                <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                  候选 {evidenceYield.total_candidates ?? 0} <br />
                  接受 {evidenceYield.accepted_candidates ?? 0} <br />
                  硬拒绝 {evidenceYield.rejected_candidates ?? 0} <br />
                  未选 {evidenceYield.unselected_candidates ?? 0}
                </div>
              </div>
            )}
            {verification && (
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>验证质量</div>
                <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                  强支持 {verification.strong_support_evidence_count ?? verification.strong_supported_claim_count ?? 0} <br />
                  弱支持 {verification.weak_support_evidence_count ?? verification.weak_supported_claim_count ?? 0} <br />
                  反驳 {verification.contradict_evidence_count ?? 0}
                </div>
              </div>
            )}
            {gapAnalysis && (
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>缺口分析</div>
                <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                  触发补充: {gapAnalysis.triggered ? '是' : '否'} <br />
                  轮次: {gapAnalysis.round_no ?? 0} <br />
                  缺失槽位: {Array.isArray(gapAnalysis.required_slots_missing) ? gapAnalysis.required_slots_missing.length : 0}
                </div>
              </div>
            )}
          </div>
        </SectionCard>
      )}

      <SectionCard title="来源审阅详情">
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
            <thead>
              <tr>
                <th style={thStyle}>域名</th>
                <th style={thStyle}>类别</th>
                <th style={thStyle}>状态</th>
                <th style={thStyle}>质量</th>
                <th style={thStyle}>贡献度</th>
              </tr>
            </thead>
            <tbody>
              {sourceRows.slice(0, 10).map((source: any) => (
                <tr key={source.row_key}>
                  <td style={tdStyle}>{source.domain || '未知'}</td>
                  <td style={tdStyle}>{source.category || '-'}</td>
                  <td style={tdStyle}><Badge variant="info">{source.state}</Badge></td>
                  <td style={tdStyle}>{formatOptionalNumber(source.quality)}</td>
                  <td style={tdStyle}>{source.source_yield?.contribution_level || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {sourceRows.length > 10 && (
            <div style={{ marginTop: '0.75rem', textAlign: 'center' }}>
              <Link to={`/tasks/${taskId}/sources`} style={{ fontSize: '0.875rem' }}>查看全部 {sourceRows.length} 个来源</Link>
            </div>
          )}
        </div>
      </SectionCard>
    </div>
  );
};

const buildSourceSelectionRows = ({
  selectedSources,
  attemptedSources,
  unattemptedSources,
  failedSources,
}: {
  selectedSources: any[];
  attemptedSources: any[];
  unattemptedSources: any[];
  failedSources: any[];
}) => {
  const byKey = new Map<string, any>();

  const merge = (source: any, state: string) => {
    const key = source.candidate_url_id || source.fetch_attempt_id || source.canonical_url || source.final_url || `${state}-${byKey.size}`;
    const existing = byKey.get(key) || {};
    const states = new Set<string>(String(existing.state || '').split(', ').filter(Boolean));
    states.add(state);
    const merged = { ...existing, ...source, row_key: key, state: Array.from(states).join(', ') };
    byKey.set(key, normalizeSourceRow(merged));
  };

  selectedSources.forEach((source) => merge(source, 'selected'));
  attemptedSources.forEach((source) => merge(source, 'attempted'));
  unattemptedSources.forEach((source) => merge(source, 'unattempted'));
  failedSources.forEach((source) => merge(source, 'failed'));

  return Array.from(byKey.values());
};

const normalizeSourceRow = (source: any) => {
  const finalUrl = source.final_url || source.canonical_url || source.url;
  return {
    ...source,
    final_url: finalUrl,
    domain: source.domain || domainFromUrl(finalUrl),
    category: source.source_category || source.source_intent || source.category || source.metadata?.source_category,
    quality: source.source_quality_score || source.final_source_score || source.quality_score,
  };
};

const domainFromUrl = (value: unknown): string | null => {
  if (typeof value !== 'string' || !value) return null;
  try {
    return new URL(value).hostname;
  } catch {
    return null;
  }
};

const formatOptionalNumber = (value: unknown): string => {
  if (typeof value !== 'number') return '-';
  return value.toFixed(2);
};

const TaskEventList: React.FC<{ events: TaskEvent[] }> = ({ events }) => {
  if (events.length === 0) {
    return <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem' }}>等待研究开始...</p>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {events.slice().reverse().slice(0, 10).map((event) => (
        <div key={event.event_id} style={{ display: 'flex', gap: '0.75rem' }}>
          <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: 'var(--primary-color)', marginTop: '0.4rem', flexShrink: 0 }} />
          <div>
            <div style={{ fontWeight: 600, fontSize: '0.875rem' }}>{event.event_type}</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem' }}>
              {formatChinaTime(event.created_at)}
              {event.payload?.stage ? ` · ${event.payload.stage}` : ''}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
};

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  borderBottom: '1px solid var(--border-color)',
  padding: '0.75rem 0.5rem',
  color: 'var(--text-secondary)',
  fontSize: '0.75rem',
  textTransform: 'uppercase',
  fontWeight: 700,
};

const tdStyle: React.CSSProperties = {
  borderBottom: '1px solid var(--border-color)',
  padding: '0.75rem 0.5rem',
  verticalAlign: 'top',
};
