import React, { useState } from 'react';
import {
  MapPin,
  DollarSign,
  Calendar,
  CheckCircle,
  ChevronRight,
  Building,
  Send,
  FileText,
} from 'lucide-react';
import type { DashboardOpportunityItem } from '../../types';
import { ReliabilityBadge, ScoreBadge, StatusBadge } from '../common/Badge';

interface OpportunityCardProps {
  opportunity: DashboardOpportunityItem;
  onInspect: (opp: DashboardOpportunityItem) => void;
  onApprove: (opp: DashboardOpportunityItem) => void;
  onDismiss: (opp: DashboardOpportunityItem) => void;
  onReviewSubmit?: (opp: DashboardOpportunityItem) => void;
}

export const OpportunityCard: React.FC<OpportunityCardProps> = ({
  opportunity,
  onInspect,
  onApprove,
  onDismiss,
  onReviewSubmit,
}) => {
  const [isHovered, setIsHovered] = useState(false);

  const explanation = opportunity.relevance_explanation;
  const matchedSkills = explanation?.matched_skills || explanation?.top_skills || [];
  const previewSkills = matchedSkills.slice(0, 5);
  const remainingCount = matchedSkills.length - previewSkills.length;

  const salaryDisplay =
    opportunity.salary_min || opportunity.salary_max
      ? `$${(opportunity.salary_min || 0).toLocaleString('en-US')} - $${(
          opportunity.salary_max || 0
        ).toLocaleString('en-US')}`
      : null;

  const isRecommended = opportunity.status === 'recommended';
  const isReadyToApply = opportunity.status === 'ready_to_apply';
  const isAwaitingSubmission = opportunity.status === 'awaiting_submission';
  const isApplied = opportunity.status === 'applied' || opportunity.status === 'submitted';
  const isDismissed = opportunity.status === 'dismissed';

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        position: 'relative',
        borderRadius: 'var(--radius-xl)',
        background: isHovered ? 'var(--bg-card-hover)' : 'var(--bg-card)',
        border: isHovered
          ? '1px solid rgba(139, 92, 246, 0.35)'
          : '1px solid var(--border-subtle)',
        boxShadow: isHovered
          ? 'var(--shadow-md), 0 0 32px rgba(124, 58, 237, 0.12)'
          : 'var(--shadow-sm)',
        padding: '24px 28px',
        display: 'flex',
        flexDirection: 'column',
        gap: '18px',
        transition: 'all var(--transition-normal)',
      }}
    >
      {/* ── Top Header Strip ─────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '12px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div
            style={{
              width: '36px',
              height: '36px',
              borderRadius: 'var(--radius-md)',
              background: 'rgba(255, 255, 255, 0.04)',
              border: '1px solid var(--border-subtle)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#c4b5fd',
            }}
          >
            <Building size={18} />
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                {opportunity.company}
              </span>
              {opportunity.source && (
                <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>
                  via {opportunity.source}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Badges Strip */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <ReliabilityBadge tier={opportunity.reliability_tier} />
          <StatusBadge status={opportunity.status} />
          <ScoreBadge score={opportunity.relevance_score} />
        </div>
      </div>

      {/* ── Main Title & Location/Salary Strip ────────────────────────── */}
      <div>
        <h2
          onClick={() => onInspect(opportunity)}
          className="editorial-title"
          style={{
            fontSize: '1.45rem',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            margin: '0 0 8px 0',
            transition: 'color var(--transition-fast)',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = '#c4b5fd')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-primary)')}
        >
          {opportunity.title}
        </h2>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '16px',
            fontSize: '0.82rem',
            color: 'var(--text-secondary)',
          }}
        >
          {opportunity.location && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
              <MapPin size={14} color="var(--purple-500)" />
              <span>{opportunity.location}</span>
            </div>
          )}
          {salaryDisplay && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '5px', color: 'var(--emerald-400)' }}>
              <DollarSign size={14} />
              <span style={{ fontWeight: 600 }}>{salaryDisplay}</span>
            </div>
          )}
          {opportunity.deadline_at && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '5px', color: 'var(--amber-400)' }}>
              <Calendar size={14} />
              <span>Deadline: {new Date(opportunity.deadline_at).toLocaleDateString()}</span>
            </div>
          )}
        </div>
      </div>

      {/* ── Relevance Snippet / Match Summary ─────────────────────────── */}
      {explanation?.match_summary && (
        <div
          style={{
            fontSize: '0.84rem',
            lineHeight: 1.5,
            color: 'var(--text-secondary)',
            background: 'rgba(255, 255, 255, 0.025)',
            borderLeft: '3px solid var(--purple-500)',
            padding: '8px 14px',
            borderRadius: '0 var(--radius-sm) var(--radius-sm) 0',
          }}
        >
          {explanation.match_summary}
        </div>
      )}

      {/* ── Matched Skills Pills ──────────────────────────────────────── */}
      {previewSkills.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px' }}>
          {previewSkills.map((skill, idx) => (
            <span
              key={idx}
              style={{
                padding: '3px 9px',
                borderRadius: 'var(--radius-full)',
                fontSize: '0.72rem',
                fontWeight: 600,
                background: 'rgba(99, 102, 241, 0.1)',
                color: '#c7d2fe',
                border: '1px solid rgba(99, 102, 241, 0.22)',
              }}
            >
              {skill}
            </span>
          ))}
          {remainingCount > 0 && (
            <span
              style={{
                padding: '3px 8px',
                borderRadius: 'var(--radius-full)',
                fontSize: '0.72rem',
                color: 'var(--text-tertiary)',
              }}
            >
              +{remainingCount} more
            </span>
          )}
        </div>
      )}

      {/* ── Card Footer Action Bar ────────────────────────────────────── */}
      <div
        style={{
          marginTop: '6px',
          paddingTop: '16px',
          borderTop: '1px solid var(--border-subtle)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '12px',
        }}
      >
        <button
          onClick={() => onInspect(opportunity)}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            background: 'transparent',
            color: 'var(--text-secondary)',
            fontSize: '0.82rem',
            fontWeight: 500,
            padding: '6px 12px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            transition: 'all var(--transition-fast)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = '#ffffff';
            e.currentTarget.style.borderColor = 'var(--border-medium)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)';
            e.currentTarget.style.borderColor = 'var(--border-subtle)';
          }}
        >
          <span>Inspect Fit</span>
          <ChevronRight size={14} />
        </button>

        {/* Status or Primary Actions */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {isRecommended && (
            <>
              <button
                onClick={() => onDismiss(opportunity)}
                style={{
                  padding: '7px 14px',
                  borderRadius: 'var(--radius-md)',
                  fontSize: '0.82rem',
                  fontWeight: 500,
                  background: 'transparent',
                  color: 'var(--text-tertiary)',
                  border: '1px solid var(--border-subtle)',
                  transition: 'all var(--transition-fast)',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = 'var(--rose-400)';
                  e.currentTarget.style.borderColor = 'rgba(239, 68, 68, 0.35)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = 'var(--text-tertiary)';
                  e.currentTarget.style.borderColor = 'var(--border-subtle)';
                }}
              >
                Dismiss
              </button>

              <button
                onClick={() => onApprove(opportunity)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '7px 18px',
                  borderRadius: 'var(--radius-md)',
                  fontSize: '0.82rem',
                  fontWeight: 600,
                  color: '#ffffff',
                  background: 'linear-gradient(135deg, var(--orange-500), var(--orange-600))',
                  border: '1px solid rgba(249, 115, 22, 0.5)',
                  boxShadow: 'var(--shadow-orange)',
                  cursor: 'pointer',
                  transition: 'all var(--transition-fast)',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.transform = 'translateY(-1px)';
                  e.currentTarget.style.boxShadow = '0 6px 20px rgba(249, 115, 22, 0.35)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.transform = 'translateY(0)';
                  e.currentTarget.style.boxShadow = 'var(--shadow-orange)';
                }}
              >
                <CheckCircle size={15} />
                <span>Approve</span>
              </button>
            </>
          )}

          {isReadyToApply && (
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 12px',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(16, 185, 129, 0.1)',
                border: '1px solid rgba(16, 185, 129, 0.25)',
                fontSize: '0.78rem',
                fontWeight: 600,
                color: 'var(--emerald-400)',
              }}
            >
              <CheckCircle size={14} />
              <span>Ready for Phase 9 Worker</span>
            </div>
          )}

          {isAwaitingSubmission && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '6px 12px',
                  borderRadius: 'var(--radius-md)',
                  background: 'rgba(56, 189, 248, 0.1)',
                  border: '1px solid rgba(56, 189, 248, 0.25)',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  color: '#38bdf8',
                }}
              >
                <FileText size={14} />
                <span>Form Filled by Worker</span>
              </div>

              {onReviewSubmit && (
                <button
                  onClick={() => onReviewSubmit(opportunity)}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '7px 18px',
                    borderRadius: 'var(--radius-md)',
                    fontSize: '0.82rem',
                    fontWeight: 600,
                    color: '#ffffff',
                    background: 'linear-gradient(135deg, #0284c7, #0369a1)',
                    border: '1px solid rgba(14, 165, 233, 0.5)',
                    boxShadow: '0 0 16px rgba(14, 165, 233, 0.25)',
                    cursor: 'pointer',
                    transition: 'all var(--transition-fast)',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.transform = 'translateY(-1px)';
                    e.currentTarget.style.boxShadow = '0 6px 20px rgba(14, 165, 233, 0.35)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.transform = 'translateY(0)';
                    e.currentTarget.style.boxShadow = '0 0 16px rgba(14, 165, 233, 0.25)';
                  }}
                >
                  <Send size={14} />
                  <span>Review & Submit</span>
                </button>
              )}
            </div>
          )}

          {isApplied && (
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 12px',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(16, 185, 129, 0.1)',
                border: '1px solid rgba(16, 185, 129, 0.25)',
                fontSize: '0.78rem',
                fontWeight: 600,
                color: 'var(--emerald-400)',
              }}
            >
              <CheckCircle size={14} />
              <span>Application Submitted</span>
            </div>
          )}

          {isDismissed && (
            <span
              style={{
                fontSize: '0.78rem',
                color: 'var(--text-tertiary)',
                fontStyle: 'italic',
              }}
            >
              Dismissed by user
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
