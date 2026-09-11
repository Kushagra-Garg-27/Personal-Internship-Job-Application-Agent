import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SubmissionReviewModal } from './SubmissionReviewModal';
import { api } from '../../api/client';
import type { DashboardOpportunityItem, ApplicationResponse } from '../../types';
import { HUMAN_SUBMISSION_APPROVAL_TOKEN } from '../../types';

vi.mock('../../api/client', () => ({
  api: {
    listApplications: vi.fn(),
    confirmSubmitApplication: vi.fn(),
  },
}));

const mockOpportunity: DashboardOpportunityItem = {
  id: 42,
  dedup_hash: 'hash-42',
  status: 'awaiting_submission',
  reliability_tier: 'experimental',
  title: 'Full Stack Engineer',
  company: 'Unstop Partner Labs',
  location: 'Remote, India',
  discovered_at: '2026-09-01T10:00:00Z',
  available_resumes: [
    { id: 11, version: 2, original_filename: 'resume_real.pdf', is_active: true },
  ],
};

const mockApplication: ApplicationResponse = {
  id: 99,
  opportunity_id: 42,
  resume_id: 11,
  attempt_number: 1,
  status: 'form_filled',
  adapter_name: 'unstop',
  notes: JSON.stringify({
    adapter: 'unstop',
    tier: 'experimental',
    custom_answers: [
      {
        question_id: 'q1',
        question_text: 'Why should we hire you?',
        label: 'Why should we hire you?',
        answer: 'Strong expertise in React, FastAPI, and robust distributed systems.',
        is_ai_draft: true,
      },
    ],
    filled_at: '2026-09-11T12:00:00Z',
  }),
  created_at: '2026-09-11T11:59:00Z',
  updated_at: '2026-09-11T12:00:00Z',
};

