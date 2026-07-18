export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface Conversation {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | string;
  content: string;
  run_id: string | null;
  created_at: string;
}

export interface RunAccepted {
  run_id: string;
  conversation_id: string;
  status: RunStatus;
}

export interface Run {
  id: string;
  conversation_id: string;
  status: RunStatus;
  skill_id: string | null;
  answer: string;
  error_code: string | null;
  steps: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  metadata: Record<string, unknown>;
}

export interface RunEvent {
  run_id: string;
  sequence: number;
  type: string;
  data: Record<string, unknown>;
}

export interface ApiErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
  };
}
