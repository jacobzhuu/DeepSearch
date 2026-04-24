import React, { ReactNode } from 'react';
import { Link } from 'react-router-dom';

interface PageLayoutProps {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}

export const PageLayout: React.FC<PageLayoutProps> = ({ title, children, actions }) => {
  return (
    <div style={{ maxWidth: '800px', margin: '0 auto', padding: '2rem 1rem' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem' }}>
        <h1 style={{ margin: 0 }}>{title}</h1>
        {actions && <div>{actions}</div>}
      </header>
      
      <main>
        {children}
      </main>
      
      <footer style={{ marginTop: '3rem', paddingTop: '1rem', borderTop: '1px solid #eee', textAlign: 'center', color: '#999' }}>
        <p><Link to="/tasks/new">Create New Task</Link> | DeepSearch Operator UI</p>
      </footer>
    </div>
  );
};
