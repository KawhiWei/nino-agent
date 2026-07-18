import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  AIChatDialogue,
  AIChatInput,
  Button,
  Toast,
  Tooltip,
  Typography,
} from '@douyinfe/semi-ui';
import {
  IconAlertCircle,
  IconChevronDown,
  IconChevronUp,
  IconClock,
  IconLoading,
  IconPlus,
  IconPulse,
  IconTickCircle,
} from '@douyinfe/semi-icons';
import type { Message as DialogueMessage } from '@douyinfe/semi-ui/lib/es/aiChatDialogue/interface';
import type { MessageContent } from '@douyinfe/semi-ui/lib/es/aiChatInput/interface';
import {
  NinoApiError,
  cancelRun,
  createConversation,
  listMessages,
  listRuns,
  streamMessage,
  streamRunEvents,
} from './api/client';
import type { Message as ApiMessage, Run, RunEvent } from './api/types';

const CONVERSATION_STORAGE_KEY = 'nino-agent.conversation-id';

const roleConfig = {
  user: { name: '你', color: '#1d5f52' },
  assistant: { name: 'Nino', color: '#d85b3f' },
};

const suggestions = [
  '查询订单 DEMO-202607-001 的收入、成本和毛利',
  '统计 2026 年 7 月各业务线毛利',
  '找出本月毛利异常的订单并说明原因',
];

type ProgressStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
type ProgressItemStatus = 'active' | 'completed' | 'failed';

interface ProgressItem {
  key: string;
  title: string;
  detail: string;
  status: ProgressItemStatus;
  sequence: number;
}

interface RunProgress {
  runId: string;
  status: ProgressStatus;
  headline: string;
  startedAt: number;
  finishedAt?: number;
  items: ProgressItem[];
}

interface ProgressUpdate {
  key: string;
  title: string;
  detail: string;
  status: ProgressItemStatus;
  headline?: string;
}

function toDialogueMessage(message: ApiMessage): DialogueMessage {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    createdAt: new Date(message.created_at).getTime(),
    status: 'completed',
  };
}

function textFromInput(message: MessageContent): string {
  return (message.inputContents ?? [])
    .map((item) => {
      if (typeof item.text === 'string') return item.text;
      if (typeof item.content === 'string') return item.content;
      return '';
    })
    .join('')
    .trim();
}

function eventDelta(event: RunEvent): string {
  return event.type === 'answer_delta' && typeof event.data.delta === 'string'
    ? event.data.delta
    : '';
}

function stringData(event: RunEvent, key: string): string {
  const value = event.data[key];
  return typeof value === 'string' ? value : '';
}

