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
      errorMessage = typeof rawDetail === 'string' ? rawDetail : JSON.stringify(rawDetail, null, 2);
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
