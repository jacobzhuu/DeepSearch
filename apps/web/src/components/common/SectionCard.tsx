import React from 'react';

interface SectionCardProps {
  title?: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
  style?: React.CSSProperties;
}

export const SectionCard: React.FC<SectionCardProps> = ({ title, children, actions, style }) => {
  return (
    <section className="card" style={{ marginBottom: '1.5rem', ...style }}>
      {(title || actions) && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem' }}>
          {title && <h2 style={{ fontSize: '1.25rem', fontWeight: 600, margin: 0 }}>{title}</h2>}
          {actions && <div>{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
};
