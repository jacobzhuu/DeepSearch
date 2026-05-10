import React from 'react';

export const EmptyState: React.FC<{ message?: string }> = ({ message = '暂无相关研究数据' }) => {
  return (
    <div 
      style={{ 
        padding: '4rem 2rem', 
        textAlign: 'center', 
        color: 'var(--text-secondary)', 
        border: '2px dashed var(--border-color)', 
        borderRadius: 'var(--radius-md)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: '1rem'
      }}
    >
      <div style={{ fontSize: '2.5rem', opacity: 0.5 }}>📊</div>
      <p style={{ margin: 0, fontWeight: 500 }}>{message}</p>
    </div>
  );
};
