import React from 'react';
import { ShieldCheck, Compass, Sparkles } from 'lucide-react';
import type { ReliabilityTier, OpportunityStatus } from '../../types';

interface ReliabilityBadgeProps {
  tier: ReliabilityTier;
  className?: string;
}

export const ReliabilityBadge: React.FC<ReliabilityBadgeProps> = ({ tier, className = '' }) => {
  if (tier === 'stable') {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '5px',
          padding: '4px 10px',
          borderRadius: 'var(--radius-full)',
          fontSize: '0.72rem',
          fontWeight: 600,
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
          background: 'rgba(99, 102, 241, 0.14)',
          color: '#a5b4fc',
          border: '1px solid rgba(99, 102, 241, 0.32)',
          boxShadow: '0 0 12px rgba(99, 102, 241, 0.12)',
        }}
        className={className}
        title="Verified stable adapter with guaranteed schema conformity"
      >
        <ShieldCheck size={13} color="#818cf8" />
        Stable Source
      </span>
    );
  }

  if (tier === 'discovery_only') {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '5px',
          padding: '4px 10px',
          borderRadius: 'var(--radius-full)',
          fontSize: '0.72rem',
          fontWeight: 600,
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
          background: 'rgba(245, 158, 11, 0.12)',
          color: '#fcd34d',
          border: '1px solid rgba(245, 158, 11, 0.32)',
        }}
        className={className}
        title="Unverified raw discovery source (e.g. Gmail job alerts, broad aggregators)"
      >
        <Compass size={13} color="#fbbf24" />
        Discovery Only
      </span>
    );
  }

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '5px',
        padding: '4px 10px',
        borderRadius: 'var(--radius-full)',
        fontSize: '0.72rem',
        fontWeight: 600,
        letterSpacing: '0.04em',
        textTransform: 'uppercase',
        background: 'rgba(139, 92, 246, 0.12)',
        color: '#c4b5fd',
        border: '1px solid rgba(139, 92, 246, 0.28)',
      }}
      className={className}
    >
      <Sparkles size={13} color="#a78bfa" />
      Experimental
    </span>
  );
};

interface ScoreBadgeProps {
  score?: number | null;
  className?: string;
}

export const ScoreBadge: React.FC<ScoreBadgeProps> = ({ score, className = '' }) => {
  if (score === null || score === undefined) {
    return (
      <span
        style={{
          padding: '4px 10px',
          borderRadius: 'var(--radius-full)',
          fontSize: '0.75rem',
          fontWeight: 600,
          background: 'rgba(255, 255, 255, 0.05)',
          color: 'var(--text-tertiary)',
        }}
        className={className}
      >
        No Score
      </span>
    );
  }

  const pct = Math.round(score * 100);
  const isHigh = pct >= 80;
  const isMed = pct >= 60 && pct < 80;

  const bg = isHigh
    ? 'rgba(124, 58, 237, 0.2)'
    : isMed
    ? 'rgba(99, 102, 241, 0.16)'
    : 'rgba(255, 255, 255, 0.08)';

  const color = isHigh ? '#d8b4fe' : isMed ? '#a5b4fc' : 'var(--text-secondary)';
  const borderColor = isHigh ? 'rgba(168, 85, 247, 0.36)' : 'rgba(255, 255, 255, 0.1)';

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        padding: '4px 12px',
        borderRadius: 'var(--radius-full)',
        fontSize: '0.8rem',
        fontWeight: 700,
        letterSpacing: '0.02em',
        background: bg,
        color,
        border: `1px solid ${borderColor}`,
        boxShadow: isHigh ? '0 0 16px rgba(168, 85, 247, 0.18)' : 'none',
      }}
      className={className}
    >
      {pct}% Match
    </span>
  );
};

interface StatusBadgeProps {
  status: OpportunityStatus;
  className?: string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, className = '' }) => {
  const formatMap: Record<string, { label: string; color: string; bg: string; border: string }> = {
    recommended: {
      label: 'Recommended',
      color: '#c4b5fd',
      bg: 'rgba(139, 92, 246, 0.14)',
      border: 'rgba(139, 92, 246, 0.3)',
    },
    scam_review_pending: {
      label: 'Review Pending',
      color: '#fcd34d',
      bg: 'rgba(245, 158, 11, 0.14)',
      border: 'rgba(245, 158, 11, 0.32)',
    },
    ready_to_apply: {
      label: 'Ready to Apply',
      color: '#fdba74',
      bg: 'rgba(249, 115, 22, 0.16)',
      border: 'rgba(249, 115, 22, 0.35)',
    },
    dismissed: {
      label: 'Dismissed',
      color: 'var(--text-tertiary)',
      bg: 'rgba(255, 255, 255, 0.05)',
      border: 'rgba(255, 255, 255, 0.08)',
    },
    scam_risk_rejected: {
      label: 'Scam Rejected',
      color: '#f87171',
      bg: 'rgba(239, 68, 68, 0.14)',
      border: 'rgba(239, 68, 68, 0.3)',
    },
    ineligible: {
      label: 'Ineligible',
      color: '#9ca3af',
      bg: 'rgba(156, 163, 175, 0.1)',
      border: 'rgba(156, 163, 175, 0.2)',
    },
  };

  const current = formatMap[status] || {
    label: status.replace(/_/g, ' '),
    color: 'var(--text-secondary)',
    bg: 'rgba(255, 255, 255, 0.06)',
    border: 'var(--border-subtle)',
  };

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '3px 9px',
        borderRadius: 'var(--radius-full)',
        fontSize: '0.72rem',
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.04em',
        color: current.color,
        background: current.bg,
        border: `1px solid ${current.border}`,
      }}
      className={className}
    >
      {current.label}
    </span>
  );
};
