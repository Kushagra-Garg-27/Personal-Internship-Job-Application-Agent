import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { SubmissionQueuePage } from './SubmissionQueuePage';
import { api } from '../api/client';
import type { PendingQueueItem } from '../types';

vi.mock('../api/client', () => ({
  api: {
    getPendingQueue: vi.fn(),
    revokeApproval: vi.fn(),
  },
}));

const mockQueueItems: PendingQueueItem[] = [
  {
    application_id: 101,
    opportunity_id: 1,
    opportunity_title: 'Frontend Engineer',
    opportunity_company: 'Acme Corp',
    opportunity_url: 'https://example.com/job/1',
    application_status: 'form_filled',
    queue_state: 'APPROVED_PENDING',
    approved_at: '2026-09-14T01:00:00Z',
    approved_by: 'human_operator',
  },
  {
    application_id: 102,
    opportunity_id: 2,
    opportunity_title: 'Backend Developer',
    opportunity_company: 'Beta Systems',
    opportunity_url: 'https://example.com/job/2',
    application_status: 'form_filled',
    queue_state: 'CLAIMED_IN_PROGRESS',
    approved_at: '2026-09-14T01:05:00Z',
    approved_by: 'human_operator',
    claimed_at: '2026-09-14T01:06:00Z',
    claimed_by: 'worker-node-1',
  },
  {
    application_id: 103,
    opportunity_id: 3,
    opportunity_title: 'DevOps Specialist',
    opportunity_company: 'Gamma Cloud',
    opportunity_url: 'https://example.com/job/3',
    application_status: 'failed',
    queue_state: 'MANUAL_REVIEW',
    approved_at: '2026-09-14T01:10:00Z',
  },
  {
    application_id: 104,
    opportunity_id: 4,
    opportunity_title: 'Security Analyst',
    opportunity_company: 'Delta Cyber',
    opportunity_url: 'https://example.com/job/4',
    application_status: 'form_filled',
    queue_state: 'REVOKED',
    approval_revoked_at: '2026-09-14T01:15:00Z',
    approval_revoked_by: 'security_lead',
    approval_revocation_reason: 'Position closed by employer',
  },
  {
    application_id: 105,
    opportunity_id: 5,
    opportunity_title: 'Product Manager',
    opportunity_company: 'Epsilon Tech',
    opportunity_url: 'https://example.com/job/5',
    application_status: 'submitted',
    queue_state: 'SUBMITTED',
    approved_at: '2026-09-14T01:20:00Z',
    claimed_at: '2026-09-14T01:21:00Z',
    claimed_by: 'worker-node-2',
    confirmation_ref: 'EPSILON-REF-789',
  },
];

