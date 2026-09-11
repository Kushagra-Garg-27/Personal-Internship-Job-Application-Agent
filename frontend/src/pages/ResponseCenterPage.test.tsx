import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ResponseCenterPage, ResponseCenterPlaceholder } from './ResponseCenterPage';
import { api } from '../api/client';
import type {
  RecruiterMessage,
  MessageStats,
  IntegrationHealthEvent,
  ApproveReplyResponse,
  AcknowledgeMessageResponse,
} from '../types';

vi.mock('../api/client', () => ({
  api: {
    getMessages: vi.fn(),
    getMessageStats: vi.fn(),
    getIntegrationHealth: vi.fn(),
    draftReply: vi.fn(),
    approveReply: vi.fn(),
    acknowledgeMessage: vi.fn(),
  },
}));

const makeMessage = (overrides: Partial<RecruiterMessage> = {}): RecruiterMessage => ({
  id: 1,
  gmail_id: 'gmail-1',
  sender: 'recruiter@acme.com',
  sender_domain: 'acme.com',
  subject: 'Interview invitation',
  body_preview: 'We would like to schedule an interview next week.',
  received_at: new Date().toISOString(),
  classification: 'interview_invite',
  classification_source: 'rules',
  classification_confidence: 0.95,
  link_confidence: 'high',
  created_at: new Date().toISOString(),
  application_id: 10,
  opportunity_title: 'Software Engineer',
  opportunity_company: 'Acme',
  opportunity_status: 'applied',
  suggested_reply: 'Thank you for the invitation — I look forward to it!',
  action_taken: null,
  action_taken_at: null,
  ...overrides,
});

const mockStats: MessageStats = {
  total: 9,
  by_classification: {
    interview_invite: 4,
    rejection: 2,
    screening_question: 0,
    follow_up: 0,
    offer: 0,
    generic: 0,
    unclassified: 1,
  },
  linked: 6,
  unlinked: 3,
};

const healthyEvent: IntegrationHealthEvent = {
  id: 1,
  integration_name: 'gmail_response_poller',
  event_type: 'poll_success',
  detail: 'ok',
  occurred_at: new Date(Date.now() - 60_000).toISOString(),
};

const messagesResponse = (items: RecruiterMessage[], total?: number) => ({
  items,
  total: total ?? items.length,
  limit: 50,
  offset: 0,
});

const approveResponse = (
  message: RecruiterMessage,
  overrides: Partial<ApproveReplyResponse> = {}
): ApproveReplyResponse => ({
  message,
  opportunity_id: 10,
  opportunity_status: 'interview',
  draft_id: 'draft-abc',
  detail: 'ok',
  ...overrides,
});

const ackResponse = (
  message: RecruiterMessage,
  overrides: Partial<AcknowledgeMessageResponse> = {}
): AcknowledgeMessageResponse => ({
  message,
  opportunity_id: 10,
  opportunity_status: 'rejected_by_recruiter',
  detail: 'ok',
  ...overrides,
});

