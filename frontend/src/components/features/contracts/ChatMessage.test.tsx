import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ChatMessage } from './message';
import { groupStoredMessagesIntoUiMessages } from './sessionMessages';

describe('ChatMessage', () => {
  it('combines streamed chunks before rendering sanitized GFM', () => {
    const { container } = render(<ChatMessage message={{
      id: 'a', type: 'ai', generating: false,
      parts: [
        { type: 'ai_message', content: '**Payment' },
        { type: 'ai_message', content: '**\n\n- Net 90\n\n| Term | Value |\n|---|---|\n| Fee | $50,000 |\n\n[bad](javascript:alert(1))\n\n<script>alert(1)</script>' },
      ],
    }} />);
    expect(screen.getByText('Payment').tagName).toBe('STRONG');
    expect(screen.getByText('Net 90')).toBeInTheDocument();
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText('bad').closest('a')).not.toHaveAttribute('href');
  });

  it('renders malformed model markdown safely and citations separately', () => {
    render(<ChatMessage message={{
      id: 'b', type: 'ai', generating: false,
      parts: [
        { type: 'ai_message', content: '* **Payment**:**** within 90 days' },
        { type: 'citations', content: JSON.stringify([{
          citation_id: 'CIT_1', contract_id: 'CONTRACT_A', filename: 'Clean_MSA.pdf',
          source_type: 'chunk', page: null, section_id: null, section_title: null,
          clause_id: null, clause_type: null, chunk_id: 'CHUNK_1', chunk_index: 2,
          start_offset: 10, end_offset: 40, excerpt: 'Payment within 90 days.',
          tool_name: 'EnhancedContractSearch', tool_call_id: 'call_1', validation_status: 'tenant_active',
        }]) },
      ],
    }} />);
    expect(screen.getAllByText(/within 90 days/)).toHaveLength(2);
    expect(screen.getByRole('complementary', { name: 'Sources' })).toHaveTextContent('Clean_MSA.pdf');
    expect(screen.getByRole('complementary', { name: 'Sources' })).not.toHaveTextContent('page');
  });

  it('labels a completed answer that has no verified evidence', () => {
    render(<ChatMessage message={{
      id: 'c', type: 'ai', generating: false,
      parts: [{ type: 'ai_message', content: 'I could not find supporting records.' }],
    }} />);
    expect(screen.getByText('No verified source citations were produced for this answer.')).toBeInTheDocument();
  });

  it('restores a failed terminal turn as an error rather than a normal answer', () => {
    const restored = groupStoredMessagesIntoUiMessages([{
      message_id: 'm1', role: 'ai_message',
      content: 'Response validation failed. Please retry.',
      model: 'gemini-2.5-flash', tool_name: null, tool_call_id: null,
      citations: [], terminal_status: 'validation_failed', sequence: 2,
      created_at: '2026-08-08T00:00:00Z',
    }]);

    render(<ChatMessage message={restored[0]} />);

    expect(screen.getByRole('alert')).toHaveTextContent('Response validation failed');
    expect(screen.queryByText('No verified source citations were produced for this answer.')).not.toBeInTheDocument();
  });

  it('restores cancellation as a restrained terminal state, not a system failure', () => {
    const restored = groupStoredMessagesIntoUiMessages([{
      message_id: 'm2', role: 'ai_message', content: 'Generation stopped',
      model: 'gpt-4o', tool_name: null, tool_call_id: null,
      citations: [], terminal_status: 'cancelled', sequence: 4,
      created_at: '2026-08-08T00:00:00Z',
    }]);

    render(<ChatMessage message={restored[0]} />);

    expect(screen.getByRole('alert')).toHaveTextContent('Generation stopped');
    expect(screen.queryByText(/failed/i)).not.toBeInTheDocument();
  });
});