describe('SubmissionReviewModal (U4 Human Submission Gate)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not render when isOpen is false', () => {
    const { container } = render(
      <SubmissionReviewModal
        opportunity={mockOpportunity}
        isOpen={false}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it('1. Displays an awaiting_submission application and loads application details', async () => {
    vi.mocked(api.listApplications).mockResolvedValueOnce([mockApplication]);

    render(
      <SubmissionReviewModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />
    );

    expect(screen.getByText('Review & Confirm Application Submission')).toBeInTheDocument();
    expect(screen.getByText('Full Stack Engineer')).toBeInTheDocument();
    expect(screen.getByText(/Unstop Partner Labs/)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Prepared Form Data & Custom Answers')).toBeInTheDocument();
      expect(screen.getByText('Why should we hire you?')).toBeInTheDocument();
      expect(
        screen.getByText('Strong expertise in React, FastAPI, and robust distributed systems.')
      ).toBeInTheDocument();
      expect(screen.getByText('AI-Drafted Answer (Gemini)')).toBeInTheDocument();
    });
  });

  it('2. The final submission action is clearly presented as a human approval action', async () => {
    vi.mocked(api.listApplications).mockResolvedValueOnce([mockApplication]);

    render(
      <SubmissionReviewModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Irreversible Submission Boundary')).toBeInTheDocument();
      expect(
        screen.getByText('I have reviewed the prepared application and confirm this submission.')
      ).toBeInTheDocument();
      expect(screen.getByText('Confirm & Submit Application')).toBeInTheDocument();
      expect(screen.getByText('2. Human Review')).toBeInTheDocument();
      expect(screen.getByText('Current active gate')).toBeInTheDocument();
    });
  });

  it('3. Opening the review UI does NOT call confirm-submit', async () => {
    vi.mocked(api.listApplications).mockResolvedValueOnce([mockApplication]);

    render(
      <SubmissionReviewModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(api.listApplications).toHaveBeenCalledWith(42);
    });

    // confirmSubmitApplication must NOT have been called merely by opening the modal
    expect(api.confirmSubmitApplication).not.toHaveBeenCalled();
  });

  it('4. Submission requires the explicit confirmation action (checkbox gate)', async () => {
    vi.mocked(api.listApplications).mockResolvedValueOnce([mockApplication]);

    render(
      <SubmissionReviewModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Confirm & Submit Application')).toBeInTheDocument();
    });

    const submitBtn = screen.getByRole('button', { name: /Confirm & Submit Application/i });
    const checkbox = screen.getByRole('checkbox', {
      name: /I have reviewed the prepared application and confirm this submission/i,
    });

    // Button should be disabled initially when checkbox is unchecked
    expect((checkbox as HTMLInputElement).checked).toBe(false);
    expect(submitBtn).toBeDisabled();

    // Clicking button while disabled does nothing
    fireEvent.click(submitBtn);
    expect(api.confirmSubmitApplication).not.toHaveBeenCalled();

    // Check the box
    fireEvent.click(checkbox);
    expect((checkbox as HTMLInputElement).checked).toBe(true);
    expect(submitBtn).not.toBeDisabled();
  });

  it('5 & 6. Sends the correct application ID and required human approval token', async () => {
    vi.mocked(api.listApplications).mockResolvedValueOnce([mockApplication]);
    vi.mocked(api.confirmSubmitApplication).mockResolvedValueOnce({
      success: true,
      status: 'applied',
      confirmed: true,
      confirmation_ref: 'UNSTOP-CONFIRMED-99',
    });

    const onSubmitted = vi.fn();
    const onClose = vi.fn();

    render(
      <SubmissionReviewModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={onClose}
        onSubmitted={onSubmitted}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Confirm & Submit Application')).toBeInTheDocument();
    });

    const checkbox = screen.getByRole('checkbox', {
      name: /I have reviewed the prepared application and confirm this submission/i,
    });
    fireEvent.click(checkbox);

    const submitBtn = screen.getByRole('button', { name: /Confirm & Submit Application/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.confirmSubmitApplication).toHaveBeenCalledTimes(1);
      // 5. Correct application ID (99)
      // 6. Required human approval token (HUMAN_CONFIRMED_SUBMIT)
      expect(api.confirmSubmitApplication).toHaveBeenCalledWith(
        99,
        expect.objectContaining({
          approval_token: HUMAN_SUBMISSION_APPROVAL_TOKEN,
          approved_by: 'human_user',
          platform_confirmed: true,
        })
      );
    });
  });

  it('7. Successful confirmation updates the UI appropriately and calls onSubmitted', async () => {
    vi.mocked(api.listApplications).mockResolvedValueOnce([mockApplication]);
    const mockSuccessResponse = {
      success: true,
      status: 'applied',
      confirmed: true,
      confirmation_ref: 'UNSTOP-CONFIRMED-99',
    };
    vi.mocked(api.confirmSubmitApplication).mockResolvedValueOnce(mockSuccessResponse);

    const onSubmitted = vi.fn();
    const onClose = vi.fn();

    render(
      <SubmissionReviewModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={onClose}
        onSubmitted={onSubmitted}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole('checkbox')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /Confirm & Submit Application/i }));

    await waitFor(() => {
      expect(onSubmitted).toHaveBeenCalledWith(mockSuccessResponse);
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('8a. Failed confirmation with rejection reason shows an actionable error and does not call onSubmitted', async () => {
    vi.mocked(api.listApplications).mockResolvedValueOnce([mockApplication]);
    vi.mocked(api.confirmSubmitApplication).mockResolvedValueOnce({
      success: false,
      status: 'unconfirmed',
      reason: 'Platform confirmation not observed after human-approved submission.',
    });

    const onSubmitted = vi.fn();
    const onClose = vi.fn();

    render(
      <SubmissionReviewModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={onClose}
        onSubmitted={onSubmitted}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole('checkbox')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /Confirm & Submit Application/i }));

    await waitFor(() => {
      expect(
        screen.getByText('Platform confirmation not observed after human-approved submission.')
      ).toBeInTheDocument();
      // Must not falsely mark as submitted
      expect(onSubmitted).not.toHaveBeenCalled();
      expect(onClose).not.toHaveBeenCalled();
    });
  });

  it('8b. Failed confirmation with API HTTP error shows error message and does not call onSubmitted', async () => {
    vi.mocked(api.listApplications).mockResolvedValueOnce([mockApplication]);
    vi.mocked(api.confirmSubmitApplication).mockRejectedValueOnce(
      new Error('Explicit human approval is required before submission.')
    );

    const onSubmitted = vi.fn();
    const onClose = vi.fn();

    render(
      <SubmissionReviewModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={onClose}
        onSubmitted={onSubmitted}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole('checkbox')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /Confirm & Submit Application/i }));

    await waitFor(() => {
      expect(
        screen.getByText('Explicit human approval is required before submission.')
      ).toBeInTheDocument();
      expect(onSubmitted).not.toHaveBeenCalled();
      expect(onClose).not.toHaveBeenCalled();
    });
  });

  it('9. The action is disabled while submission is in flight', async () => {
    vi.mocked(api.listApplications).mockResolvedValueOnce([mockApplication]);
    let resolveSubmit: (val: any) => void;
    const pendingPromise = new Promise((resolve) => {
      resolveSubmit = resolve;
    });
    vi.mocked(api.confirmSubmitApplication).mockReturnValueOnce(pendingPromise as any);

    render(
      <SubmissionReviewModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole('checkbox')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox'));
    const submitBtn = screen.getByRole('button', { name: /Confirm & Submit Application/i });

    fireEvent.click(submitBtn);

    // In-flight state
    expect(screen.getByText('Submitting Application...')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Submitting Application.../i })).toBeDisabled();

    // Resolve promise and await completion
    resolveSubmit!({ success: true, status: 'applied' });
    await waitFor(() => {
      expect(screen.queryByText('Submitting Application...')).not.toBeInTheDocument();
    });
  });

  it('10. Already-submitted applications cannot be submitted again through this UI', async () => {
    const submittedApp: ApplicationResponse = {
      ...mockApplication,
      status: 'submitted',
      confirmation_ref: 'UNSTOP-CONFIRMED-99',
      submitted_at: '2026-09-11T12:05:00Z',
    };
    vi.mocked(api.listApplications).mockResolvedValueOnce([submittedApp]);

    render(
      <SubmissionReviewModal
        opportunity={mockOpportunity}
        isOpen={true}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Application Already Submitted')).toBeInTheDocument();
      expect(screen.getByText(/Confirmation Reference: UNSTOP-CONFIRMED-99/)).toBeInTheDocument();
      // Submit action button must not be available
      expect(screen.queryByRole('button', { name: /Confirm & Submit Application/i })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
    });
  });
});
