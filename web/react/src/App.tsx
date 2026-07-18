import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AIChatDialogue,
  AIChatInput,
  Button,
  Toast,
  Tooltip,
  Typography,
} from '@douyinfe/semi-ui';
import { IconPlus, IconPulse } from '@douyinfe/semi-icons';
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
  const initialConversationId = useRef(conversationId);
  const activeRunId = useRef<string | null>(null);
  const activeAssistantId = useRef<string | null>(null);
  const streamController = useRef<AbortController | null>(null);

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
    },
    [updateAssistant],
  );

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
        activeRunId.current = activeRun.id;
        activeAssistantId.current = assistantId;
        const controller = new AbortController();
        streamController.current = controller;

        const result = await streamRunEvents(activeRun.id, {
          signal: controller.signal,
          onEvent: (event) => {
            if (!active) return;
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
  }, [finishAssistant, updateAssistant]);

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
          },
          onEvent: (event) => {
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
          Toast.error({ content: detail });
        }
      } finally {
        activeRunId.current = null;
        activeAssistantId.current = null;
        streamController.current = null;
        setGenerating(false);
      }
    },
    [conversationId, finishAssistant, generating, restoring, updateAssistant],
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
      setGenerating(false);
    }
  }, [updateAssistant]);

  const startNewConversation = useCallback(() => {
    localStorage.removeItem(CONVERSATION_STORAGE_KEY);
    setConversationId(null);
    setMessages([]);
  }, []);

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
