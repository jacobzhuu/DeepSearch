import React from 'react';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  isLoading?: boolean;
}

export const Button: React.FC<ButtonProps> = ({ 
  children, 
  variant = 'primary', 
  size = 'md', 
  isLoading, 
  style, 
  disabled,
  ...props 
}) => {
  const getVariantStyles = (): React.CSSProperties => {
    switch (variant) {
      case 'primary':
        return { backgroundColor: 'var(--primary-color)', color: 'white', border: 'none' };
      case 'secondary':
        return { backgroundColor: 'var(--primary-container)', color: 'var(--primary-color)', border: 'none' };
      case 'outline':
        return { backgroundColor: 'transparent', color: 'var(--primary-color)', border: '1px solid var(--primary-color)' };
      case 'ghost':
        return { backgroundColor: 'transparent', color: 'var(--text-secondary)', border: 'none' };
      case 'danger':
        return { backgroundColor: '#fce8e6', color: '#d93025', border: '1px solid #fce8e6' };
      default:
        return { backgroundColor: 'var(--primary-color)', color: 'white', border: 'none' };
    }
  };

  const getSizeStyles = (): React.CSSProperties => {
    switch (size) {
      case 'sm':
        return { padding: '0.35rem 0.75rem', fontSize: '0.875rem' };
      case 'lg':
        return { padding: '0.85rem 1.75rem', fontSize: '1.125rem' };
      default:
        return { padding: '0.65rem 1.25rem', fontSize: '1rem' };
    }
  };

  return (
    <button
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        borderRadius: 'var(--radius-sm)',
        fontWeight: 600,
        cursor: disabled || isLoading ? 'not-allowed' : 'pointer',
        opacity: disabled || isLoading ? 0.6 : 1,
        transition: 'all 0.2s ease',
        gap: '0.5rem',
        ...getVariantStyles(),
        ...getSizeStyles(),
        ...style,
      }}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading && (
        <div 
          style={{ 
            width: '16px', 
            height: '16px', 
            border: '2px solid rgba(255,255,255,0.3)', 
            borderTop: '2px solid white', 
            borderRadius: '50%',
            animation: 'spin 0.8s linear infinite'
          }} 
        />
      )}
      {children}
    </button>
  );
};
