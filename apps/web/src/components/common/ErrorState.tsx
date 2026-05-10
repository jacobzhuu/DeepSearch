import React from 'react';

interface ErrorStateProps {
  error: Error | string | null;
  onRetry?: () => void;
}

export const ErrorState: React.FC<ErrorStateProps> = ({ error, onRetry }) => {
  if (!error) return null;
  
  const errorMessage = error instanceof Error ? error.message : error;

  return (
    <div 
      className="card-solid" 
      style={{ 
        padding: '2rem', 
        border: '1px solid #fce8e6', 
        backgroundColor: '#fffbfa',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: '1rem',
        textAlign: 'center'
      }}
    >
      <div style={{ color: '#d93025', fontSize: '2rem' }}>⚠️</div>
      <h3 style={{ color: '#d93025', margin: 0 }}>研究过程中出现错误</h3>
      <div style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', maxWidth: '500px' }}>
        {errorMessage}
      </div>
      {onRetry && (
        <button 
          onClick={onRetry} 
          style={{ 
            marginTop: '0.5rem',
            padding: '0.5rem 1.5rem',
            backgroundColor: 'var(--primary-color)',
            color: 'white',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            fontWeight: 600,
          }}
        >
          重新尝试
        </button>
      )}
    </div>
  );
};
