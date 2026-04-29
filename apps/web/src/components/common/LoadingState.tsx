import React from 'react';

export const LoadingState: React.FC<{ message?: string }> = ({ message = '加载中...' }) => {
  return (
    <div style={{ padding: '2rem', textAlign: 'center', color: '#666' }}>
      <p>{message}</p>
    </div>
  );
};
