import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { OpportunityCard } from './OpportunityCard';
import type { DashboardOpportunityItem } from '../../types';

const mockOpportunity: DashboardOpportunityItem = {
  id: 101,
  dedup_hash: 'hash-101',
  status: 'recommended',
  reliability_tier: 'stable',
  source: 'greenhouse',
  title: 'Senior Frontend Engineer',
  company: 'Acme Corp',
  description: 'Looking for a senior React engineer.',
  location: 'Remote, US',
  salary_min: 140000,
  salary_max: 180000,
  discovered_at: '2026-09-01T12:00:00Z',
  relevance_score: 0.88,
  relevance_explanation: {
    top_skills: ['React', 'TypeScript', 'GraphQL'],
    matched_skills: ['React', 'TypeScript'],
    match_summary: 'Strong alignment with React and modern frontend systems.',
  },
  available_resumes: [
    { id: 1, version: 1, original_filename: 'resume_v1.pdf', is_active: true },
  ],
};

describe('OpportunityCard', () => {
  it('renders opportunity details, badges, and score', () => {
    const onInspect = vi.fn();
    const onApprove = vi.fn();
    const onDismiss = vi.fn();

    render(
      <OpportunityCard
        opportunity={mockOpportunity}
        onInspect={onInspect}
        onApprove={onApprove}
        onDismiss={onDismiss}
      />
    );
    expect(screen.getByText('Senior Frontend Engineer')).toBeInTheDocument();
    expect(screen.getByText('Acme Corp')).toBeInTheDocument();
    expect(screen.getByText('Stable Source')).toBeInTheDocument();
    expect(screen.getByText('Recommended')).toBeInTheDocument();
    expect(screen.getByText('88% Match')).toBeInTheDocument();
    expect(screen.getByText('Remote, US')).toBeInTheDocument();
    expect(screen.getByText('$140,000 - $180,000')).toBeInTheDocument();
    expect(screen.getByText('Strong alignment with React and modern frontend systems.')).toBeInTheDocument();
  });

  it('renders discovery_only badge correctly', () => {
    const discoveryOpp = {
      ...mockOpportunity,
      reliability_tier: 'discovery_only' as const,
    };

    render(
      <OpportunityCard
        opportunity={discoveryOpp}
        onInspect={vi.fn()}
        onApprove={vi.fn()}
        onDismiss={vi.fn()}
      />
    );

    expect(screen.getByText('Discovery Only')).toBeInTheDocument();
  });

  it('triggers onInspect when title or inspect button is clicked', () => {
    const onInspect = vi.fn();
    render(
      <OpportunityCard
        opportunity={mockOpportunity}
        onInspect={onInspect}
        onApprove={vi.fn()}
        onDismiss={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText('Senior Frontend Engineer'));
    expect(onInspect).toHaveBeenCalledWith(mockOpportunity);

    fireEvent.click(screen.getByText('Inspect Fit'));
    expect(onInspect).toHaveBeenCalledTimes(2);
  });

  it('triggers onApprove and onDismiss on action buttons', () => {
    const onApprove = vi.fn();
    const onDismiss = vi.fn();

    render(
      <OpportunityCard
        opportunity={mockOpportunity}
        onInspect={vi.fn()}
        onApprove={onApprove}
        onDismiss={onDismiss}
      />
    );

    fireEvent.click(screen.getByText('Approve'));
    expect(onApprove).toHaveBeenCalledWith(mockOpportunity);

    fireEvent.click(screen.getByText('Dismiss'));
    expect(onDismiss).toHaveBeenCalledWith(mockOpportunity);
  });

  it('displays approved indicator when status is ready_to_apply', () => {
    const readyOpp = {
      ...mockOpportunity,
      status: 'ready_to_apply' as const,
    };

    render(
      <OpportunityCard
        opportunity={readyOpp}
        onInspect={vi.fn()}
        onApprove={vi.fn()}
        onDismiss={vi.fn()}
      />
    );

    expect(screen.getByText('Ready for Phase 9 Worker')).toBeInTheDocument();
    expect(screen.queryByText('Approve')).not.toBeInTheDocument();
  });
});
