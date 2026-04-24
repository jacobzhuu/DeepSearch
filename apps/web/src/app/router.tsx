import { createBrowserRouter, Navigate } from 'react-router-dom';
import { NewTaskPage } from '../pages/tasks/NewTaskPage';
import { TaskDetailPage } from '../pages/tasks/TaskDetailPage';
import { TaskSourcesPage } from '../pages/tasks/TaskSourcesPage';
import { TaskClaimsPage } from '../pages/tasks/TaskClaimsPage';
import { TaskReportPage } from '../pages/tasks/TaskReportPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Navigate to="/tasks/new" replace />,
  },
  {
    path: '/tasks/new',
    element: <NewTaskPage />,
  },
  {
    path: '/tasks/:taskId',
    element: <TaskDetailPage />,
  },
  {
    path: '/tasks/:taskId/sources',
    element: <TaskSourcesPage />,
  },
  {
    path: '/tasks/:taskId/claims',
    element: <TaskClaimsPage />,
  },
  {
    path: '/tasks/:taskId/report',
    element: <TaskReportPage />,
  },
]);
