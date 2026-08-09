import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { FetchEventSourceInit } from '@microsoft/fetch-event-source';
import { ChatInput } from './input';
import { ChatOutput } from './output';
import { ChatProvider } from './provider';

const mocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  fetchEventSource: vi.fn(),
  setSelectedContract: vi.fn(),
  cancelFetch: vi.fn(),
}));

vi.mock('../../../contexts/ContractHistoryContext', () => ({
  useContractHistory: () => ({
    contracts: [{ contract_id: 'CONTRACT_A', filename: 'Clean_MSA.pdf' }],
    selectedContractId: 'CONTRACT_A',
    setSelectedContract: mocks.setSelectedContract,
  }),
}));

vi.mock('../../../contexts/ChatSessionContext', () => ({
  useChatSession: () => ({
    activeSession: {
      session_id: 'SESSION_A', contract_id: 'CONTRACT_A', title: 'Payment review',
      created_at: null, updated_at: null, message_count: 2,
    },
    createSession: mocks.createSession,
  }),
}));

vi.mock('../../../lib/authStore', () => ({
  authHeader: () => 'Bearer test',
  clearSession: vi.fn(),
}));

vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: mocks.fetchEventSource,
}));

vi.mock('../../../services/modelRegistryApi', () => ({
  getWorkflowModels: () => Promise.resolve({
    workflow: 'chat',
    default_model: 'gemini-2.5-flash',
    models: [
      { id: 'gemini-2.5-flash', provider: 'google', display_label: 'Google · Gemini 2.5 Flash' },
      { id: 'gpt-4o', provider: 'openai', display_label: 'OpenAI · GPT-4o' },
    ],
  }),
}));

function renderChat() {
  return render(
    <ChatProvider>
      <ChatOutput />
      <ChatInput />
    </ChatProvider>
  );
}

async function submit(prompt = 'Compare the payment terms') {
  await waitFor(() => expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeEnabled());
  fireEvent.change(screen.getByRole('textbox'), { target: { value: prompt } });
  fireEvent.click(screen.getByRole('button', { name: /Send your prompt now/i }));
}

describe('Contract Chat Stop generating', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.cancelFetch.mockResolvedValue(new Response(
      JSON.stringify({ status: 'cancelled' }),
      { status: 202, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', mocks.cancelFetch);
  });

  it('aborts the active stream, ignores late chunks, and recovers the composer', async () => {
    let streamOptions!: FetchEventSourceInit;
    mocks.fetchEventSource.mockImplementation(async (_url, options) => {
      streamOptions = options;
      await options.onopen?.(new Response(null, { status: 200 }));
      await new Promise<void>((resolve) => {
        options.signal.addEventListener('abort', () => resolve(), { once: true });
      });
    });
    renderChat();
    await submit();

    const stop = await screen.findByRole('button', { name: 'Stop generating' });
    expect(screen.queryByRole('button', { name: /Send your prompt now/i })).not.toBeInTheDocument();
    fireEvent.click(stop);

    await waitFor(() => expect(streamOptions.signal!.aborted).toBe(true));
    expect(mocks.cancelFetch).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/chat\/runs\/[0-9a-f-]+\/cancel$/),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ session_id: 'SESSION_A' }),
      }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('Generation stopped');
    await waitFor(() => expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeEnabled());

    streamOptions.onmessage?.({ id: '', event: '', data: JSON.stringify({
      type: 'ai_message', status: 'passed', content: 'Late answer must be ignored',
    }) });
    streamOptions.onmessage?.({ id: '', event: '', data: JSON.stringify({ type: 'end', status: 'passed', content: '' }) });
    expect(screen.queryByText('Late answer must be ignored')).not.toBeInTheDocument();
    expect(screen.getAllByText('Generation stopped')).toHaveLength(1);
  });

  it('uses a fresh controller and completes a new request after cancellation', async () => {
    const signals: AbortSignal[] = [];
    mocks.fetchEventSource
      .mockImplementationOnce(async (_url, options) => {
        signals.push(options.signal);
        await new Promise<void>((resolve) => {
          options.signal.addEventListener('abort', () => resolve(), { once: true });
        });
      })
      .mockImplementationOnce(async (_url, options) => {
        signals.push(options.signal);
        options.onmessage?.({ data: JSON.stringify({
          type: 'ai_message', status: 'passed', content: 'Fresh request completed',
        }) });
        options.onmessage?.({ data: JSON.stringify({ type: 'end', status: 'passed', content: '' }) });
        options.onclose?.();
      });
    renderChat();
    await submit('First request');
    fireEvent.click(await screen.findByRole('button', { name: 'Stop generating' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeEnabled());

    fireEvent.click(screen.getByRole('combobox', { name: 'Model' }));
    fireEvent.click(await screen.findByRole('option', { name: 'OpenAI · GPT-4o' }));
    await submit('Second request');
    expect(await screen.findByText('Fresh request completed')).toBeInTheDocument();
    expect(signals).toHaveLength(2);
    expect(signals[0]).not.toBe(signals[1]);
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
    expect(JSON.parse(mocks.fetchEventSource.mock.calls[1][1].body)).toMatchObject({
      model: 'gpt-4o',
      prompt: 'Second request',
    });
  });

  it('prevents repeated Stop activation while server acknowledgement is pending', async () => {
    let acknowledge: (response: Response) => void = () => undefined;
    mocks.cancelFetch.mockImplementationOnce(() => new Promise<Response>((resolve) => {
      acknowledge = resolve;
    }));
    mocks.fetchEventSource.mockImplementation(async (_url, options) => {
      await new Promise<void>((resolve) => {
        options.signal.addEventListener('abort', () => resolve(), { once: true });
      });
    });
    renderChat();
    await submit();

    fireEvent.click(await screen.findByRole('button', { name: 'Stop generating' }));
    const stopping = screen.getByRole('button', { name: 'Stopping…' });
    expect(stopping).toBeDisabled();
    fireEvent.click(stopping);
    expect(mocks.cancelFetch).toHaveBeenCalledTimes(1);

    acknowledge(new Response(JSON.stringify({ status: 'cancelled' }), { status: 202 }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Generation stopped');
    await waitFor(() => expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeEnabled());
  });

  it('lets durable completion win before a late stop can be activated', async () => {
    let releaseEnd: () => void = () => undefined;
    mocks.fetchEventSource.mockImplementation(async (_url, options) => {
      options.onmessage?.({ data: JSON.stringify({
        type: 'ai_message', status: 'passed', content: 'Durably completed answer',
      }) });
      await new Promise<void>((resolve) => { releaseEnd = resolve; });
      options.onmessage?.({ data: JSON.stringify({ type: 'end', status: 'passed', content: '' }) });
    });
    renderChat();
    await submit();

    expect(await screen.findByText('Durably completed answer')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Stop generating' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Finishing…' })).toBeDisabled();
    releaseEnd();
    await waitFor(() => expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeEnabled());
    expect(screen.queryByText('Generation stopped')).not.toBeInTheDocument();
  });

  it('aborts on Chat UI unmount without releasing a late answer', async () => {
    let signal: AbortSignal | undefined;
    mocks.fetchEventSource.mockImplementation(async (_url, options) => {
      signal = options.signal;
      await new Promise<void>((resolve) => {
        options.signal.addEventListener('abort', () => resolve(), { once: true });
      });
    });
    const view = renderChat();
    await submit();
    await screen.findByRole('button', { name: 'Stop generating' });
    view.unmount();
    await waitFor(() => expect(signal?.aborted).toBe(true));
  });
});
