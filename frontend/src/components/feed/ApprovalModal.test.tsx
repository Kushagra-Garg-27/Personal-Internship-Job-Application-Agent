import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ApprovalModal } from './ApprovalModal';
import { api } from '../../api/client';
import type { DashboardOpportunityItem } from '../../types';

vi.mock('../../api/client', () => ({
  api: {
    finalApprove: vi.fn(),
  },
}));

const mockOpportunity: DashboardOpportunityItem = {
  id: 42,
  dedup_hash: 'hash-42',
  status: 'recommended',
  reliability_tier: 'stable',
  title: 'Full Stack Tech Lead',
  company: 'Linear Labs',
  location: 'San Francisco, CA',
  discovered_at: '2026-09-01T10:00:00Z',
  available_resumes: [
    { id: 10, version: 1, original_filename: 'resume_general.pdf', is_active: false },
    { id: 11, version: 2, original_filename: 'resume_tech_lead.pdf', is_active: true },
  ],
};

describe('ApprovalModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not render when isOpen is false', () => {
    const { container } = render(
      <ApprovalModal
        opportunity={mockOpportunity}
        isOpen={false}
        onClose={vi.fn()}
        onApproved={vi.fn()}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders modal with target opportunity and preselects active resume', () => {
    render(
      <ApprovalModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={vi.fn()}
        onApproved={vi.fn()}
      />
    );

    expect(screen.getByText('Approve for Application')).toBeInTheDocument();
    expect(screen.getByText('Full Stack Tech Lead')).toBeInTheDocument();
    expect(screen.getByText(/Linear Labs/)).toBeInTheDocument();
    expect(screen.getByText('resume_tech_lead.pdf')).toBeInTheDocument();
    expect(screen.getByText('Active Default')).toBeInTheDocument();

    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(2);
    // Second radio corresponds to id 11 (active)
    expect((radios[1] as HTMLInputElement).checked).toBe(true);
    expect((radios[0] as HTMLInputElement).checked).toBe(false);
  });

  it('allows user to switch resume and calls api.finalApprove on submit', async () => {
    const onApproved = vi.fn();
    const onClose = vi.fn();
    vi.mocked(api.finalApprove).mockResolvedValueOnce({
      id: 42,
      status: 'ready_to_apply',
      selected_resume_id: 10,
    });

    render(
      <ApprovalModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={onClose}
        onApproved={onApproved}
      />
    );

    // Click first resume
    fireEvent.click(screen.getByText('resume_general.pdf'));

    const radios = screen.getAllByRole('radio');
    expect((radios[0] as HTMLInputElement).checked).toBe(true);

    // Click confirm
    fireEvent.click(screen.getByText('Confirm & Approve'));

    await waitFor(() => {
      expect(api.finalApprove).toHaveBeenCalledWith(42, 10);
      expect(onApproved).toHaveBeenCalledWith({
        id: 42,
        status: 'ready_to_apply',
        selected_resume_id: 10,
      });
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('displays error message if approval API fails', async () => {
    vi.mocked(api.finalApprove).mockRejectedValueOnce(new Error('Transition disallowed'));

    render(
      <ApprovalModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={vi.fn()}
        onApproved={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText('Confirm & Approve'));

    await waitFor(() => {
      expect(screen.getByText('Transition disallowed')).toBeInTheDocument();
    });
  });
});
