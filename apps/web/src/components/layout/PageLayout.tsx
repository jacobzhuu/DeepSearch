import React, { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';

interface PageLayoutProps {
  title?: string;
  children: ReactNode;
  actions?: ReactNode;
  maxWidth?: string;
}

export const PageLayout: React.FC<PageLayoutProps> = ({ title, children, actions, maxWidth = '1000px' }) => {
  const location = useLocation();

  const isNavLinkActive = (path: string) => {
    return location.pathname === path || (path !== '/tasks' && location.pathname.startsWith(path));
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header 
        style={{ 
          backgroundColor: 'var(--surface-color)', 
          backdropFilter: 'blur(12px)',
          borderBottom: '1px solid var(--border-color)',
          position: 'sticky',
          top: 0,
          zIndex: 10,
          padding: '0.75rem 1.5rem',
        }}
      >
        <div style={{ maxWidth: '1200px', margin: '0 auto', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '2rem' }}>
            <Link to="/tasks" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', textDecoration: 'none' }}>
              <div style={{ 
                width: '32px', 
                height: '32px', 
                borderRadius: '8px', 
                background: 'var(--accent-gradient)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'white',
                fontWeight: 800,
                fontSize: '1.2rem'
              }}>D</div>
              <span style={{ fontWeight: 700, fontSize: '1.25rem', color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>开源情报收集与溯源系统</span>
            </Link>
            
            <nav style={{ display: 'flex', gap: '1rem' }}>
              <Link 
                to="/tasks" 
                style={{ 
                  padding: '0.5rem 0.75rem', 
                  borderRadius: 'var(--radius-sm)',
                  color: isNavLinkActive('/tasks') && !location.pathname.includes('/new') ? 'var(--primary-color)' : 'var(--text-secondary)',
                  fontWeight: isNavLinkActive('/tasks') && !location.pathname.includes('/new') ? 600 : 500,
                  backgroundColor: isNavLinkActive('/tasks') && !location.pathname.includes('/new') ? 'var(--primary-container)' : 'transparent',
                }}
              >
                任务列表
              </Link>
              <Link 
                to="/tasks/new" 
                style={{ 
                  padding: '0.5rem 0.75rem', 
                  borderRadius: 'var(--radius-sm)',
                  color: location.pathname === '/tasks/new' ? 'var(--primary-color)' : 'var(--text-secondary)',
                  fontWeight: location.pathname === '/tasks/new' ? 600 : 500,
                  backgroundColor: location.pathname === '/tasks/new' ? 'var(--primary-container)' : 'transparent',
                }}
              >
                新建任务
              </Link>
            </nav>
          </div>
          
          {actions && <div>{actions}</div>}
        </div>
      </header>
      
      <main style={{ flex: 1, padding: '2rem 1.5rem' }}>
        <div style={{ maxWidth, margin: '0 auto' }}>
          {title && (
            <div style={{ marginBottom: '2rem', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
              <h1 style={{ fontSize: '2rem', fontWeight: 700, margin: 0, letterSpacing: '-0.02em' }}>{title}</h1>
            </div>
          )}
          {children}
        </div>
      </main>
      
      <footer style={{ padding: '2rem 1.5rem', borderTop: '1px solid var(--border-color)', backgroundColor: 'white' }}>
        <div style={{ maxWidth: '1200px', margin: '0 auto', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
          <p>© 2026 开源情报收集与溯源系统</p>
          <div style={{ marginTop: '0.5rem', display: 'flex', justifyContent: 'center', gap: '1rem' }}>
            <Link to="/tasks">任务</Link>
            <Link to="/tasks/new">创建</Link>
          </div>
        </div>
      </footer>
    </div>
  );
};
