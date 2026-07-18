import type {
  ApiErrorEnvelope,
  Conversation,
  Message,
  Run,
  RunAccepted,
  RunEvent,
} from './types';

const API_BASE_URL = (import.meta.env.VITE_NINO_API_BASE_URL ?? '').replace(/\/$/, '');

export class NinoApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'NinoApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let body: ApiErrorEnvelope | undefined;
    try {
      body = (await response.json()) as ApiErrorEnvelope;
    } catch {
      body = undefined;
    }
    throw new NinoApiError(
      body?.error?.message || `请求失败 (${response.status})`,
      body?.error?.code || 'HTTP_ERROR',
      response.status,
    );
  }

  return response.json() as Promise<T>;
}

export function createConversation(title?: string): Promise<Conversation> {
  return request('/api/v1/conversations', {
    method: 'POST',
    body: JSON.stringify(title ? { title } : {}),
  });
}

export function listMessages(conversationId: string): Promise<Message[]> {
  return request(`/api/v1/conversations/${conversationId}/messages`);
}

export function listRuns(conversationId: string): Promise<Run[]> {
  return request(`/api/v1/conversations/${conversationId}/runs`);
}

export function getRun(runId: string): Promise<Run> {
  return request(`/api/v1/runs/${runId}`);
}

export function cancelRun(runId: string): Promise<Run> {
  return request(`/api/v1/runs/${runId}/cancel`, { method: 'POST' });
}

interface StreamRunOptions {
  signal: AbortSignal;
  onEvent: (event: RunEvent) => void;
  onAccepted?: (accepted: RunAccepted) => void;
}

async function consumeRunStream(
  response: Response,
  { onEvent, onAccepted }: StreamRunOptions,
): Promise<Run> {
  if (!response.ok || !response.body) {
    throw new NinoApiError(`无法建立事件流 (${response.status})`, 'SSE_CONNECTION_FAILED', response.status);
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  let result: Run | null = null;

  while (true) {
    const { value, done } = await reader.read();
    buffer += value ?? '';
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? '';

    for (const block of blocks) {
      const lines = block.split(/\r?\n/);
      const eventType = lines.find((line) => line.startsWith('event:'))?.slice(6).trim();
      const data = lines
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n');

      if (data) {
        if (eventType === 'run_accepted') {
          onAccepted?.(JSON.parse(data) as RunAccepted);
        } else if (eventType === 'run_result') {
          result = JSON.parse(data) as Run;
        } else {
          onEvent(JSON.parse(data) as RunEvent);
        }
      }
    }

    if (done) {
      if (result) return result;
      throw new NinoApiError('事件流在任务完成前断开', 'SSE_STREAM_ENDED', 0);
    }
  }
}

export async function streamMessage(
  conversationId: string,
  content: string,
  options: StreamRunOptions,
): Promise<Run> {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/conversations/${conversationId}/messages/stream`,
    {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ content }),
      signal: options.signal,
    },
  );

  const runId = response.headers.get('X-Run-ID');
  if (runId) {
    options.onAccepted?.({ run_id: runId, conversation_id: conversationId, status: 'queued' });
  }
  return consumeRunStream(response, options);
}

export async function streamRunEvents(
  runId: string,
  options: StreamRunOptions,
): Promise<Run> {
  const response = await fetch(`${API_BASE_URL}/api/v1/runs/${runId}/events/stream`, {
    headers: { Accept: 'text/event-stream' },
    signal: options.signal,
  });
  return consumeRunStream(response, options);
}
