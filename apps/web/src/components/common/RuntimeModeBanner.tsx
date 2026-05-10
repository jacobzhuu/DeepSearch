import React from 'react';
import { Badge } from './Badge';

type RuntimeModeBannerProps = {
  runningMode?: string | null;
  dependencies?: Record<string, any> | null;
  warnings?: string[];
};

export const RuntimeModeBanner: React.FC<RuntimeModeBannerProps> = ({
  runningMode,
  dependencies,
  warnings = [],
}) => {
  if (!runningMode && !dependencies) return null;

  const searchProvider = dependencies?.search_provider;
  const indexMode = dependencies?.index_mode || dependencies?.index_backend;
  const llmMode = dependencies?.llm_mode;
  const sourceJudgeEnabled = dependencies?.llm_source_judge_enabled;
  const sourceTriageActive = dependencies?.llm_source_triage_active;
  
  const isSmoke = searchProvider === 'smoke' || String(runningMode || '').includes('smoke-search');
  const isLocalIndex = indexMode === 'deterministic-local' || indexMode === 'local';
  const isNoLlm = llmMode === 'no-LLM' || String(runningMode || '').includes('no-LLM');
  
  const messages = [
    ...warnings,
    ...(isSmoke ? ['当前为开发 smoke 模式：搜索来源是 deepsearch-smoke.local 合成夹具，不是真实网页证据。'] : []),
    ...(isLocalIndex ? ['当前为本地 deterministic index：适合连通性验证，不代表持久化真实检索质量。'] : []),
    ...(isNoLlm ? ['当前没有启用 LLM planner：研究计划来自确定性 fallback，不是模型规划。'] : []),
  ];
  const dedupedMessages = Array.from(new Set(messages.filter(Boolean)));

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1rem' }}>
      {runningMode && <Badge variant="info">模式: {runningMode}</Badge>}
      {searchProvider && <Badge variant={isSmoke ? 'warning' : 'success'}>搜索: {searchProvider}</Badge>}
      {indexMode && <Badge variant={isLocalIndex ? 'warning' : 'success'}>索引: {indexMode}</Badge>}
      {llmMode && <Badge variant={isNoLlm ? 'warning' : 'success'}>LLM: {llmMode}</Badge>}
      {sourceJudgeEnabled && <Badge variant="success">来源评价: 开启</Badge>}
      {sourceTriageActive && <Badge variant="success">深度分流: 开启</Badge>}
      {dedupedMessages.map((msg, i) => (
        <Badge key={i} variant="warning">{msg}</Badge>
      ))}
    </div>
  );
};
