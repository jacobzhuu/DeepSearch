import React from 'react';

interface MetricCardProps {
  label: string;
  value: number | string | null | undefined;
  icon?: React.ReactNode;
  unit?: string;
}

export const MetricCard: React.FC<MetricCardProps> = ({ label, value, icon, unit }) => {
  return (
    <div className="card-solid" style={{ padding: '1rem', display: 'flex', flexDirection: 'column', gap: '0.25rem', minWidth: '120px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)', fontSize: '0.75rem', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {icon}
        {label}
      </div>
      <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--text-primary)' }}>
        {value ?? 0}
        {unit && <span style={{ fontSize: '0.875rem', fontWeight: 400, marginLeft: '0.25rem', color: 'var(--text-secondary)' }}>{unit}</span>}
      </div>
    </div>
  );
};
