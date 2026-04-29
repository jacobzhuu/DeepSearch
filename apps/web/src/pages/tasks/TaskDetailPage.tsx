import React from 'react';
import { useParams, Link, useLocation, useNavigate } from 'react-router-dom';
import { PageLayout } from '../../components/layout/PageLayout';
import { LoadingState } from '../../components/common/LoadingState';
import { ErrorState } from '../../components/common/ErrorState';
import { PipelineCounts, PipelineFailure, PipelineRunResponse, ResearchTask, TaskEvent } from '../../features/tasks/types';
import { useCreateTask, useRunTask, useTask, useTaskEvents } from '../../features/tasks/hooks';

export const TaskDetailPage: React.FC = () => {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { task, isLoading, error, refetch } = useTask(taskId);
  const { eventsData, refetch: refetchEvents } = useTaskEvents(taskId);
  const { runTask, isRunning, result: runResult, error: runError } = useRunTask();
  const { createTask, isCreating, error: createError } = useCreateTask();
  const initialPipelineResult = (location.state as { pipelineResult?: PipelineRunResponse } | null)?.pipelineResult || null;
  const pipelineResult = runResult || initialPipelineResult;

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

  if (isLoading) return <PageLayout title="任务详情"><LoadingState /></PageLayout>;
  
  if (error) return (
    <PageLayout title="任务详情">
      <ErrorState error={error} onRetry={refetch} />
      <Link to="/tasks/new">返回主页</Link>
    </PageLayout>
  );

  if (!task) return <PageLayout title="任务详情"><p>未找到任务。</p></PageLayout>;

  const canRun = task.status === 'PLANNED';
  const canCreateReplacement = task.status === 'FAILED';
  const actionBusy = isRunning || isCreating;
  const actionNote = canRun
    ? null
    : canCreateReplacement
      ? '该任务已失败并保留在审计账本中，不能原地重跑。可以用相同查询创建一个新任务重新运行。'
      : '只有处于 PLANNED 状态的任务才能运行。请在运行前创建一个新任务或修改该任务。';
  const latestPipelineFailure = pipelineResult?.failure || latestFailureFromEvents(eventsData?.events || []);
  const primaryActionLabel = canCreateReplacement
    ? isCreating || isRunning
      ? '新任务运行中...'
      : '用相同查询新建任务'
    : isRunning
      ? '运行中...'
      : '运行 DeepSearch';
  const primaryActionDisabled = actionBusy || (!canRun && !canCreateReplacement);
  const handlePrimaryAction = canCreateReplacement ? handleCreateReplacementAndRun : handleRun;

  return (
    <PageLayout 
      title="任务详情"
      actions={
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', alignItems: 'flex-end' }}>
          <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
            <span style={{ padding: '0.25rem 0.75rem', backgroundColor: '#eee', borderRadius: '1rem', fontSize: '0.875rem', fontWeight: 'bold' }}>
              {task.status}
            </span>
            <button
              onClick={handlePrimaryAction}
              disabled={primaryActionDisabled}
              aria-describedby={actionNote ? 'run-disabled-reason' : undefined}
              style={{ ...buttonStyle, border: 0, opacity: primaryActionDisabled ? 0.65 : 1, cursor: primaryActionDisabled ? 'not-allowed' : 'pointer' }}
            >
              {primaryActionLabel}
            </button>
          </div>
          {actionNote && (
            <div id="run-disabled-reason" style={{ maxWidth: '22rem', fontSize: '0.8rem', color: '#7a4b00', textAlign: 'right' }}>
              {actionNote}
            </div>
          )}
        </div>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
        <ErrorState error={createError} />
        <ErrorState error={runError} />
        <PipelineFailureHelp failure={latestPipelineFailure} dependencies={pipelineResult?.dependencies || null} />
        <PipelineResultPanel result={pipelineResult} />
        
        <section style={{ backgroundColor: '#f9f9f9', padding: '1.5rem', borderRadius: '8px' }}>
          <h2 style={{ marginTop: 0, fontSize: '1.25rem' }}>查询内容</h2>
          <p style={{ margin: 0, fontSize: '1.1rem' }}>{task.query}</p>
        </section>

        <section>
          <h3 style={{ borderBottom: '1px solid #eee', paddingBottom: '0.5rem' }}>元数据</h3>
          <ul style={{ listStyle: 'none', padding: 0, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <li><strong>任务 ID:</strong> <br/><span style={{ fontFamily: 'monospace', fontSize: '0.9em' }}>{task.task_id}</span></li>
            <li><strong>修订版本:</strong> <br/>{task.revision_no}</li>
            <li><strong>进度:</strong> <br/>{task.progress?.current_state || task.status}</li>
            <li><strong>事件总数:</strong> <br/>{task.progress?.events_total ?? 0}</li>
            <li><strong>创建时间:</strong> <br/>{new Date(task.created_at).toLocaleString()}</li>
            <li><strong>更新时间:</strong> <br/>{new Date(task.updated_at).toLocaleString()}</li>
          </ul>
        </section>

        <TaskObservabilityPanel observability={task.progress?.observability || null} />

        <section>
          <h3 style={{ borderBottom: '1px solid #eee', paddingBottom: '0.5rem' }}>探索</h3>
          <div style={{ display: 'flex', gap: '1rem' }}>
            <Link to={`/tasks/${taskId}/sources`} style={buttonStyle}>来源</Link>
            <Link to={`/tasks/${taskId}/claims`} style={buttonStyle}>结论声明</Link>
            <Link to={`/tasks/${taskId}/report`} style={buttonStyle}>报告</Link>
          </div>
        </section>

        <section>
          <h3 style={{ borderBottom: '1px solid #eee', paddingBottom: '0.5rem' }}>事件日志</h3>
          <TaskEventList events={eventsData?.events || []} />
        </section>

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

const PipelineFailureHelp: React.FC<{ failure: PipelineFailure | null; dependencies: Record<string, any> | null }> = ({ failure, dependencies }) => {
  if (!failure) return null;

  const isSearxngHtmlResponse = failure.reason === 'searxng_html_response';
  const isPrecondition = failure.reason === 'pipeline_precondition_failed' && failure.failed_stage === 'PRECONDITION';
  if (!isSearxngHtmlResponse && !isPrecondition) return null;

  return (
    <section style={{ border: '1px solid #f3c27a', borderRadius: '8px', padding: '1rem', backgroundColor: '#fff8ed' }}>
      <h3 style={{ marginTop: 0 }}>运行失败处理</h3>
      {isSearxngHtmlResponse ? (
        <>
          <p style={{ marginTop: 0 }}>
            搜索阶段收到 HTML 页面，而不是 SearXNG JSON。常见原因是 `SEARXNG_BASE_URL` 指到了前端或普通网页服务。
          </p>
          <p style={{ margin: '0.5rem 0' }}>
            当前搜索配置: {dependencies?.search_provider || 'searxng'} / {dependencies?.searxng_base_url || '未记录'}
          </p>
          <div style={{ fontFamily: 'monospace', fontSize: '0.85rem', backgroundColor: '#fff', border: '1px solid #f3d6ad', borderRadius: '6px', padding: '0.75rem', overflowX: 'auto' }}>
            SEARCH_PROVIDER=smoke INDEX_BACKEND=local SNAPSHOT_STORAGE_BACKEND=filesystem ./dev.sh restart
          </div>
        </>
      ) : (
        <p style={{ margin: 0 }}>
          失败任务是审计记录，不能直接再次运行。请用相同查询新建任务，或从创建页提交一个新任务。
        </p>
      )}
      <p style={{ marginBottom: 0, color: '#7a4b00' }}>
        后端建议: {failure.next_action}
      </p>
    </section>
  );
};

type TaskObservability = NonNullable<NonNullable<ResearchTask['progress']>['observability']>;

const TaskObservabilityPanel: React.FC<{ observability: TaskObservability | null }> = ({ observability }) => {
  if (!observability) return null;

  const selectedSources = Array.isArray(observability.selected_sources) ? observability.selected_sources : [];
  const attemptedSources = Array.isArray(observability.attempted_sources) ? observability.attempted_sources : [];
  const unattemptedSources = Array.isArray(observability.unattempted_sources) ? observability.unattempted_sources : [];
  const failedSources = Array.isArray(observability.failed_sources) ? observability.failed_sources : [];
  const droppedSources = asObjectArray(observability.dropped_sources);
  const parseDecisions = Array.isArray(observability.parse_decisions) ? observability.parse_decisions : [];
  const warnings = Array.isArray(observability.warnings) ? observability.warnings : [];
  const qualitySummary = observability.source_quality_summary || null;
  const sourceYieldSummary = asObjectArray(observability.source_yield_summary);
  const slotCoverageSummary = asObjectArray(observability.slot_coverage_summary);
  const evidenceYieldSummary = observability.evidence_yield_summary || null;
  const verificationSummary = observability.verification_summary || null;
  const researchPlan = observability.research_plan || null;
  const rawPlannerQueries = asObjectArray(observability.raw_planner_queries);
  const finalSearchQueries = asObjectArray(observability.final_search_queries);
  const droppedPlannerQueries = asObjectArray(observability.dropped_or_downweighted_planner_queries);
  const guardrailWarnings = Array.isArray(observability.planner_guardrail_warnings) ? observability.planner_guardrail_warnings : [];
  const answerCoverage = observability.answer_coverage || null;
  const answerSlots = asObjectArray(observability.answer_slots || observability.report_slot_coverage);
  const answerYield = asObjectArray(observability.answer_yield);
  const supplementalAcquisition = observability.supplemental_acquisition || null;
  const failureDiagnostics = observability.failure_diagnostics || null;
  const planSubquestions = Array.isArray(researchPlan?.subquestions) ? researchPlan.subquestions : [];
  const planSearchQueries = Array.isArray(researchPlan?.search_queries) ? researchPlan.search_queries : [];
  const sourcePreferences = researchPlan?.source_preferences || {};
  const preferredDomains = Array.isArray(sourcePreferences.preferred_domains) ? sourcePreferences.preferred_domains : [];
  const avoidDomains = Array.isArray(sourcePreferences.avoid_domains) ? sourcePreferences.avoid_domains : [];
  const sourceRows = buildSourceSelectionRows({
    selectedSources,
    attemptedSources,
    unattemptedSources,
    failedSources,
  });

  return (
    <section style={{ border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1rem', backgroundColor: '#fbfdff' }}>
      <h3 style={{ marginTop: 0 }}>运行监控 (Observability)</h3>
      <ul style={{ listStyle: 'none', padding: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '0.5rem' }}>
        <li><strong>搜索结果:</strong><br />{observability.search_result_count ?? '无'}</li>
        <li><strong>获取成功:</strong><br />{observability.fetch_succeeded ?? '无'}</li>
        <li><strong>获取失败:</strong><br />{observability.fetch_failed ?? '无'}</li>
      </ul>
      {warnings.length > 0 && (
        <div style={{ marginTop: '0.75rem', color: '#7a4b00' }}>
          {warnings.map((warning: string) => (
            <div key={warning}><strong>警告:</strong> {warning}</div>
          ))}
        </div>
      )}
      <div style={{ marginTop: '0.75rem' }}>
        <strong>研究计划</strong>
        {researchPlan ? (
          <div style={{ marginTop: '0.5rem', display: 'grid', gap: '0.75rem' }}>
            <div>
              <span style={{ color: '#475569' }}>意图:</span>{' '}
              <span>{researchPlan.intent || '无'}</span>
              {observability.planner_mode ? (
                <span style={{ color: '#64748b' }}> / {observability.planner_mode}</span>
              ) : null}
            </div>
            {(observability.intent_classification || observability.extracted_entity) && (
              <div>
                <span style={{ color: '#475569' }}>护栏:</span>{' '}
                <span>{observability.intent_classification || '无'}</span>
                {observability.extracted_entity ? (
                  <span style={{ color: '#64748b' }}> / 实体 {observability.extracted_entity}</span>
                ) : null}
              </div>
            )}
            {planSubquestions.length > 0 && (
              <div>
                <div style={{ color: '#475569' }}>子问题</div>
                <ul style={{ marginTop: '0.35rem', paddingLeft: '1.25rem' }}>
                  {planSubquestions.map((item: string) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
            {planSearchQueries.length > 0 && (
              <div>
                <div style={{ color: '#475569' }}>搜索查询</div>
                <ul style={{ marginTop: '0.35rem', paddingLeft: '1.25rem' }}>
                  {planSearchQueries.map((item: any) => (
                    <li key={`${item.priority || ''}-${item.query_text || item}`}>
                      <span>{item.query_text || item}</span>
                      {item.expected_source_type ? (
                        <span style={{ color: '#64748b' }}> ({item.expected_source_type})</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {(preferredDomains.length > 0 || avoidDomains.length > 0) && (
              <div style={{ display: 'grid', gap: '0.35rem' }}>
                {preferredDomains.length > 0 && (
                  <div><span style={{ color: '#475569' }}>首选来源:</span> {preferredDomains.join(', ')}</div>
                )}
                {avoidDomains.length > 0 && (
                  <div><span style={{ color: '#475569' }}>避免来源:</span> {avoidDomains.join(', ')}</div>
                )}
              </div>
            )}
          </div>
        ) : (
          <p style={{ margin: '0.5rem 0 0', color: '#64748b' }}>未生成研究计划。</p>
        )}
      </div>
      {(finalSearchQueries.length > 0 || rawPlannerQueries.length > 0 || droppedPlannerQueries.length > 0 || guardrailWarnings.length > 0) && (
        <div style={{ marginTop: '0.75rem' }}>
          <strong>最终搜索查询</strong>
          {finalSearchQueries.length > 0 ? (
            <ol style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
              {finalSearchQueries.map((item: any, index: number) => (
                <li key={`${index}-${item.query_text || item.query || item}`}>
                  {item.query_text || item.query || String(item)}
                  {item.query_source ? <span style={{ color: '#64748b' }}> / {item.query_source}</span> : null}
                </li>
              ))}
            </ol>
          ) : (
            <p style={{ margin: '0.5rem 0 0', color: '#64748b' }}>没有记录到规划器搜索查询护栏数据。</p>
          )}
          {droppedPlannerQueries.length > 0 && (
            <div style={{ marginTop: '0.5rem' }}>
              <span style={{ color: '#475569' }}>已丢弃或降权:</span>{' '}
              {droppedPlannerQueries.map((item: any) => item.query_text || item.query || '规划器查询').join(', ')}
            </div>
          )}
          {guardrailWarnings.length > 0 && (
            <div style={{ marginTop: '0.5rem', color: '#7a4b00' }}>
              {guardrailWarnings.map((warning: string) => (
                <div key={warning}><strong>规划器护栏:</strong> {warning}</div>
              ))}
            </div>
          )}
        </div>
      )}
      {qualitySummary && (
        <div style={{ marginTop: '0.75rem' }}>
          <strong>来源质量</strong>
          <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
            <li>来源: {qualitySummary.source_count ?? '无'} / 高质量 {qualitySummary.high_quality_source_count ?? '无'}</li>
            <li>证据域名: {qualitySummary.evidence_domain_count ?? '无'}</li>
            <li>排除的分块: {qualitySummary.excluded_chunk_count ?? '无'}</li>
          </ul>
        </div>
      )}
      <SourceSelectionTable rows={sourceRows} />
      <SourceYieldSummaryPanel rows={sourceYieldSummary} />
      <DroppedSourcesPanel rows={droppedSources} />
      <AnswerSlotCoveragePanel rows={answerSlots} />
      <SlotCoverageSummaryPanel rows={slotCoverageSummary} />
      <EvidenceYieldSummaryPanel summary={evidenceYieldSummary} />
      <VerificationSummaryPanel summary={verificationSummary} />
      <AnswerYieldPanel rows={answerYield} />
      <AnswerCoveragePanel coverage={answerCoverage} />
      <SupplementalAcquisitionPanel supplemental={supplementalAcquisition} />
      <FailureDiagnosticsPanel diagnostics={failureDiagnostics} />
      {failedSources.length > 0 && (
        <div style={{ marginTop: '0.75rem' }}>
          <strong>获取失败</strong>
          <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
            {failedSources.slice(0, 5).map((source: any) => (
              <li key={source.fetch_attempt_id || source.canonical_url}>
                <a href={source.canonical_url} target="_blank" rel="noreferrer">{source.canonical_url}</a>
                {' '}状态 {source.http_status ?? '无'} / {source.error_code || source.error_reason || '未知'}
                {source.trace?.exception_type ? ` / ${source.trace.exception_type}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      {parseDecisions.length > 0 && (
        <div style={{ marginTop: '0.75rem' }}>
          <strong>解析决策</strong>
          <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
            {parseDecisions.slice(0, 5).map((decision: any) => (
              <li key={decision.snapshot_id || decision.content_snapshot_id}>
                <span style={{ fontFamily: 'monospace' }}>{decision.decision || '未知'}</span>
                {' '}关于 <a href={decision.canonical_url} target="_blank" rel="noreferrer">{decision.canonical_url || '未知 URL'}</a>
                {' '}({decision.mime_type || '未知 MIME'}, 正文 {decision.body_length ?? '无'} 字节)
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
};

const SourceSelectionTable: React.FC<{ rows: any[] }> = ({ rows }) => {
  if (rows.length === 0) return null;

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <strong>来源选择表</strong>
      <div style={{ marginTop: '0.5rem', overflowX: 'auto' }}>
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={thStyle}>域名</th>
              <th style={thStyle}>标题</th>
              <th style={thStyle}>类别</th>
              <th style={thStyle}>状态</th>
              <th style={thStyle}>原因</th>
              <th style={thStyle}>质量</th>
              <th style={thStyle}>最终 URL</th>
              <th style={thStyle}>来源产出</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 12).map((source: any) => (
              <tr key={source.row_key}>
                <td style={tdStyle}>{source.domain || '未知'}</td>
                <td style={tdStyle}>{source.title || '无'}</td>
                <td style={tdStyle}>{source.category || '无'}</td>
                <td style={tdStyle}>{source.state}</td>
                <td style={tdStyle}>{source.reason || source.downrank_reason || '无'}</td>
                <td style={tdStyle}>{formatOptionalNumber(source.quality)}</td>
                <td style={tdStyle}>
                  {source.final_url ? (
                    <a href={source.final_url} target="_blank" rel="noreferrer">{source.final_url}</a>
                  ) : '无'}
                </td>
                <td style={tdStyle}>{formatSourceYield(source.source_yield)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const AnswerYieldPanel: React.FC<{ rows: any[] }> = ({ rows }) => {
  if (rows.length === 0) return null;

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <strong>答案产出</strong>
      <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
        {rows.slice(0, 6).map((item: any) => (
          <li key={item.source_document_id || item.canonical_url}>
            {item.domain || domainFromUrl(item.canonical_url) || '未知'}:{' '}
            {formatSourceYield(item)}
          </li>
        ))}
      </ul>
    </div>
  );
};

const SourceYieldSummaryPanel: React.FC<{ rows: any[] }> = ({ rows }) => {
  if (rows.length === 0) return null;

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <strong>来源产出摘要</strong>
      <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
        {rows.slice(0, 8).map((source: any) => (
          <li key={source.source_document_id || source.candidate_url_id || source.canonical_url}>
            {source.domain || domainFromUrl(source.canonical_url) || '未知'}:{' '}
            {source.contribution_level || '无'} 贡献度, 结论声明 {source.claim_count ?? 0}, 证据 {source.accepted_evidence_count ?? 0}
            {Array.isArray(source.dropped_reasons) && source.dropped_reasons.length > 0 ? ` / ${source.dropped_reasons.join(', ')}` : ''}
          </li>
        ))}
      </ul>
    </div>
  );
};

const DroppedSourcesPanel: React.FC<{ rows: any[] }> = ({ rows }) => {
  if (rows.length === 0) return null;

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <strong>丢弃的来源</strong>
      <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
        {rows.slice(0, 6).map((source: any) => (
          <li key={source.source_document_id || source.candidate_url_id || source.canonical_url}>
            <a href={source.canonical_url || source.url} target="_blank" rel="noreferrer">
              {source.domain || domainFromUrl(source.canonical_url || source.url) || '来源'}
            </a>
            {' '}({Array.isArray(source.dropped_reasons) ? source.dropped_reasons.join(', ') : '未知'})
          </li>
        ))}
      </ul>
    </div>
  );
};

const SlotCoverageSummaryPanel: React.FC<{ rows: any[] }> = ({ rows }) => {
  if (rows.length === 0) return null;

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <strong>槽位质量摘要</strong>
      <ul style={{ listStyle: 'none', padding: 0, marginTop: '0.5rem', display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
        {rows.map((slot: any) => (
          <li key={slot.slot_id || slot.label} style={{ ...coveragePillStyle, backgroundColor: slot.status === 'covered' ? '#e8f5ed' : slot.status === 'weak' ? '#fffbe6' : '#fff2e8', color: slot.status === 'covered' ? '#166534' : slot.status === 'weak' ? '#854d0e' : '#9a3412' }}>
            {slot.label || slot.slot_id}: {slot.status === 'covered' ? '已覆盖' : slot.status === 'weak' ? '弱覆盖' : slot.status || '未知'} / 结论声明 {slot.supported_claim_count ?? 0} 强支持, {slot.weak_supported_claim_count ?? 0} 弱支持
          </li>
        ))}
      </ul>
    </div>
  );
};

const EvidenceYieldSummaryPanel: React.FC<{ summary: Record<string, any> | null }> = ({ summary }) => {
  if (!summary) return null;
  const topReasons = asObjectArray(summary.top_rejection_reasons);

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <strong>证据产出摘要</strong>
      <div style={{ marginTop: '0.5rem', color: '#475569' }}>
        候选 {summary.total_candidates ?? 0}, 已接受 {summary.accepted_candidates ?? 0}, 已拒绝 {summary.rejected_candidates ?? 0}
      </div>
      {topReasons.length > 0 && (
        <div style={{ marginTop: '0.35rem', color: '#64748b' }}>
          拒绝原因: {topReasons.slice(0, 4).map((item: any) => `${item.reason}: ${item.count}`).join(', ')}
        </div>
      )}
    </div>
  );
};

const VerificationSummaryPanel: React.FC<{ summary: Record<string, any> | null }> = ({ summary }) => {
  if (!summary) return null;
  const methods = Array.isArray(summary.verifier_methods) ? summary.verifier_methods : [];

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <strong>验证摘要</strong>
      <div style={{ marginTop: '0.5rem', color: '#475569' }}>
        强支持 {summary.strong_support_evidence_count ?? summary.strong_supported_claim_count ?? 0}, 弱支持 {summary.weak_support_evidence_count ?? summary.weak_supported_claim_count ?? 0}, 反驳 {summary.contradict_evidence_count ?? 0}
      </div>
      {methods.length > 0 && (
        <div style={{ marginTop: '0.35rem', color: '#64748b' }}>验证方法: {methods.join(', ')}</div>
      )}
    </div>
  );
};

const AnswerSlotCoveragePanel: React.FC<{ rows: any[] }> = ({ rows }) => {
  if (rows.length === 0) return null;

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <strong>答案槽位</strong>
      <ul style={{ listStyle: 'none', padding: 0, marginTop: '0.5rem', display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
        {rows.map((slot: any) => (
          <li key={slot.slot_id || slot.label} style={{ ...coveragePillStyle, backgroundColor: slot.covered ? '#e8f5ed' : '#fff2e8', color: slot.covered ? '#166534' : '#9a3412' }}>
            {slot.label || slot.slot_id}: {slot.covered ? '已覆盖' : '缺失'}
            {slot.required === false ? ' / 可选' : ''}
          </li>
        ))}
      </ul>
    </div>
  );
};

const AnswerCoveragePanel: React.FC<{ coverage: Record<string, boolean> | null }> = ({ coverage }) => {
  if (!coverage) return null;
  const categories = ['definition', 'mechanism', 'privacy', 'feature'];
  const categoryMap: Record<string, string> = {
    'definition': '定义',
    'mechanism': '机制',
    'privacy': '隐私',
    'feature': '特征'
  };

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <strong>答案覆盖率</strong>
      <ul style={{ listStyle: 'none', padding: 0, marginTop: '0.5rem', display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
        {categories.map((category) => (
          <li key={category} style={{ ...coveragePillStyle, backgroundColor: coverage[category] ? '#e8f5ed' : '#fff2e8', color: coverage[category] ? '#166534' : '#9a3412' }}>
            {categoryMap[category]}: {coverage[category] ? '已覆盖' : '缺失'}
          </li>
        ))}
      </ul>
    </div>
  );
};

const SupplementalAcquisitionPanel: React.FC<{ supplemental: Record<string, any> | null }> = ({ supplemental }) => {
  if (!supplemental) return null;
  const attempted = asObjectArray(supplemental.attempted_sources);
  const skipped = asObjectArray(supplemental.skipped_sources || supplemental.supplemental_sources_skipped);

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <strong>补充获取</strong>
      <div style={{ marginTop: '0.5rem', color: '#475569' }}>
        触发: {String(Boolean(supplemental.triggered))}
        {supplemental.reason ? ` / ${supplemental.reason}` : ''}
      </div>
      {attempted.length > 0 && (
        <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
          {attempted.slice(0, 5).map((source: any) => (
            <li key={source.candidate_url_id || source.canonical_url}>
              尝试 <a href={source.canonical_url} target="_blank" rel="noreferrer">{source.canonical_url}</a>
            </li>
          ))}
        </ul>
      )}
      {skipped.length > 0 && (
        <div style={{ marginTop: '0.35rem', color: '#64748b' }}>
          已跳过: {skipped.slice(0, 3).map((source: any) => source.skip_reason || source.canonical_url || '来源').join(', ')}
        </div>
      )}
    </div>
  );
};

const FailureDiagnosticsPanel: React.FC<{ diagnostics: Record<string, any> | null }> = ({ diagnostics }) => {
  if (!diagnostics) return null;
  const topRejected = asObjectArray(diagnostics.top_rejected_candidates);
  const unattemptedHighQuality = asObjectArray(diagnostics.unattempted_high_quality_sources);
  const whyReferenceNotAttempted = diagnostics.why_wikipedia_or_about_not_attempted;

  return (
    <div style={{ marginTop: '0.75rem', borderTop: '1px solid #e2e8f0', paddingTop: '0.75rem' }}>
      <strong>失败诊断</strong>
      {diagnostics.next_action && (
        <div style={{ marginTop: '0.5rem' }}><strong>下一步操作:</strong> {diagnostics.next_action}</div>
      )}
      {diagnostics.why_supplemental_acquisition_triggered && (
        <div><strong>补充获取触发原因:</strong> {diagnostics.why_supplemental_acquisition_triggered}</div>
      )}
      {whyReferenceNotAttempted && typeof whyReferenceNotAttempted === 'object' && (
        <div style={{ marginTop: '0.5rem' }}>
          <strong>关于/维基百科:</strong>{' '}
          {Object.entries(whyReferenceNotAttempted).map(([key, value]) => `${key}: ${String(value)}`).join('; ')}
        </div>
      )}
      {unattemptedHighQuality.length > 0 && (
        <div style={{ marginTop: '0.5rem' }}>
          <strong>未尝试的高质量来源:</strong>{' '}
          {unattemptedHighQuality.slice(0, 4).map((source: any) => source.canonical_url || source.domain || '来源').join(', ')}
        </div>
      )}
      {topRejected.length > 0 && (
        <ul style={{ marginTop: '0.5rem', paddingLeft: '1.25rem' }}>
          {topRejected.slice(0, 5).map((item: any, index: number) => (
            <li key={`${index}-${item.statement || item.text || item.rejected_reason}`}>
              {item.rejected_reason || item.reason || '已拒绝'}: {item.statement || item.text || item.excerpt || '候选'}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

const asObjectArray = (value: unknown): Array<Record<string, any>> => {
  return Array.isArray(value) ? value.filter((item): item is Record<string, any> => item && typeof item === 'object') : [];
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
  const sourceYield = source.source_yield || null;
  return {
    ...source,
    final_url: finalUrl,
    domain: source.domain || domainFromUrl(finalUrl),
    category: source.source_category || source.source_intent || source.category || source.metadata?.source_category,
    reason: source.source_selection_reason || source.selected_reason || source.fetch_priority_reason || source.reason,
    downrank_reason: source.downrank_reason,
    quality: source.source_quality_score || source.final_source_score || source.quality_score,
    source_yield: sourceYield,
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
  if (typeof value !== 'number') return '无';
  return value.toFixed(value > 1 ? 0 : 2);
};

const formatSourceYield = (value: any): string => {
  if (!value || typeof value !== 'object') return '无';
  const chunks = value.chunk_count ?? '无';
  const eligible = value.eligible_chunk_count ?? '无';
  const candidates = value.candidate_sentence_count ?? '无';
  const answerRelevant = value.answer_relevant_candidate_count ?? '无';
  const accepted = value.accepted_claim_candidate_count ?? '无';
  const lowYield = value.low_yield_reason ? ` / ${value.low_yield_reason}` : '';
  return `分块 ${chunks}, 符合条件 ${eligible}, 句子 ${candidates}, 答案相关 ${answerRelevant}, 已接受 ${accepted}${lowYield}`;
};

const PipelineResultPanel: React.FC<{ result: PipelineRunResponse | null }> = ({ result }) => {
  if (!result) return null;

  return (
    <section style={{ border: '1px solid #ddd', borderRadius: '8px', padding: '1rem', backgroundColor: result.completed ? '#f1fff4' : '#fff8f1' }}>
      <h3 style={{ marginTop: 0 }}>流程执行结果</h3>
      <p style={{ marginTop: 0 }}>
        <strong>模式:</strong> {result.running_mode}
      </p>
      <CountsGrid counts={result.counts} />
      {result.failure && (
        <div style={{ marginTop: '1rem', borderTop: '1px solid #e0d0c0', paddingTop: '1rem' }}>
          <div><strong>失败阶段:</strong> {result.failure.failed_stage}</div>
          <div><strong>原因:</strong> {result.failure.reason}</div>
          <div><strong>消息:</strong> {result.failure.message}</div>
          <div><strong>下一步操作:</strong> {result.failure.next_action}</div>
          <FailureDiagnosticsPanel diagnostics={result.failure.details || null} />
        </div>
      )}
    </section>
  );
};

const CountsGrid: React.FC<{ counts: PipelineCounts }> = ({ counts }) => (
  <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0.5rem' }}>
    {Object.entries(counts).map(([key, value]) => (
      <li key={key} style={{ backgroundColor: '#fff', border: '1px solid #eee', borderRadius: '4px', padding: '0.5rem' }}>
        <strong>{key}:</strong> {value}
      </li>
    ))}
  </ul>
);

const TaskEventList: React.FC<{ events: TaskEvent[] }> = ({ events }) => {
  if (events.length === 0) {
    return <p style={{ color: '#777' }}>没有记录事件。</p>;
  }

  return (
    <ol style={{ margin: 0, paddingLeft: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {events.slice().reverse().slice(0, 12).map((event) => (
        <li key={event.event_id}>
          <div><strong>{event.event_type}</strong> #{event.sequence_no}</div>
          <div style={{ color: '#666', fontSize: '0.875rem' }}>
            {new Date(event.created_at).toLocaleString()}
            {event.payload?.stage ? ` | ${event.payload.stage}` : ''}
            {event.payload?.reason ? ` | ${event.payload.reason}` : ''}
          </div>
        </li>
      ))}
    </ol>
  );
};

const buttonStyle = {
  display: 'inline-block',
  padding: '0.5rem 1rem',
  backgroundColor: '#0066cc',
  color: 'white',
  textDecoration: 'none',
  borderRadius: '4px',
  fontWeight: 'bold',
};

const tableStyle: React.CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
  fontSize: '0.875rem',
};

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  borderBottom: '1px solid #cbd5e1',
  padding: '0.45rem',
  color: '#334155',
  whiteSpace: 'nowrap',
};

const tdStyle: React.CSSProperties = {
  borderBottom: '1px solid #e2e8f0',
  padding: '0.45rem',
  verticalAlign: 'top',
  maxWidth: '20rem',
  overflowWrap: 'anywhere',
};

const coveragePillStyle: React.CSSProperties = {
  borderRadius: '999px',
  padding: '0.2rem 0.55rem',
  border: '1px solid #e2e8f0',
};
