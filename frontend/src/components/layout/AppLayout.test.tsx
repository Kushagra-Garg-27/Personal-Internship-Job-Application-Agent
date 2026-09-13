import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AppLayout } from './AppLayout';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getPendingScamReviews: vi.fn(),
    getPendingQueue: vi.fn(),
  },
}));

describe('AppLayout - Pending Submission Queue Badge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('10. Pending-count badge displays correct count when pending items exist', async () => {
    vi.mocked(api.getPendingScamReviews).mockResolvedValueOnce([]);
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce([
      {
        application_id: 1,
        opportunity_id: 10,
        application_status: 'form_filled',
        queue_state: 'APPROVED_PENDING',
      },
      {
        application_id: 2,
        opportunity_id: 20,
        application_status: 'form_filled',
        queue_state: 'APPROVED_PENDING',
      },
    ] as any);

    render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>
    );

    expect(screen.getByText('Submission Queue')).toBeInTheDocument();

    await waitFor(() => {
      // Badge displaying count 2
      expect(screen.getByText('2')).toBeInTheDocument();
    });
  });

  it('10b. Pending-count badge handles API failure safely without crashing or showing error badge', async () => {
    vi.mocked(api.getPendingScamReviews).mockRejectedValueOnce(new Error('Network failure'));
    vi.mocked(api.getPendingQueue).mockRejectedValueOnce(new Error('Backend offline'));

    render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>
    );

    // Navigation renders normally
    expect(screen.getByText('Submission Queue')).toBeInTheDocument();
    expect(screen.getByText('Career Intelligence Core')).toBeInTheDocument();

    // No broken badge is displayed
    await waitFor(() => {
      expect(screen.queryByText('NaN')).not.toBeInTheDocument();
      expect(screen.queryByText('undefined')).not.toBeInTheDocument();
    });
  });

  it('10c. Polling timer cleanup occurs when AppLayout unmounts', async () => {
    vi.mocked(api.getPendingScamReviews).mockResolvedValueOnce([]);
    vi.mocked(api.getPendingQueue).mockResolvedValueOnce([]);
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval');

    const { unmount } = render(
      <MemoryRouter>
        <AppLayout />
      </MemoryRouter>
    );

    unmount();

    expect(clearIntervalSpy).toHaveBeenCalled();
  });
});