describe('ResponseCenterPlaceholder', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.getMessages).mockResolvedValue(messagesResponse([]));
    vi.mocked(api.getMessageStats).mockResolvedValue(mockStats);
    vi.mocked(api.getIntegrationHealth).mockResolvedValue({ items: [], total: 0 });
  });

  // ── Data loading, empty, error, health ─────────────────────────────────

  it('renders the page header immediately and fetches messages, stats, and health on mount', async () => {
    let resolveMessages!: (value: { items: RecruiterMessage[]; total: number; limit: number; offset: number }) => void;
    vi.mocked(api.getMessages).mockImplementationOnce(
      () =>
        new Promise((res) => {
          resolveMessages = res;
        })
    );

    render(<ResponseCenterPlaceholder />);

    // Header is visible while the fetch is still pending...
    expect(screen.getByText('Response Center')).toBeInTheDocument();
    // ...and the empty state is NOT shown prematurely during loading.
    expect(screen.queryByText('No messages yet')).not.toBeInTheDocument();

    await act(async () => {
      resolveMessages(messagesResponse([]));
    });

    await waitFor(() => {
      expect(api.getMessages).toHaveBeenCalledWith({ classification: undefined, limit: 50 });
      expect(api.getMessageStats).toHaveBeenCalled();
      expect(api.getIntegrationHealth).toHaveBeenCalledWith('gmail_response_poller', 5);
    });
  });

  it('shows the empty state when no recruiter messages exist', async () => {
    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('No messages yet')).toBeInTheDocument();
      expect(
        screen.getByText(/When the response poller detects recruiter replies/)
      ).toBeInTheDocument();
    });
  });

  it('renders the stats row with totals, classified, linked, and unlinked counts', async () => {
    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      // Total Messages = 9, Classified = 9 - 1 (unclassified) = 8, Linked = 6, Unlinked = 3
      expect(screen.getByText('9')).toBeInTheDocument();
      expect(screen.getByText('8')).toBeInTheDocument();
      expect(screen.getByText('6')).toBeInTheDocument();
      expect(screen.getByText('3')).toBeInTheDocument();
      expect(screen.getByText('Total Messages')).toBeInTheDocument();
      expect(screen.getByText('Classified')).toBeInTheDocument();
      expect(screen.getByText('Linked')).toBeInTheDocument();
      expect(screen.getByText('Unlinked')).toBeInTheDocument();
    });
  });

  it('shows an error banner when the initial load fails', async () => {
    vi.mocked(api.getMessages).mockRejectedValueOnce(new Error('Backend unreachable'));

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Backend unreachable')).toBeInTheDocument();
    });
    expect(screen.queryByText('No messages yet')).not.toBeInTheDocument();
  });

  it('shows a healthy Gmail poller indicator after a recent successful poll', async () => {
    vi.mocked(api.getIntegrationHealth).mockResolvedValueOnce({ items: [healthyEvent], total: 1 });

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Gmail Poller: Online')).toBeInTheDocument();
    });
  });

  it('shows "No data" for the Gmail poller when no health events exist', async () => {
    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Gmail Poller: No data')).toBeInTheDocument();
    });
  });

  // ── Message rendering & filters ───────────────────────────────────────

  it('renders a recruiter message with classification, sender, subject, and linked opportunity', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(messagesResponse([makeMessage()]));

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('acme.com')).toBeInTheDocument();
      expect(screen.getByText('Interview invitation')).toBeInTheDocument();
      // The classification badge 'Interview' renders alongside the identically-labelled filter chip.
      expect(screen.getAllByText('Interview').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('Acme')).toBeInTheDocument();
      expect(screen.getByText(/Status: applied/)).toBeInTheDocument();
    });
  });

  it('shows the LLM badge for llm-classified messages', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(
      messagesResponse([makeMessage({ classification_source: 'llm' })])
    );

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('LLM')).toBeInTheDocument();
    });
  });

  it('expands a message to reveal body preview and sender details', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(messagesResponse([makeMessage()]));

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Interview invitation')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Interview invitation'));

    await waitFor(() => {
      expect(screen.getByText('We would like to schedule an interview next week.')).toBeInTheDocument();
      expect(screen.getByText('recruiter@acme.com')).toBeInTheDocument();
      expect(screen.getByText('Software Engineer — Acme')).toBeInTheDocument();
    });
  });

  it('refetches with the selected classification when a filter chip is clicked', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(messagesResponse([makeMessage()]));

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Interview invitation')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Rejection/ }));

    await waitFor(() => {
      expect(api.getMessages).toHaveBeenLastCalledWith({
        classification: 'rejection',
        limit: 50,
      });
    });
  });

  it('shows a pagination hint when the server total exceeds the loaded messages', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(
      messagesResponse([makeMessage(), makeMessage({ id: 2, gmail_id: 'gmail-2' })], 5)
    );

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Showing 2 of 5 messages')).toBeInTheDocument();
    });
  });

  // ── Safety gate: rendering a draft must never send or submit ──────────

  it('renders the suggested reply in an editable textarea without calling any send/submit API', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(messagesResponse([makeMessage()]));

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Interview invitation')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Interview invitation'));

    await waitFor(() => {
      const textarea = screen.getByRole('textbox') as HTMLTextAreaElement;
      expect(textarea.value).toBe('Thank you for the invitation — I look forward to it!');
      expect(screen.getByText(/Hard Invariant: System only drafts/)).toBeInTheDocument();
    });

    // Displaying the draft is NOT an action: nothing was sent, approved, or acknowledged.
    expect(api.approveReply).not.toHaveBeenCalled();
    expect(api.draftReply).not.toHaveBeenCalled();
    expect(api.acknowledgeMessage).not.toHaveBeenCalled();
  });

  // ── Reply-bearing flow: approve / re-draft / edit ─────────────────────

  it('creates a Gmail draft only after the explicit Approve click, passing the suggested text', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(messagesResponse([makeMessage()]));
    vi.mocked(api.approveReply).mockResolvedValueOnce(
      approveResponse(makeMessage({ action_taken: 'approved' }))
    );

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Interview invitation')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Interview invitation'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Approve & create draft/ })).toBeInTheDocument();
    });

    // No API call happens before the explicit human approval.
    expect(api.approveReply).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /Approve & create draft/ }));

    await waitFor(() => {
      expect(api.approveReply).toHaveBeenCalledWith(1, 'Thank you for the invitation — I look forward to it!');
      expect(screen.getByText('Draft ready in your Gmail — go send it!')).toBeInTheDocument();
      expect(screen.getByText(/Draft ID: draft-abc/)).toBeInTheDocument();
      // Success copy reinforces that the system only drafts and never sends automatically.
      expect(
        screen.getByText(/The system never sends emails automatically; please review in Gmail and send/)
      ).toBeInTheDocument();
    });
  });

  it('approves the edited reply text, not the original suggestion', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(messagesResponse([makeMessage()]));
    vi.mocked(api.approveReply).mockResolvedValueOnce(
      approveResponse(makeMessage({ action_taken: 'approved' }))
    );

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Interview invitation')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Interview invitation'));

    await waitFor(() => {
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'My hand-written custom reply' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Approve & create draft/ }));

    await waitFor(() => {
      expect(api.approveReply).toHaveBeenCalledWith(1, 'My hand-written custom reply');
    });
  });

  it('re-drafts with Gemini via api.draftReply and updates the textarea without approving', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(messagesResponse([makeMessage()]));
    vi.mocked(api.draftReply).mockResolvedValueOnce(
      makeMessage({ suggested_reply: 'A fresh AI-generated draft.' })
    );

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Interview invitation')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Interview invitation'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Re-draft with Gemini/ })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Re-draft with Gemini/ }));

    await waitFor(() => {
      expect(api.draftReply).toHaveBeenCalledWith(1);
      expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
        'A fresh AI-generated draft.'
      );
    });
    // Re-drafting only drafts: it must never approve or send.
    expect(api.approveReply).not.toHaveBeenCalled();
  });

  it('offers "Draft reply with Gemini" (no textarea) for reply-bearing messages without a suggestion', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(
      messagesResponse([
        makeMessage({
          suggested_reply: null,
          classification: 'follow_up',
          subject: 'Following up on your application',
        }),
      ])
    );

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Following up on your application')).toBeInTheDocument();
      // The classification badge 'Follow-up' renders alongside the identically-labelled filter chip.
      expect(screen.getAllByText('Follow-up').length).toBeGreaterThanOrEqual(1);
    });
    fireEvent.click(screen.getByText('Following up on your application'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Draft reply with Gemini/ })).toBeInTheDocument();
      expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    });
  });

  it('shows an error banner and keeps the message actionable when approving fails', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(messagesResponse([makeMessage()]));
    vi.mocked(api.approveReply).mockRejectedValueOnce(new Error('Gmail token expired'));

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Interview invitation')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Interview invitation'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Approve & create draft/ })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Approve & create draft/ }));

    await waitFor(() => {
      expect(screen.getByText('Gmail token expired')).toBeInTheDocument();
    });
    // The approval controls remain available for retry — nothing was auto-submitted.
    expect(screen.getByRole('button', { name: /Approve & create draft/ })).toBeInTheDocument();
  });

  it('disables the action buttons while an approval is in flight', async () => {
    let resolveApprove!: (value: ApproveReplyResponse) => void;
    vi.mocked(api.getMessages).mockResolvedValueOnce(messagesResponse([makeMessage()]));
    vi.mocked(api.approveReply).mockImplementationOnce(
      () =>
        new Promise((res) => {
          resolveApprove = res;
        })
    );

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Interview invitation')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Interview invitation'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Approve & create draft/ })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Approve & create draft/ }));

    // In-flight state: approve button is disabled and shows progress copy.
    expect(screen.getByRole('button', { name: /Creating Gmail draft/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Re-draft with Gemini/ })).toBeDisabled();

    await act(async () => {
      resolveApprove(approveResponse(makeMessage({ action_taken: 'approved' })));
    });

    await waitFor(() => {
      expect(screen.getByText('Draft ready in your Gmail — go send it!')).toBeInTheDocument();
    });
  });

  // ── Unlinked messages ─────────────────────────────────────────────────

  it('blocks reply actions for unlinked messages with a warning', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(
      messagesResponse([
        makeMessage({ application_id: null, link_confidence: 'none', suggested_reply: null }),
      ])
    );

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('acme.com')).toBeInTheDocument();
      // 'Unlinked' appears both as the message link state and as a stats-row label.
      expect(screen.getAllByText('Unlinked').length).toBeGreaterThanOrEqual(1);
    });
    fireEvent.click(screen.getByText('acme.com'));

    await waitFor(() => {
      expect(screen.getByText(/Unlinked message\. Link this email to an application record/)).toBeInTheDocument();
    });
    // No reply-bearing controls may be offered for an unlinked message.
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Approve & create draft/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Draft reply with Gemini/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Acknowledge/ })).not.toBeInTheDocument();
  });

  // ── Terminal / non-reply flow ─────────────────────────────────────────

  it('offers only Acknowledge for rejection messages and calls api.acknowledgeMessage on click', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(
      messagesResponse([
        makeMessage({
          classification: 'rejection',
          suggested_reply: null,
          subject: 'Application status update',
        }),
      ])
    );
    vi.mocked(api.acknowledgeMessage).mockResolvedValueOnce(
      ackResponse(
        makeMessage({ classification: 'rejection', suggested_reply: null, action_taken: 'acknowledged' })
      )
    );

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('Application status update')).toBeInTheDocument();
      // The classification badge 'Rejection' renders alongside the identically-labelled filter chip.
      expect(screen.getAllByText('Rejection').length).toBeGreaterThanOrEqual(1);
    });
    fireEvent.click(screen.getByText('Application status update'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Acknowledge' })).toBeInTheDocument();
    });
    // A rejection is terminal: no reply-drafting or approval path is offered.
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Acknowledge' }));

    await waitFor(() => {
      expect(api.acknowledgeMessage).toHaveBeenCalledWith(1);
      expect(screen.getByText('Message acknowledged.')).toBeInTheDocument();
    });
  });

  // ── Already-actioned states ───────────────────────────────────────────

  it('shows the draft-ready state (no approval button) for an already-approved message', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(
      messagesResponse([makeMessage({ action_taken: 'approved' })])
    );

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('acme.com')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('acme.com'));

    await waitFor(() => {
      expect(screen.getByText(/Draft ready in your Gmail — go send it/)).toBeInTheDocument();
      expect(screen.getByText(/The system never sends emails automatically/)).toBeInTheDocument();
      // Sender is rendered inside a <strong>, so match it as its own element.
      expect(screen.getAllByText('recruiter@acme.com').length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.queryByRole('button', { name: /Approve & create draft/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Re-draft with Gemini/ })).not.toBeInTheDocument();
  });

  it('shows the acknowledged state (no action buttons) for an already-acknowledged message', async () => {
    vi.mocked(api.getMessages).mockResolvedValueOnce(
      messagesResponse([
        makeMessage({
          classification: 'rejection',
          suggested_reply: null,
          action_taken: 'acknowledged',
          opportunity_status: 'rejected_by_recruiter',
        }),
      ])
    );

    render(<ResponseCenterPlaceholder />);

    await waitFor(() => {
      expect(screen.getByText('acme.com')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('acme.com'));

    await waitFor(() => {
      // The status is wrapped in a <strong>, splitting the sentence across elements.
      expect(screen.getByText(/Message acknowledged\. Application status updated to/)).toBeInTheDocument();
      expect(screen.getByText('rejected by recruiter')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /Acknowledge/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument();
  });

  // ── Refresh behavior ─────────────────────────────────────────────────

  it('refetches messages, stats, and health when the Refresh button is clicked', async () => {
    render(<ResponseCenterPage />);

    await waitFor(() => {
      expect(screen.getByText('Response Center')).toBeInTheDocument();
    });

    const refreshButton = screen.getByRole('button', { name: /Refresh/i });
    fireEvent.click(refreshButton);

    await waitFor(() => {
      expect(api.getMessages).toHaveBeenCalledTimes(2);
      expect(api.getMessageStats).toHaveBeenCalledTimes(2);
      expect(api.getIntegrationHealth).toHaveBeenCalledTimes(2);
    });
  });
});