function recordData(event: RunEvent, key: string): Record<string, unknown> {
  const value = event.data[key];
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function compactText(value: string, limit = 180): string {
  const normalized = value.replace(/\s+/g, ' ').trim();
  return normalized.length <= limit ? normalized : `${normalized.slice(0, limit - 3)}...`;
}

function agentName(agentId: string): string {
  if (agentId.endsWith('.planner')) return '规划器';
  if (agentId.endsWith('.analyst')) return '数据分析器';
  if (agentId.endsWith('.verifier')) return '独立验证器';
  if (agentId.endsWith('.orchestrator')) return '编排器';
  return agentId || 'Agent';
}

function referenceName(referenceId: string): string {
  if (referenceId === 'metric-definitions') return '收入、成本和毛利指标定义';
  if (referenceId === 'order-query-rules') return '订单查询规则';
  return referenceId || '业务规则';
}

function readableTask(rawTask: string, verification: boolean): string {
  if (!rawTask) return verification ? '独立复核分析结论和原始数据证据' : '执行计划中的数据分析任务';
  const originalTask = rawTask.match(/Original task:\s*\n([^\n]+)/)?.[1];
  const task = originalTask || rawTask;
  return compactText(verification ? `独立复核：${task}` : task);
}

function resultSummary(event: RunEvent): string {
  const nodeResult = recordData(event, 'node_result');
  if (typeof nodeResult.summary === 'string' && nodeResult.summary.trim()) {
    const summary = nodeResult.summary.trim();
    if (summary.startsWith('{')) {
      try {
        const parsed = JSON.parse(summary) as { verdict?: unknown; evidence_level?: unknown };
        if (parsed.verdict === 'passed') {
          return `独立验证通过，证据等级为 ${String(parsed.evidence_level || '已验证')}`;
        }
      } catch {
        return compactText(summary);
      }
    }
    return compactText(summary);
  }
  const raw = stringData(event, 'result_summary');
  if (!raw) return '';
  try {
    const parsed = JSON.parse(raw) as { summary?: unknown; verdict?: unknown };
    if (typeof parsed.summary === 'string') return compactText(parsed.summary);
    if (parsed.verdict === 'passed') return '独立验证已通过，关键数据与计算口径一致';
  } catch {
    return compactText(raw);
  }
  return '';
}

function inputSummary(event: RunEvent): string {
  const input = recordData(event, 'input_summary');
  const labels: Record<string, string> = {
    orderSerialId: '订单号',
    order_serial_id: '订单号',
    startDate: '开始日期',
    endDate: '结束日期',
    month: '月份',
    reference_id: '规则',
    referenceId: '规则',
    verdict: '结论',
    evidence_level: '证据等级',
  };
  return Object.entries(input)
    .slice(0, 5)
    .map(([key, value]) => {
      const rendered = typeof value === 'string' ? value : JSON.stringify(value);
      return `${labels[key] || key}：${compactText(rendered, 80)}`;
    })
    .join('；');
}

function toolDescription(event: RunEvent, tool: string): { title: string; detail: string } {
  const input = inputSummary(event);
  const planNodeId = stringData(event, 'plan_node_id').replace(/\.verify$/, '');
  if (tool === 'nino_runtime_submit_task_graph_node') {
    return {
      title: '调度数据分析节点',
      detail: planNodeId
        ? `将计划中的数据查询任务交给分析器执行；节点标识：${planNodeId}`
        : '将计划中的分析任务交给数据分析器执行',
    };
  }
  if (tool === 'nino_runtime_load_reference') {
    return { title: '加载业务规则', detail: input || '读取本次分析需要遵循的指标口径和查询约束' };
  }
  if (tool === 'nino_data_get_order_detail') {
    return { title: '查询订单明细', detail: input ? `读取订单数据，${input}` : '读取目标订单的收入、成本、退款和币种字段' };
  }
  if (tool === 'nino_runtime_submit_evaluator_verdict') {
    const summary = recordData(event, 'input_summary');
    const checked = typeof summary.checked_requirements_count === 'number'
      ? summary.checked_requirements_count
      : 0;
    const failed = typeof summary.failed_requirements_count === 'number'
      ? summary.failed_requirements_count
      : 0;
    const verdict = summary.verdict === 'passed' ? '通过' : String(summary.verdict || '待判定');
    return {
      title: '提交独立验证结论',
      detail: `结论：${verdict}；证据等级：${String(summary.evidence_level || '待判定')}；已核对 ${checked} 项，失败 ${failed} 项`,
    };
  }
  return {
    title: '调用数据查询工具',
    detail: `${tool ? `工具 ${tool}` : '执行获准的数据查询'}${input ? `，${input}` : ''}`,
  };
}

function progressUpdate(event: RunEvent): ProgressUpdate | null {
  const step = typeof event.data.step === 'number' ? event.data.step : 0;
  const phase = stringData(event, 'phase');
  const planNodeId = stringData(event, 'plan_node_id');
  const childRunId = stringData(event, 'child_run_id');
  const callId = stringData(event, 'call_id');
  const tool = stringData(event, 'tool');
  const nodeKind = stringData(event, 'node_kind');
  const agent = agentName(stringData(event, 'agent_id'));
  const isVerification = nodeKind === 'verification' || planNodeId.endsWith('.verify');

  switch (event.type) {
    case 'run_started':
      return { key: 'run', title: '接收并分析问题', detail: '编排器已接收请求，准备判断任务类型和生成执行计划', status: 'completed', headline: '正在分析问题' };
    case 'skill_selected':
      return {
        key: `skill:${stringData(event, 'skill_id')}:${childRunId}`,
        title: '启用数据分析能力',
        detail: `${agent}已加载能力 ${stringData(event, 'skill_id') || '数据分析'}，将按该能力的规则执行`,
        status: 'completed',
      };
    case 'model_started': {
      const title = phase === 'planning'
        ? '生成执行计划'
        : phase === 'reconciliation' || phase === 'history_reconciliation'
          ? '汇总并生成最终回答'
          : isVerification ? '分析验证证据' : '分析工具结果';
      const detail = phase === 'planning'
        ? '规划器正在理解问题，拆分查询、计算和独立验证任务'
        : phase === 'history_reconciliation'
          ? '编排器正在结合会话历史直接计算本次追问的答案'
          : phase === 'reconciliation'
            ? '编排器正在汇总已通过验证的数据，组织最终中文答复'
            : isVerification
              ? `${agent}正在检查第 ${step} 轮证据，决定是否需要补充查询`
              : `${agent}正在分析第 ${step} 轮查询结果，决定下一步操作`;
      return {
        key: `model:${phase || 'worker'}:${step}:${childRunId}`,
        title,
        detail,
        status: 'active',
        headline: title === '汇总并生成最终回答' ? '正在流式生成回答' : `正在${title}`,
      };
    }
    case 'model_completed': {
      const title = phase === 'planning'
        ? '生成执行计划'
        : phase === 'reconciliation' || phase === 'history_reconciliation'
          ? '汇总并生成最终回答'
          : isVerification ? '分析验证证据' : '分析工具结果';
      return {
        key: `model:${phase || 'worker'}:${step}:${childRunId}`,
        title,
        detail: phase === 'planning'
          ? '规划器理解问题并拆分查询、计算和独立验证任务'
          : phase === 'history_reconciliation'
            ? '编排器结合会话历史计算本次追问的答案'
            : phase === 'reconciliation'
              ? '编排器汇总已通过验证的数据并组织最终中文答复'
            : `${agent}已完成第 ${step} 轮判断`,
        status: 'completed',
      };
    }
    case 'graph_planned': {
      const nodes = Array.isArray(event.data.nodes) ? event.data.nodes : [];
      const tasks = nodes
        .map((node) => {
          if (!node || typeof node !== 'object') return '';
          const data = node as Record<string, unknown>;
          return data.kind === 'verification'
            ? '独立验证分析结果、原始数据和计算口径'
            : data.task;
        })
        .filter((task): task is string => typeof task === 'string' && Boolean(task));
      return {
        key: `graph:${step}`,
        title: '确认执行计划',
        detail: tasks.length
          ? compactText(`计划包含 ${nodes.length} 个节点：${tasks.join('；')}`)
          : `已生成包含 ${nodes.length || 1} 个节点的分析与验证计划`,
        status: 'completed',
        headline: '正在执行分析计划',
      };
    }
    case 'graph_reconciled':
      return { key: `graph:${step}`, title: '生成结果修正计划', detail: '验证发现证据或结论存在缺口，编排器已增加补查或修正节点', status: 'completed', headline: '正在修正分析结果' };
    case 'agent_started': {
      const title = isVerification ? '开始独立复核' : '开始数据分析任务';
      return {
        key: `agent-start:${planNodeId}`,
        title,
        detail: readableTask(stringData(event, 'task'), isVerification),
        status: 'completed',
        headline: isVerification ? '正在独立复核分析结果' : '正在执行数据分析任务',
      };
    }
    case 'agent_completed': {
      const title = isVerification ? '独立复核完成' : '数据分析任务完成';
      return {
        key: `agent-result:${planNodeId}`,
        title,
        detail: resultSummary(event) || (isVerification ? '独立验证器已完成证据复核' : '数据分析器已返回结构化分析结果'),
        status: 'completed',
      };
    }
    case 'agent_failed': {
      const title = isVerification ? '独立验证未通过' : '数据分析步骤失败';
      return { key: `agent-result:${planNodeId}`, title, detail: stringData(event, 'message') || '该节点未返回可用结果，编排器将判断是否重试或修正计划', status: 'failed', headline: '正在尝试修正失败步骤' };
    }
    case 'tool_started': {
      const description = toolDescription(event, tool);
      return {
        key: `tool:${callId || tool}:${childRunId}`,
        ...description,
        status: 'active',
        headline: `正在${description.title}`,
      };
    }
    case 'tool_completed': {
      const description = toolDescription(event, tool);
      return {
        key: `tool:${callId || tool}:${childRunId}`,
        title: description.title,
        detail: event.data.is_error === true
          ? `${description.detail}；调用失败，请查看后端日志中的 call_id ${callId}`
          : description.detail,
        status: event.data.is_error === true ? 'failed' : 'completed',
      };
    }
    case 'reference_loaded': {
      const referenceId = stringData(event, 'reference_id');
      return {
        key: `reference:${referenceId}:${childRunId}`,
        title: `读取${referenceName(referenceId)}`,
        detail: `已加载 ${referenceId}，后续计算和验证将使用该规则`,
        status: 'completed',
      };
    }
    case 'evaluator_verdict':
      return {
        key: `verdict:${childRunId}`,
        title: stringData(event, 'verdict') === 'passed' ? '验证结论通过' : '验证结论未通过',
        detail: stringData(event, 'verdict') === 'passed'
          ? `证据等级为 ${stringData(event, 'evidence_level') || '已验证'}，数据、计算公式和用户目标一致`
          : '存在未满足的验证要求，编排器将根据失败项生成修正计划',
        status: stringData(event, 'verdict') === 'passed' ? 'completed' : 'failed',
      };
    case 'node_skipped':
      return { key: `skipped:${planNodeId}`, title: '跳过不适用步骤', detail: `计划节点 ${planNodeId || '当前节点'} 不满足执行条件或已有可复用结果`, status: 'completed' };
    case 'clarification_requested':
      return { key: `clarification:${step}`, title: '等待用户补充信息', detail: stringData(event, 'message') || '缺少继续查询所必需的输入', status: 'completed', headline: '等待补充信息' };
    case 'answer_delta':
      return { key: 'answer-stream', title: '流式发送最终回答', detail: '最终回答正在分批发送到浏览器，已收到的内容会立即显示', status: 'active', headline: '正在流式生成回答' };
    case 'run_completed':
      return { key: 'terminal', title: '任务执行完成', detail: '所有必要步骤已结束，最终回答已保存到当前会话', status: 'completed', headline: '任务已完成' };
    case 'run_failed':
      return { key: 'terminal', title: '任务执行失败', detail: stringData(event, 'message') || `错误代码：${stringData(event, 'error_code') || 'UNKNOWN'}`, status: 'failed', headline: '任务执行失败' };
    case 'run_cancelled':
      return { key: 'terminal', title: '任务已取消', detail: '用户停止了本次执行，未继续运行后续步骤', status: 'failed', headline: '任务已取消' };
    default:
      return null;
  }
}

function formatElapsed(startedAt: number, now: number): string {
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function errorText(error: unknown): string {
  if (error instanceof Error) return error.message;
  return '请求未完成，请稍后重试。';
}

export default function App() {
  const [conversationId, setConversationId] = useState<string | null>(() =>
    localStorage.getItem(CONVERSATION_STORAGE_KEY),
  );
  const [messages, setMessages] = useState<DialogueMessage[]>([]);
  const [generating, setGenerating] = useState(false);
  const [restoring, setRestoring] = useState(Boolean(conversationId));
  const [progressByMessage, setProgressByMessage] = useState<Record<string, RunProgress>>({});
  const [expandedProgress, setExpandedProgress] = useState<Record<string, boolean>>({});
  const [clock, setClock] = useState(0);
  const initialConversationId = useRef(conversationId);
  const activeRunId = useRef<string | null>(null);
  const activeAssistantId = useRef<string | null>(null);
  const streamController = useRef<AbortController | null>(null);

  const acceptRun = useCallback((assistantId: string, runId: string, status: ProgressStatus = 'queued') => {
    setClock(Date.now());
    setExpandedProgress((current) => ({ ...current, [assistantId]: false }));
    setProgressByMessage((current) => {
      const previous = current[assistantId];
      return {
        ...current,
        [assistantId]: {
          runId,
          status,
          headline: status === 'queued' ? '任务已提交，等待执行' : '正在恢复任务进度',
          startedAt: previous?.startedAt ?? Date.now(),
          items: previous?.items ?? [],
        },
      };
    });
  }, []);

  const applyProgressEvent = useCallback((assistantId: string, event: RunEvent) => {
    const update = progressUpdate(event);
    if (!update) return;
    const terminalStatus: ProgressStatus | null = event.type === 'run_completed'
      ? 'completed'
      : event.type === 'run_failed'
        ? 'failed'
        : event.type === 'run_cancelled'
          ? 'cancelled'
          : null;
    if (terminalStatus) {
      setExpandedProgress((current) => ({ ...current, [assistantId]: false }));
    }
    setProgressByMessage((current) => {
      const previous = current[assistantId];
      const base: RunProgress = previous?.runId === event.run_id
        ? previous
        : {
            runId: event.run_id,
            status: 'running',
            headline: '正在执行任务',
            startedAt: Date.now(),
            items: [],
          };
      const existingIndex = base.items.findIndex((item) => item.key === update.key);
      const item = {
        ...update,
        sequence: existingIndex >= 0 ? base.items[existingIndex].sequence : event.sequence,
      };
      const items = existingIndex >= 0
        ? base.items.map((existing, index) => (index === existingIndex ? item : existing))
        : [...base.items, item];
      items.sort((left, right) => left.sequence - right.sequence);
      return {
        ...current,
        [assistantId]: {
          ...base,
          status: terminalStatus ?? 'running',
          headline: update.headline ?? base.headline,
          finishedAt: terminalStatus ? Date.now() : undefined,
          items,
        },
      };
    });
  }, []);

  const finishProgress = useCallback((assistantId: string, run: Run) => {
    setExpandedProgress((current) => ({ ...current, [assistantId]: false }));
    setProgressByMessage((current) => {
      const progress = current[assistantId];
      if (!progress) return current;
      const status: ProgressStatus = run.status;
      const headline = status === 'completed'
        ? '任务已完成'
        : status === 'cancelled'
          ? '任务已取消'
          : '任务执行失败';
      return {
        ...current,
        [assistantId]: {
          ...progress,
          runId: run.id,
          status,
          headline,
          finishedAt: Date.now(),
          items: progress.items.map((item) =>
            item.status === 'active'
              ? { ...item, status: status === 'completed' ? 'completed' : 'failed' }
              : item,
          ),
        },
      };
    });
  }, []);

  const updateAssistant = useCallback(
    (id: string, updater: (message: DialogueMessage) => DialogueMessage) => {
      setMessages((current) =>
        current.map((message) => (message.id === id ? updater(message) : message)),
      );
    },
    [],
  );

  const finishAssistant = useCallback(
    (assistantId: string, run: Run) => {
      updateAssistant(assistantId, (message) => ({
        ...message,
        content:
          run.answer ||
          (typeof message.content === 'string' && message.content
            ? message.content
            : run.error_code || '任务未能完成。'),
        status: run.status === 'completed' ? 'completed' : 'incomplete',
      }));
      finishProgress(assistantId, run);
    },
    [finishProgress, updateAssistant],
  );

  const progressIsActive = Object.values(progressByMessage).some(
    (progress) => progress.status === 'queued' || progress.status === 'running',
  );

  useEffect(() => {
    if (!progressIsActive) {
      return;
    }
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [progressIsActive]);

  useEffect(() => {
    const storedConversationId = initialConversationId.current;
    if (!storedConversationId) {
      return;
    }

    let active = true;
    Promise.all([listMessages(storedConversationId), listRuns(storedConversationId)])
      .then(async ([items, runs]) => {
        if (!active) return;
        const restoredMessages = items.map(toDialogueMessage);
        setMessages(restoredMessages);

        const activeRun = runs.find((run) => run.status === 'queued' || run.status === 'running');
        if (!activeRun) return;

        const assistantId = `assistant-${activeRun.id}`;
        setMessages((current) => [
          ...current,
          {
            id: assistantId,
            role: 'assistant',
            content: '',
            createdAt: Date.now(),
            status: 'in_progress',
          },
        ]);
        setGenerating(true);
        acceptRun(assistantId, activeRun.id, activeRun.status);
        activeRunId.current = activeRun.id;
        activeAssistantId.current = assistantId;
        const controller = new AbortController();
        streamController.current = controller;

        const result = await streamRunEvents(activeRun.id, {
          signal: controller.signal,
          onEvent: (event) => {
            if (!active) return;
            applyProgressEvent(assistantId, event);
            const delta = eventDelta(event);
            if (delta) {
              updateAssistant(assistantId, (message) => ({
                ...message,
                content: `${typeof message.content === 'string' ? message.content : ''}${delta}`,
              }));
            }
          },
        });
        if (active) finishAssistant(assistantId, result);
      })
      .catch((error: unknown) => {
        if (!active || (error instanceof DOMException && error.name === 'AbortError')) return;
        const assistantId = activeAssistantId.current;
        if (assistantId) {
          updateAssistant(assistantId, (message) => ({
            ...message,
            content:
              typeof message.content === 'string' && message.content
                ? message.content
                : '连接已中断，请刷新页面继续接收结果。',
            status: 'incomplete',
          }));
        }
        if (error instanceof NinoApiError && error.status === 404) {
          localStorage.removeItem(CONVERSATION_STORAGE_KEY);
          setConversationId(null);
        }
        if (assistantId) {
          setProgressByMessage((current) => {
            const progress = current[assistantId];
            return progress ? {
              ...current,
              [assistantId]: {
                ...progress,
                status: 'failed',
                headline: '进度连接已中断',
                finishedAt: Date.now(),
                items: progress.items.map((item) =>
                  item.status === 'active' ? { ...item, status: 'failed' } : item,
                ),
              },
            } : current;
          });
        }
        Toast.error({ content: `无法恢复上次会话：${errorText(error)}` });
      })
      .finally(() => {
        if (active) {
          activeRunId.current = null;
          activeAssistantId.current = null;
          streamController.current = null;
          setGenerating(false);
          setRestoring(false);
        }
      });

    return () => {
      active = false;
    };
  }, [acceptRun, applyProgressEvent, finishAssistant, updateAssistant]);

  useEffect(
    () => () => {
      streamController.current?.abort();
    },
    [],
  );

  const sendText = useCallback(
    async (content: string) => {
      const text = content.trim();
      if (!text || generating || restoring) return;

      const now = Date.now();
      const userMessageId = `user-${now}`;
      const assistantMessageId = `assistant-${now}`;
      setMessages((current) => [
        ...current,
        { id: userMessageId, role: 'user', content: text, createdAt: now, status: 'completed' },
        {
          id: assistantMessageId,
          role: 'assistant',
          content: '',
          createdAt: now + 1,
          status: 'in_progress',
        },
      ]);
      setGenerating(true);
      activeAssistantId.current = assistantMessageId;
      acceptRun(assistantMessageId, assistantMessageId);

      try {
        let currentConversationId = conversationId;
        if (!currentConversationId) {
          const conversation = await createConversation(text.slice(0, 40));
          currentConversationId = conversation.id;
          localStorage.setItem(CONVERSATION_STORAGE_KEY, conversation.id);
          setConversationId(conversation.id);
        }

        const controller = new AbortController();
        streamController.current = controller;

        const run = await streamMessage(currentConversationId, text, {
          signal: controller.signal,
          onAccepted: (accepted) => {
            activeRunId.current = accepted.run_id;
            acceptRun(assistantMessageId, accepted.run_id, accepted.status);
          },
          onEvent: (event) => {
            applyProgressEvent(assistantMessageId, event);
            const delta = eventDelta(event);
            if (delta) {
              updateAssistant(assistantMessageId, (message) => ({
                ...message,
                content: `${typeof message.content === 'string' ? message.content : ''}${delta}`,
              }));
            }
          },
        });
        finishAssistant(assistantMessageId, run);
      } catch (error: unknown) {
        if (!(error instanceof DOMException && error.name === 'AbortError')) {
          const detail = errorText(error);
          updateAssistant(assistantMessageId, (message) => ({
            ...message,
            content: typeof message.content === 'string' && message.content ? message.content : detail,
            status: 'incomplete',
          }));
          setProgressByMessage((current) => {
            const progress = current[assistantMessageId];
            return progress ? {
              ...current,
              [assistantMessageId]: {
                ...progress,
                status: 'failed',
                headline: '任务执行失败',
                finishedAt: Date.now(),
                items: progress.items.map((item) =>
                  item.status === 'active' ? { ...item, status: 'failed' } : item,
                ),
              },
            } : current;
          });
          Toast.error({ content: detail });
        }
      } finally {
        activeRunId.current = null;
        activeAssistantId.current = null;
        streamController.current = null;
        setGenerating(false);
      }
    },
    [acceptRun, applyProgressEvent, conversationId, finishAssistant, generating, restoring, updateAssistant],
  );

  const handleMessageSend = useCallback(
    (message: MessageContent) => {
      void sendText(textFromInput(message));
    },
    [sendText],
  );

  const stopGenerating = useCallback(async () => {
    const runId = activeRunId.current;
    const assistantId = activeAssistantId.current;
    if (!runId) return;

    try {
      await cancelRun(runId);
    } catch (error: unknown) {
      Toast.warning({ content: `取消请求失败：${errorText(error)}` });
    } finally {
      streamController.current?.abort();
      if (assistantId) {
        updateAssistant(assistantId, (message) => ({
          ...message,
          content:
            typeof message.content === 'string' && message.content
              ? message.content
              : '已停止生成。',
          status: 'incomplete',
        }));
      }
      if (assistantId) {
        setProgressByMessage((current) => {
          const progress = current[assistantId];
          return progress ? {
            ...current,
            [assistantId]: {
              ...progress,
              status: 'cancelled',
              headline: '任务已取消',
              finishedAt: Date.now(),
              items: progress.items.map((item) =>
                item.status === 'active' ? { ...item, status: 'failed' } : item,
              ),
            },
          } : current;
        });
      }
      setGenerating(false);
    }
  }, [updateAssistant]);

  const startNewConversation = useCallback(() => {
    localStorage.removeItem(CONVERSATION_STORAGE_KEY);
    setConversationId(null);
    setMessages([]);
    setProgressByMessage({});
    setExpandedProgress({});
  }, []);

  const dialogueRenderConfig = {
    renderDialogueTitle: ({ defaultTitle, message }: { defaultTitle?: ReactNode; message?: DialogueMessage }) => {
      const progress = message?.id ? progressByMessage[message.id] : undefined;
      if (!progress) return defaultTitle;
      const statusLabel = progress.status === 'queued'
        ? '等待中'
        : progress.status === 'running'
          ? '执行中'
          : progress.status === 'completed'
            ? '已完成'
            : progress.status === 'cancelled'
              ? '已取消'
              : '失败';
      const elapsedAt = progress.finishedAt ?? clock;
      return (
        <div className={`dialogue-title-with-status status-${progress.status}`}>
          {defaultTitle}
          <span className="dialogue-run-status">
            {progress.status === 'queued' || progress.status === 'running' ? <IconLoading spin /> : null}
            {progress.status === 'completed' ? <IconTickCircle /> : null}
            {progress.status === 'failed' || progress.status === 'cancelled' ? <IconAlertCircle /> : null}
            <span>{statusLabel}</span>
            <span className="dialogue-run-divider" aria-hidden="true">·</span>
            <IconClock />
            <span>{formatElapsed(progress.startedAt, elapsedAt)}</span>
          </span>
        </div>
      );
    },
    renderDialogueContent: ({
      className,
      defaultContent,
      message,
    }: {
      className?: string;
      defaultContent?: ReactNode | ReactNode[];
      message?: DialogueMessage;
    }) => {
      const messageId = message?.id;
      const progress = messageId ? progressByMessage[messageId] : undefined;
      if (!progress || !messageId) {
        return <div className={className}>{defaultContent}</div>;
      }

      const isActive = progress.status === 'queued' || progress.status === 'running';
      const isExpanded = Boolean(expandedProgress[messageId]);
      const hasAnswer = typeof message.content === 'string' && message.content.length > 0;
      const showDetails = isActive || isExpanded;
      return (
        <div className={`${className ?? ''} dialogue-content-with-progress`}>
          {showDetails ? (
            <div
              className={`message-progress message-progress-${progress.status}`}
              aria-label="任务执行进度"
              aria-live={isActive ? 'polite' : 'off'}
            >
              <div className="message-progress-headline">
                <span className="message-progress-icon" aria-hidden="true">
                  {isActive ? <IconLoading spin /> : null}
                  {progress.status === 'completed' ? <IconTickCircle /> : null}
                  {progress.status === 'failed' || progress.status === 'cancelled'
                    ? <IconAlertCircle />
                    : null}
                </span>
                <strong>{progress.headline}</strong>
              </div>
              {progress.items.length ? (
                <ol className="message-progress-list">
                  {progress.items.map((item, index) => (
                    <li key={item.key} className={`progress-item progress-item-${item.status}`}>
                      <span className="progress-marker" aria-hidden="true">
                        {item.status === 'completed' ? <IconTickCircle /> : null}
                        {item.status === 'failed' ? <IconAlertCircle /> : null}
                        {item.status === 'active' ? <IconLoading spin /> : null}
                      </span>
                      <span className="progress-copy">
                        <span className="progress-step-meta">
                          步骤 {index + 1} · 事件 #{item.sequence}
                        </span>
                        <strong>{item.title}</strong>
                        <span className="progress-detail">{item.detail}</span>
                      </span>
                    </li>
                  ))}
                </ol>
              ) : null}
            </div>
          ) : null}
          {hasAnswer ? <div className="message-final-answer">{defaultContent}</div> : null}
          {!isActive && progress.items.length ? (
            <button
              type="button"
              className="progress-details-toggle"
              aria-expanded={isExpanded}
              onClick={() => setExpandedProgress((current) => ({
                ...current,
                [messageId]: !current[messageId],
              }))}
            >
              {isExpanded ? <IconChevronUp /> : <IconChevronDown />}
              <span>{isExpanded ? '收起执行明细' : '查看执行明细'}</span>
            </button>
          ) : null}
        </div>
      );
    },
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">N</div>
          <div>
            <Typography.Title heading={5}>Nino Agent</Typography.Title>
            <span className="brand-subtitle">数据分析会话</span>
          </div>
        </div>
        <div className="topbar-actions">
          <div className="runtime-status" title="通过本地 Nino Runtime 连接">
            <IconPulse size="small" />
            <span>Runtime</span>
          </div>
          <Tooltip content="新建会话">
            <Button
              aria-label="新建会话"
              icon={<IconPlus />}
              theme="borderless"
              disabled={generating}
              onClick={startNewConversation}
            />
          </Tooltip>
        </div>
      </header>

      <section className="conversation" aria-label="Nino Agent 会话">
        {messages.length === 0 && !restoring ? (
          <div className="empty-state">
            <div className="empty-mark" aria-hidden="true">N</div>
            <Typography.Title heading={3}>今天想分析什么？</Typography.Title>
            <Typography.Text type="tertiary">
              输入自然语言问题，Nino 会查询并验证数据后回答。
            </Typography.Text>
            <div className="suggestions">
              {suggestions.map((suggestion) => (
                <button type="button" key={suggestion} onClick={() => void sendText(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <AIChatDialogue
            className="dialogue"
            chats={messages}
            roleConfig={roleConfig}
            align="leftRight"
            mode="bubble"
            showReference={false}
            dialogueRenderConfig={dialogueRenderConfig}
          />
        )}

        <div className="composer-wrap">
          <AIChatInput
            className="composer"
            placeholder="向 Nino 提问"
            generating={generating}
            canSend={!restoring}
            showUploadButton={false}
            showTemplateButton={false}
            showReference={false}
            sendHotKey="enter"
            onMessageSend={handleMessageSend}
            onStopGenerate={() => void stopGenerating()}
          />
          <div className="composer-meta">
            <span>Enter 发送 · Shift + Enter 换行</span>
            <span>{conversationId ? '会话已保存' : '新会话'}</span>
          </div>
        </div>
      </section>
    </main>
  );
}