describe('SubmissionQueuePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('1. All five queue states render correctly with accurate badges and details', async () => {
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce(mockQueueItems);

    render(<SubmissionQueuePage />);

    await waitFor(() => {
      expect(screen.getByText('Frontend Engineer')).toBeInTheDocument();
    });

    // 1. APPROVED_PENDING
    expect(screen.getByText('Approved • Pending Worker')).toBeInTheDocument();
    expect(screen.getByText(/Acme Corp/)).toBeInTheDocument();

    // 2. CLAIMED_IN_PROGRESS
    expect(screen.getByText('Claimed • Executing')).toBeInTheDocument();
    expect(screen.getByText(/Beta Systems/)).toBeInTheDocument();
    expect(screen.getByText(/worker-node-1/)).toBeInTheDocument();

    // 3. MANUAL_REVIEW
    expect(screen.getByText('Manual Review Required')).toBeInTheDocument();
    expect(screen.getByText(/Gamma Cloud/)).toBeInTheDocument();

    // 4. REVOKED
    expect(screen.getByText('Revoked')).toBeInTheDocument();
    expect(screen.getByText(/Delta Cyber/)).toBeInTheDocument();
    expect(screen.getByText(/"Position closed by employer"/)).toBeInTheDocument();

    // 5. SUBMITTED (tab label + badge)
    expect(screen.getAllByText('Submitted').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Epsilon Tech/)).toBeInTheDocument();
    expect(screen.getByText('EPSILON-REF-789')).toBeInTheDocument();
  });

  it('2. Cancel appears only for APPROVED_PENDING', async () => {
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce(mockQueueItems);

    render(<SubmissionQueuePage />);

    await waitFor(() => {
      expect(screen.getByText('Frontend Engineer')).toBeInTheDocument();
    });

    const cancelButtons = screen.getAllByRole('button', { name: /Cancel Approval/i });
    // Exactly one Cancel Approval button exists (for App 101 which is APPROVED_PENDING)
    expect(cancelButtons).toHaveLength(1);
  });

  it('3. Claimed, manual-review, revoked, and submitted items cannot be canceled', async () => {
    // Render only the non-pending items
    const nonPendingItems = mockQueueItems.filter((i) => i.queue_state !== 'APPROVED_PENDING');
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce(nonPendingItems);

    render(<SubmissionQueuePage />);

    await waitFor(() => {
      expect(screen.getByText('Backend Developer')).toBeInTheDocument();
    });

    expect(screen.queryByRole('button', { name: /Cancel Approval/i })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Background worker is currently filling \/ submitting this form\. Cancellation is locked\./i)
    ).toBeInTheDocument();
  });

  it('4. Confirmation modal names the exact application and company', async () => {
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce(mockQueueItems);

    render(<SubmissionQueuePage />);

    await waitFor(() => {
      expect(screen.getByText('Frontend Engineer')).toBeInTheDocument();
    });

    const cancelButton = screen.getByRole('button', { name: /Cancel Approval/i });
    fireEvent.click(cancelButton);

    // Modal opens
    expect(screen.getByText('Cancel Pending Approval')).toBeInTheDocument();
    expect(screen.getAllByText('Frontend Engineer').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Acme Corp')).toBeInTheDocument();
    expect(screen.getByText(/Are you sure you want to cancel approval for/i)).toBeInTheDocument();
  });

  it('5. Declining confirmation sends no request and closes modal', async () => {
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce(mockQueueItems);

    render(<SubmissionQueuePage />);

    await waitFor(() => {
      expect(screen.getByText('Frontend Engineer')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Cancel Approval/i }));
    expect(screen.getByText('Cancel Pending Approval')).toBeInTheDocument();

    const keepButton = screen.getByRole('button', { name: /Keep Approval/i });
    fireEvent.click(keepButton);

    // Modal closed
    expect(screen.queryByText('Cancel Pending Approval')).not.toBeInTheDocument();
    // No revocation request made
    expect(api.revokeApproval).not.toHaveBeenCalled();
  });

  it('6. API failure does not optimistically mark the item revoked', async () => {
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce(mockQueueItems);
    vi.mocked(api.revokeApproval).mockRejectedValueOnce(
      new Error('Application 101 was claimed by a worker concurrently')
    );

    render(<SubmissionQueuePage />);

    await waitFor(() => {
      expect(screen.getByText('Frontend Engineer')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Cancel Approval/i }));

    const confirmButton = screen.getByRole('button', { name: /Confirm Cancellation/i });
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(
        screen.getByText(/Application 101 was claimed by a worker concurrently/i)
      ).toBeInTheDocument();
    });

    // Close the error dialog
    fireEvent.click(screen.getByRole('button', { name: /Keep Approval/i }));

    // Item 101 is NOT optimistically revoked — still shows Approved • Pending Worker
    expect(screen.getByText('Approved • Pending Worker')).toBeInTheDocument();
    expect(screen.getByText('Frontend Engineer')).toBeInTheDocument();
  });

  it('7. Successful cancellation refreshes server state', async () => {
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce(mockQueueItems);
    vi.mocked(api.revokeApproval).mockResolvedValueOnce({
      revoked: true,
      application_id: 101,
      revoked_by: 'human_operator',
      revoked_at: '2026-09-14T01:30:00Z',
    });

    const updatedQueueItems: PendingQueueItem[] = mockQueueItems.map((item) =>
      item.application_id === 101
        ? {
            ...item,
            queue_state: 'REVOKED',
            approval_revoked_at: '2026-09-14T01:30:00Z',
            approval_revoked_by: 'human_operator',
            approval_revocation_reason: 'Wrong resume',
          }
        : item
    );
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce(updatedQueueItems);

    render(<SubmissionQueuePage />);

    await waitFor(() => {
      expect(screen.getByText('Frontend Engineer')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Cancel Approval/i }));

    const textarea = screen.getByPlaceholderText(/Wrong resume selected/i);
    fireEvent.change(textarea, { target: { value: 'Wrong resume' } });

    fireEvent.click(screen.getByRole('button', { name: /Confirm Cancellation/i }));

    await waitFor(() => {
      expect(api.revokeApproval).toHaveBeenCalledWith(101, 'Wrong resume');
    });

    // Server queue was re-fetched
    expect(api.getPendingQueue).toHaveBeenCalledTimes(2);

    // Modal closed
    await waitFor(() => {
      expect(screen.queryByText('Cancel Pending Approval')).not.toBeInTheDocument();
    });
  });

  it('8. No automatic retry/release action exists', async () => {
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce(mockQueueItems);

    render(<SubmissionQueuePage />);

    await waitFor(() => {
      expect(screen.getByText('Frontend Engineer')).toBeInTheDocument();
    });

    // Ensure no automatic retry or release buttons exist anywhere
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /release/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /auto/i })).not.toBeInTheDocument();
  });

  it('9. Polling cleanup occurs when the component unmounts', async () => {
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce(mockQueueItems);
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval');

    const { unmount } = render(<SubmissionQueuePage />);

    await waitFor(() => {
      expect(screen.getByText('Frontend Engineer')).toBeInTheDocument();
    });

    unmount();

    expect(clearIntervalSpy).toHaveBeenCalled();
  });
});
