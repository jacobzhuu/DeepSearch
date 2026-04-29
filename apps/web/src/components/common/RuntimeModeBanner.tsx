import React from 'react';

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
  const isSmoke = searchProvider === 'smoke' || String(runningMode || '').includes('smoke-search');
  const isLocalIndex = indexMode === 'deterministic-local' || indexMode === 'local';
  const isNoLlm = llmMode === 'no-LLM' || String(runningMode || '').includes('no-LLM');
  const severity = isSmoke || isLocalIndex ? 'warning' : 'info';
  const messages = [
    ...warnings,
    ...(isSmoke ? ['当前为开发 smoke 模式：搜索来源是 deepsearch-smoke.local 合成夹具，不是真实网页证据。'] : []),
    ...(isLocalIndex ? ['当前为本地 deterministic index：适合连通性验证，不代表持久化真实检索质量。'] : []),
    ...(isNoLlm ? ['当前没有启用 LLM planner：研究计划来自确定性 fallback，不是模型规划。'] : []),
  ];
  const dedupedMessages = Array.from(new Set(messages.filter(Boolean)));

  return (
    <section style={severity === 'warning' ? warningStyle : infoStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap' }}>
        <strong>{severity === 'warning' ? '运行模式: 连通性测试' : '运行模式: 研究流程'}</strong>
        <span style={{ fontFamily: 'monospace' }}>{runningMode || '未记录'}</span>
      </div>
      {dedupedMessages.length > 0 && (
        <ul style={{ margin: '0.5rem 0 0', paddingLeft: '1.25rem' }}>
          {dedupedMessages.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}
    </section>
  );
};

const warningStyle = {
  border: '1px solid #f59e0b',
  borderRadius: '8px',
  padding: '1rem',
  backgroundColor: '#fff7ed',
  color: '#7c2d12',
};

const infoStyle = {
  border: '1px solid #93c5fd',
  borderRadius: '8px',
  padding: '1rem',
  backgroundColor: '#eff6ff',
  color: '#1e3a8a',
};
