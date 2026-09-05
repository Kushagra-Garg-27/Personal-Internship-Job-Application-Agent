import React from 'react';
import {
  X,
  ExternalLink,
  MapPin,
  DollarSign,
  Calendar,
  Sparkles,
  ShieldCheck,
  CheckCircle2,
  FileCheck,
  Building,
} from 'lucide-react';
import type { DashboardOpportunityItem } from '../../types';
import { ReliabilityBadge, ScoreBadge, StatusBadge } from '../common/Badge';

interface OpportunityDetailDrawerProps {
  opportunity: DashboardOpportunityItem | null;
  isOpen: boolean;
  onClose: () => void;
  onApproveClick: (opp: DashboardOpportunityItem) => void;
  onDismissClick: (opp: DashboardOpportunityItem) => void;
}

export const OpportunityDetailDrawer: React.FC<OpportunityDetailDrawerProps> = ({
  opportunity,
  isOpen,
  onClose,
  onApproveClick,
  onDismissClick,
}) => {
  if (!isOpen || !opportunity) return null;

  const explanation = opportunity.relevance_explanation;
  const matchedSkills = explanation?.matched_skills || explanation?.top_skills || [];
  const matchedSentences = explanation?.matched_sentences || [];

  const salaryDisplay =
    opportunity.salary_min || opportunity.salary_max
      ? `$${(opportunity.salary_min || 0).toLocaleString('en-US')} - $${(
          opportunity.salary_max || 0
        ).toLocaleString('en-US')}`
      : null;

  const isActionable = opportunity.status === 'recommended';

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Opportunity Details"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 50,
        display: 'flex',
        justifyContent: 'flex-end',
        background: 'rgba(5, 5, 8, 0.7)',
        backdropFilter: 'blur(6px)',
        WebkitBackdropFilter: 'blur(6px)',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="animate-fade-in"
        style={{
          width: '100%',
          maxWidth: '720px',
          height: '100vh',
          background: 'var(--bg-surface)',
          borderLeft: '1px solid var(--border-medium)',
          boxShadow: 'var(--shadow-lg), 0 0 50px rgba(0, 0, 0, 0.8)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Drawer Header */}
        <div
          style={{
            padding: 'var(--space-lg) var(--space-xl)',
            borderBottom: '1px solid var(--border-subtle)',
            background: 'var(--bg-card)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <ReliabilityBadge tier={opportunity.reliability_tier} />
            <StatusBadge status={opportunity.status} />
            <ScoreBadge score={opportunity.relevance_score} />
          </div>

          <button
            onClick={onClose}
            aria-label="Close drawer"
            style={{
              background: 'rgba(255, 255, 255, 0.05)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-full)',
              width: '32px',
              height: '32px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              transition: 'background var(--transition-fast)',
            }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Scrollable Content Body */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: 'var(--space-xl)',
            display: 'flex',
            flexDirection: 'column',
            gap: '28px',
          }}
        >
          {/* Title & Metadata Header */}
          <div>
            <h1
              className="editorial-title"
              style={{
                fontSize: '1.9rem',
                color: 'var(--text-primary)',
                marginBottom: '8px',
                lineHeight: 1.2,
              }}
            >
              {opportunity.title}
            </h1>

            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '1.1rem',
                color: 'var(--text-secondary)',
                marginBottom: '16px',
              }}
            >
              <Building size={18} color="#a5b4fc" />
              <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                {opportunity.company}
              </span>
              {opportunity.source && (
                <span style={{ color: 'var(--text-tertiary)', fontSize: '0.85rem' }}>
                  via {opportunity.source}
                </span>
              )}
            </div>

            {/* Quick Meta Pills */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '14px', fontSize: '0.85rem' }}>
              {opportunity.location && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-secondary)' }}>
                  <MapPin size={15} color="var(--purple-500)" />
                  <span>{opportunity.location}</span>
                </div>
              )}
              {salaryDisplay && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--emerald-400)' }}>
                  <DollarSign size={15} />
                  <span>{salaryDisplay}</span>
                </div>
              )}
              {opportunity.deadline_at && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--amber-400)' }}>
                  <Calendar size={15} />
                  <span>Deadline: {new Date(opportunity.deadline_at).toLocaleDateString()}</span>
                </div>
              )}
              {opportunity.url && (
                <a
                  href={opportunity.url}
                  target="_blank"
                  rel="noreferrer"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '5px',
                    color: '#a5b4fc',
                    textDecoration: 'none',
                    fontWeight: 500,
                  }}
                >
                  <span>Original Job Listing</span>
                  <ExternalLink size={14} />
                </a>
              )}
            </div>
          </div>

          {/* Section: Relevance Match Analysis (Stage 3) */}
          <div
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-purple)',
              borderRadius: 'var(--radius-lg)',
              padding: '20px',
              boxShadow: '0 4px 20px rgba(124, 58, 237, 0.08)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Sparkles size={18} color="var(--purple-500)" />
                <h2 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
                  Relevance & Semantic Intelligence
                </h2>
              </div>
              {opportunity.model_name && (
                <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>
                  Engine: {opportunity.model_name}
                </span>
              )}
            </div>

            {explanation?.match_summary && (
              <p
                style={{
                  fontSize: '0.88rem',
                  lineHeight: 1.5,
                  color: '#e2e8f0',
                  marginBottom: '16px',
                  background: 'rgba(124, 58, 237, 0.1)',
                  padding: '12px 14px',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid rgba(139, 92, 246, 0.2)',
                }}
              >
                {explanation.match_summary}
              </p>
            )}

            {/* Matched Skills */}
            {matchedSkills.length > 0 && (
              <div style={{ marginBottom: '16px' }}>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: '8px' }}>
                  Aligned Profile Skills ({matchedSkills.length})
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {matchedSkills.map((skill, idx) => (
                    <span
                      key={idx}
                      style={{
                        padding: '4px 10px',
                        borderRadius: 'var(--radius-full)',
                        fontSize: '0.75rem',
                        fontWeight: 600,
                        background: 'rgba(99, 102, 241, 0.14)',
                        color: '#c7d2fe',
                        border: '1px solid rgba(99, 102, 241, 0.3)',
                      }}
                    >
                      {skill}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Top Semantic Sentence Matches */}
            {matchedSentences.length > 0 && (
              <div>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: '8px' }}>
                  High-Alignment Requirement Matches
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {matchedSentences.slice(0, 4).map((sent, idx) => (
                    <div
                      key={idx}
                      style={{
                        fontSize: '0.8rem',
                        padding: '8px 12px',
                        borderRadius: 'var(--radius-sm)',
                        background: 'rgba(255, 255, 255, 0.03)',
                        borderLeft: '3px solid var(--purple-500)',
                        display: 'flex',
                        alignItems: 'baseline',
                        justifyContent: 'space-between',
                        gap: '10px',
                      }}
                    >
                      <span style={{ color: 'var(--text-secondary)' }}>"{sent.sentence}"</span>
                      {sent.similarity !== undefined && (
                        <span
                          style={{
                            fontSize: '0.72rem',
                            fontWeight: 700,
                            color: '#c4b5fd',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {Math.round(sent.similarity * 100)}% Sim
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Section: Funnel Gates Audit (Stage 1 & Stage 2) */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
            {/* Stage 1 Eligibility */}
            <div
              style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '16px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '8px' }}>
                <CheckCircle2 size={16} color="var(--emerald-400)" />
                <span style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                  Stage 1: Eligibility
                </span>
              </div>
              <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                {opportunity.eligibility_passed ? (
                  <span style={{ color: 'var(--emerald-400)' }}>Passed all deterministic rules</span>
                ) : (
                  <span>Checks completed</span>
                )}
                {opportunity.eligibility_reason?.detail && (
                  <div style={{ marginTop: '4px', color: 'var(--text-tertiary)', fontSize: '0.74rem' }}>
                    {opportunity.eligibility_reason.detail}
                  </div>
                )}
              </div>
            </div>

            {/* Stage 2 Scam & Risk */}
            <div
              style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '16px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '8px' }}>
                <ShieldCheck size={16} color="var(--purple-500)" />
                <span style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                  Stage 2: Scam & Risk
                </span>
              </div>
              <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                Verdict: <span style={{ color: '#ffffff', fontWeight: 600 }}>{opportunity.scam_verdict || 'clear'}</span>
                {opportunity.llm_verdict && (
                  <div style={{ marginTop: '4px', color: 'var(--text-tertiary)', fontSize: '0.74rem' }}>
                    LLM Analysis: {opportunity.llm_verdict}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Section: Job Description */}
          <div>
            <h2
              style={{
                fontSize: '1rem',
                fontWeight: 600,
                color: 'var(--text-primary)',
                marginBottom: '12px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
              }}
            >
              <FileCheck size={16} color="var(--text-tertiary)" />
              Full Job Description
            </h2>

            <div
              style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-lg)',
                padding: '20px',
                fontSize: '0.88rem',
                lineHeight: 1.65,
                color: 'var(--text-secondary)',
                whiteSpace: 'pre-line',
                maxHeight: '420px',
                overflowY: 'auto',
              }}
            >
              {opportunity.description || 'No detailed description provided by source.'}
            </div>
          </div>
        </div>

        {/* Drawer Sticky Action Footer */}
        <div
          style={{
            padding: 'var(--space-md) var(--space-xl)',
            borderTop: '1px solid var(--border-subtle)',
            background: 'var(--bg-surface)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '12px',
          }}
        >
          <div>
            {opportunity.status === 'ready_to_apply' && (
              <span style={{ fontSize: '0.82rem', color: 'var(--emerald-400)', fontWeight: 600 }}>
                ✓ Approved — Awaiting Phase 9 Worker
              </span>
            )}
            {opportunity.status === 'dismissed' && (
              <span style={{ fontSize: '0.82rem', color: 'var(--text-tertiary)' }}>
                Opportunity has been dismissed
              </span>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            {isActionable && (
              <>
                <button
                  onClick={() => onDismissClick(opportunity)}
                  style={{
                    padding: '9px 18px',
                    borderRadius: 'var(--radius-md)',
                    fontSize: '0.85rem',
                    fontWeight: 500,
                    background: 'transparent',
                    color: 'var(--rose-400)',
                    border: '1px solid rgba(239, 68, 68, 0.3)',
                    transition: 'all var(--transition-fast)',
                  }}
                >
                  Dismiss
                </button>

                <button
                  onClick={() => onApproveClick(opportunity)}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '9px 22px',
                    borderRadius: 'var(--radius-md)',
                    fontSize: '0.85rem',
                    fontWeight: 600,
                    color: '#ffffff',
                    background: 'linear-gradient(135deg, var(--orange-500), var(--orange-600))',
                    border: '1px solid rgba(249, 115, 22, 0.5)',
                    boxShadow: 'var(--shadow-orange)',
                    transition: 'all var(--transition-fast)',
                  }}
                >
                  <span>Approve for Application</span>
                </button>
              </>
            )}

            {!isActionable && (
              <button
                onClick={onClose}
                style={{
                  padding: '9px 18px',
                  borderRadius: 'var(--radius-md)',
                  fontSize: '0.85rem',
                  fontWeight: 500,
                  background: 'rgba(255, 255, 255, 0.05)',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border-subtle)',
                }}
              >
                Close
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
