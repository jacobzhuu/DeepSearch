import React from 'react';

interface ErrorStateProps {
  error: Error | string | null;
  onRetry?: () => void;
}

export const ErrorState: React.FC<ErrorStateProps> = ({ error, onRetry }) => {
  if (!error) return null;
  
  const errorMessage = error instanceof Error ? error.message : error;

  return (
    <div style={{ padding: '2rem', border: '1px solid red', borderRadius: '4px', color: 'red', margin: '1rem 0' }}>
      <h3>Something went wrong</h3>
      <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>{errorMessage}</pre>
      {onRetry && (
        <button onClick={onRetry} style={{ marginTop: '1rem' }}>
          Try Again
        </button>
      )}
    </div>
  );
};
