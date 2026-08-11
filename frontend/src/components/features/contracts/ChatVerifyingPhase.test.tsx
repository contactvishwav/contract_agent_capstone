import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { FetchEventSourceInit } from '@microsoft/fetch-event-source';
import { ChatInput } from './input';
import { ChatOutput } from './output';
import { ChatProvider } from './provider';

// Retry-latency UX pass: generation finishing and Output Guard's own audit
// step starting previously looked identical to the client (a bare spinner)
// for however long that step took, including during an audit retry. The
// backend now emits a {"type": "status", "phase": "verifying"} SSE event
// once generation is done and before the terminal event - this proves the
// client actually surfaces a distinct, visible phase for it instead of
// silently ignoring it like the (deliberately unused) "history" event.

const mocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  fetchEventSource: vi.fn(),
  setSelectedContract: vi.fn(),
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

async function submit(prompt = 'What are the payment terms?') {
  await waitFor(() => expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeEnabled());
  fireEvent.change(screen.getByRole('textbox'), { target: { value: prompt } });
  fireEvent.click(screen.getByRole('button', { name: /Send your prompt now/i }));
}

describe('Contract Chat verifying phase', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows a distinct "Verifying response…" phase between generation and the final answer', async () => {
    let streamOptions!: FetchEventSourceInit;
    mocks.fetchEventSource.mockImplementation(async (_url, options) => {
      streamOptions = options;
      await options.onopen?.(new Response(null, { status: 200 }));
      // Held open deliberately - onmessage is driven manually below, and
      // resolving this too early would let handleSubmit's own `finally`
      // block set streamFinished before the test can send its events
      // (matching the real library, which only resolves once the server
      // connection itself closes).
      await new Promise<void>((resolve) => {
        options.signal.addEventListener('abort', () => resolve(), { once: true });
      });
    });
    renderChat();
    await submit();
    await waitFor(() => expect(streamOptions).toBeDefined());

    // Before the "verifying" event, a plain generating spinner is showing
    // with no "Verifying" text yet.
    expect(screen.queryByText('Verifying response…')).not.toBeInTheDocument();

    streamOptions.onmessage?.({ id: '', event: '', data: JSON.stringify({
      type: 'status', phase: 'verifying', content: '',
    }) });
    expect(await screen.findByText('Verifying response…')).toBeInTheDocument();

    // The final answer arriving clears the verifying phase along with the
    // rest of the generating state.
    streamOptions.onmessage?.({ id: '', event: '', data: JSON.stringify({
      type: 'ai_message', status: 'passed', content: 'Payment is due within 90 days.',
    }) });
    streamOptions.onmessage?.({ id: '', event: '', data: JSON.stringify({ type: 'end', status: 'passed', content: '' }) });

    expect(await screen.findByText('Payment is due within 90 days.')).toBeInTheDocument();
    expect(screen.queryByText('Verifying response…')).not.toBeInTheDocument();
  });

  it('never shows the verifying phase for a turn that never sends the status event', async () => {
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
    await waitFor(() => expect(streamOptions).toBeDefined());

    streamOptions.onmessage?.({ id: '', event: '', data: JSON.stringify({
      type: 'ai_message', status: 'passed', content: 'Payment is due within 90 days.',
    }) });
    streamOptions.onmessage?.({ id: '', event: '', data: JSON.stringify({ type: 'end', status: 'passed', content: '' }) });

    expect(await screen.findByText('Payment is due within 90 days.')).toBeInTheDocument();
    expect(screen.queryByText('Verifying response…')).not.toBeInTheDocument();
  });

  it('resolves to a clear error instead of a permanent hang when the stream ends without a terminal event', async () => {
    // Real, confirmed live bug: a real Playwright browser reproduction
    // (route-intercepted to truncate a real /api/run/ response right
    // after a real "verifying" event) showed the message left stuck on
    // "Verifying response..." forever - fetchEventSource's onclose()
    // fires when the underlying stream just ends (a dropped connection,
    // a proxy truncation, a dev-server reload killing an in-flight
    // request), and unlike onerror(), onclose() previously had no
    // fallback to flip the message's generating/verifying state at all.
    let streamOptions!: FetchEventSourceInit;
    let resolveStream!: () => void;
    mocks.fetchEventSource.mockImplementation(async (_url, options) => {
      streamOptions = options;
      await options.onopen?.(new Response(null, { status: 200 }));
      await new Promise<void>((resolve) => { resolveStream = resolve; });
    });
    renderChat();
    await submit();
    await waitFor(() => expect(streamOptions).toBeDefined());

    streamOptions.onmessage?.({ id: '', event: '', data: JSON.stringify({
      type: 'status', phase: 'verifying', content: '',
    }) });
    expect(await screen.findByText('Verifying response…')).toBeInTheDocument();

    // The stream just ends - no "end" event, no thrown error - matching
    // fetchEventSource's real behavior when the body's ReadableStream
    // closes cleanly on its own.
    streamOptions.onclose?.();
    resolveStream();

    expect(await screen.findByText('Response failed before completion. Please retry.')).toBeInTheDocument();
    expect(screen.queryByText('Verifying response…')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeEnabled());
  });

  it('does not report a spurious error when onclose fires after a real terminal event already arrived', async () => {
    let streamOptions!: FetchEventSourceInit;
    let resolveStream!: () => void;
    mocks.fetchEventSource.mockImplementation(async (_url, options) => {
      streamOptions = options;
      await options.onopen?.(new Response(null, { status: 200 }));
      await new Promise<void>((resolve) => { resolveStream = resolve; });
    });
    renderChat();
    await submit();
    await waitFor(() => expect(streamOptions).toBeDefined());

    streamOptions.onmessage?.({ id: '', event: '', data: JSON.stringify({
      type: 'ai_message', status: 'passed', content: 'Payment is due within 90 days.',
    }) });
    streamOptions.onmessage?.({ id: '', event: '', data: JSON.stringify({ type: 'end', status: 'passed', content: '' }) });
    expect(await screen.findByText('Payment is due within 90 days.')).toBeInTheDocument();

    // The library's own onclose still fires after a normal completion -
    // must not overwrite the real answer with a spurious error.
    streamOptions.onclose?.();
    resolveStream();

    expect(screen.queryByText('Response failed before completion. Please retry.')).not.toBeInTheDocument();
    expect(screen.getByText('Payment is due within 90 days.')).toBeInTheDocument();
  });
});
