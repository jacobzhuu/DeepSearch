import React from 'react';

export const LoadingState: React.FC<{ message?: string }> = ({ message = '正在加载研究数据...' }) => {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '4rem 2rem', gap: '1.5rem' }}>
      <div 
        style={{ 
          width: '48px', 
          height: '48px', 
          border: '4px solid var(--primary-container)', 
          borderTop: '4px solid var(--primary-color)', 
          borderRadius: '50%',
          animation: 'spin 1s linear infinite',
        }} 
      />
      <div style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>{message}</div>
      <style>{`
        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
};
