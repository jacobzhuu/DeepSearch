import { env } from './env';

export class ApiError extends Error {
  status: number;
  detail: string;
  rawDetail: unknown;

  constructor(status: number, detail: string, rawDetail: unknown = detail) {
    super(`API error (${status}): ${detail}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.rawDetail = rawDetail;
  }
}

export async function fetchApi<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const url = `${env.API_BASE_URL}${endpoint}`;
  
  const headers = new Headers(options.headers);
  if (!headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let errorMessage = response.statusText || 'Request failed';
    let rawDetail: unknown = errorMessage;
    const responseText = await response.text();
    try {
      const errorData = JSON.parse(responseText);
      rawDetail = errorData.detail ?? errorData;
      errorMessage = formatErrorDetail(rawDetail);
    } catch {
      if (responseText) {
        errorMessage = responseText;
        rawDetail = responseText;
      }
    }
    throw new ApiError(response.status, errorMessage, rawDetail);
  }

  return response.json();
}

function formatErrorDetail(detail: unknown): string {
  if (typeof detail === 'string') {
    return detail;
  }
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>;
    const message = typeof record.message === 'string' ? record.message : null;
    const reason = typeof record.reason === 'string' ? record.reason : null;
    const nextAction = typeof record.next_action === 'string' ? record.next_action : null;
    if (message) {
      return [
        message,
        reason ? `Reason: ${reason}` : null,
        nextAction ? `Next action: ${nextAction}` : null,
      ].filter(Boolean).join('\n');
    }
    return JSON.stringify(detail, null, 2);
  }
  return String(detail);
}
