const API_TIMEZONE_PATTERN = /(Z|[+-]\d{2}:?\d{2})$/;

export const CHINA_TIME_ZONE = 'Asia/Shanghai';

export const parseApiDate = (value: string | null | undefined): Date | null => {
  if (!value) return null;
  const normalized = API_TIMEZONE_PATTERN.test(value) ? value : `${value}Z`;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

export const formatChinaDateTime = (
  value: string | null | undefined,
  options: Intl.DateTimeFormatOptions = {},
): string => {
  const parsed = parseApiDate(value);
  if (!parsed) return '-';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: CHINA_TIME_ZONE,
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    ...options,
  }).format(parsed);
};

export const formatChinaTime = (value: string | null | undefined): string =>
  formatChinaDateTime(value, {
    year: undefined,
    month: undefined,
    day: undefined,
  });
