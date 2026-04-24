import React from 'react';

export const EmptyState: React.FC<{ message?: string }> = ({ message = 'No data available' }) => {
  return (
    <div style={{ padding: '3rem', textAlign: 'center', color: '#888', border: '1px dashed #ccc', borderRadius: '4px' }}>
      <p>{message}</p>
    </div>
  );
};
