import React from 'react';
import { Badge } from './Badge';
import { ResearchTask } from '../../types/api';

interface StatusBadgeProps {
  status: ResearchTask['status'];
}

const STATUS_MAP: Record<string, string> = {
  'PLANNED': '已规划',
  'PAUSED': '已暂停',
  'CANCELLED': '已取消',
  'QUEUED': '排队中',
  'RUNNING': '运行中',
  'SEARCHING': '搜索中',
  'ACQUIRING': '获取中',
  'PARSING': '解析中',
  'INDEXING': '索引中',
  'DRAFTING_CLAIMS': '起草中',
  'VERIFYING': '验证中',
  'RESEARCHING_MORE': '深度追问',
  'REPORTING': '报告中',
  'FAILED': '已失败',
  'COMPLETED': '已完成',
  'NEEDS_REVISION': '待修改',
};

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  const getVariant = (): 'primary' | 'success' | 'warning' | 'error' | 'info' | 'default' => {
    switch (status) {
      case 'COMPLETED':
        return 'success';
      case 'FAILED':
        return 'error';
      case 'RUNNING':
      case 'SEARCHING':
      case 'ACQUIRING':
      case 'PARSING':
      case 'INDEXING':
      case 'DRAFTING_CLAIMS':
      case 'VERIFYING':
      case 'REPORTING':
      case 'RESEARCHING_MORE':
        return 'primary';
      case 'PAUSED':
      case 'NEEDS_REVISION':
        return 'warning';
      case 'QUEUED':
      case 'PLANNED':
        return 'info';
      case 'CANCELLED':
        return 'default';
      default:
        return 'default';
    }
  };

  return <Badge variant={getVariant()}>{STATUS_MAP[status] || status}</Badge>;
};
