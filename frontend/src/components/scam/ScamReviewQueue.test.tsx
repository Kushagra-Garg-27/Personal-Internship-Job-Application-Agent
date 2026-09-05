import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ScamReviewQueue } from './ScamReviewQueue';
import { api } from '../../api/client';
import type { DashboardOpportunityItem } from '../../types';

vi.mock('../../api/client', () => ({
  api: {
    getPendingScamReviews: vi.fn(),
    clearScamReview: vi.fn(),
    rejectScamReview: vi.fn(),
  },
}));

const mockScamItem: DashboardOpportunityItem = {
  id: 77,
  dedup_hash: 'hash-77',
  status: 'scam_review_pending',
  reliability_tier: 'discovery_only',
  source: 'gmail_alert',
  title: 'Executive Assistant - Immediate Start',
  company: 'Global Wealth Partners',
  description: 'Telegram interview required. Wire equipment deposit upon offer.',
  discovered_at: '2026-09-02T10:00:00Z',
  scam_verdict: 'ambiguous',
  llm_verdict: 'suspicious',
  llm_reasoning: 'Mentions Telegram interviews and equipment deposit payment.',
  available_resumes: [],
};

describe('ScamReviewQueue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders empty queue message when no items pending', async () => {
    vi.mocked(api.getPendingScamReviews).mockResolvedValueOnce([]);

    render(<ScamReviewQueue />);

    await waitFor(() => {
      expect(screen.getByText('Queue Clean — Zero Ambiguous Cases')).toBeInTheDocument();
    });
  });

  it('renders pending scam review items with reasoning', async () => {
    vi.mocked(api.getPendingScamReviews).mockResolvedValueOnce([mockScamItem]);

    render(<ScamReviewQueue />);

    await waitFor(() => {
      expect(screen.getByText('Executive Assistant - Immediate Start')).toBeInTheDocument();
      expect(screen.getByText('Global Wealth Partners')).toBeInTheDocument();
      expect(screen.getByText(/Mentions Telegram interviews and equipment deposit/)).toBeInTheDocument();
      expect(screen.getByText('Clear Listing (Proceed to Funnel)')).toBeInTheDocument();
      expect(screen.getByText('Reject as Scam')).toBeInTheDocument();
    });
  });

  it('clearing an item calls api.clearScamReview and updates queue', async () => {
    vi.mocked(api.getPendingScamReviews).mockResolvedValueOnce([mockScamItem]);
    vi.mocked(api.clearScamReview).mockResolvedValueOnce({ status: 'in_funnel' });

    render(<ScamReviewQueue />);

    await waitFor(() => {
      expect(screen.getByText('Executive Assistant - Immediate Start')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Clear Listing (Proceed to Funnel)'));

    await waitFor(() => {
      expect(api.clearScamReview).toHaveBeenCalledWith(77);
      expect(screen.getByText(/cleared! Listing returned to funnel/)).toBeInTheDocument();
      expect(screen.queryByText('Executive Assistant - Immediate Start')).not.toBeInTheDocument();
    });
  });

  it('rejecting an item calls api.rejectScamReview and updates queue', async () => {
    vi.mocked(api.getPendingScamReviews).mockResolvedValueOnce([mockScamItem]);
    vi.mocked(api.rejectScamReview).mockResolvedValueOnce({ status: 'scam_risk_rejected' });

    render(<ScamReviewQueue />);

    await waitFor(() => {
      expect(screen.getByText('Executive Assistant - Immediate Start')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Reject as Scam'));

    await waitFor(() => {
      expect(api.rejectScamReview).toHaveBeenCalledWith(77, 'Confirmed scam during human review');
      expect(screen.getByText(/rejected as scam and content hash recorded/)).toBeInTheDocument();
      expect(screen.queryByText('Executive Assistant - Immediate Start')).not.toBeInTheDocument();
    });
  });
});
