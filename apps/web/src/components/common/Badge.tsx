import React from 'react';

interface BadgeProps {
  children: React.ReactNode;
  variant?: 'default' | 'primary' | 'secondary' | 'success' | 'warning' | 'error' | 'info';
  style?: React.CSSProperties;
}

export const Badge: React.FC<BadgeProps> = ({ children, variant = 'default', style }) => {
  const getVariantStyles = (): React.CSSProperties => {
    switch (variant) {
      case 'primary':
        return { backgroundColor: '#e8f0fe', color: '#1a73e8' };
      case 'secondary':
        return { backgroundColor: '#f1f3f4', color: '#3c4043' };
      case 'success':
        return { backgroundColor: '#e6f4ea', color: '#1e8e3e' };
      case 'warning':
        return { backgroundColor: '#fef7e0', color: '#f9ab00' };
      case 'error':
        return { backgroundColor: '#fce8e6', color: '#d93025' };
      case 'info':
        return { backgroundColor: '#f1f3f4', color: '#5f6368' };
      default:
        return { backgroundColor: '#f1f3f4', color: '#5f6368' };
    }
  };

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '0.25rem 0.75rem',
        borderRadius: '999px',
        fontSize: '0.75rem',
        fontWeight: 600,
        ...getVariantStyles(),
        ...style,
      }}
    >
      {children}
    </span>
  );
};
