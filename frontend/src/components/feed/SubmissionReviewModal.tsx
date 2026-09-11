import React, { useState, useEffect, useMemo } from 'react';
import {
  X,
  Send,
  CheckCircle2,
  AlertTriangle,
  FileText,
  Loader2,
  Sparkles,
  Lock,
  Building,
  Info,
  ShieldCheck,
} from 'lucide-react';
import type {
  DashboardOpportunityItem,
  ApplicationResponse,
  ApplicationNotesData,
  ConfirmSubmitResponse,
} from '../../types';
import { HUMAN_SUBMISSION_APPROVAL_TOKEN } from '../../types';
import { api } from '../../api/client';
import { ReliabilityBadge, StatusBadge } from '../common/Badge';

interface SubmissionReviewModalProps {
  opportunity: DashboardOpportunityItem | null;
  isOpen: boolean;
  onClose: () => void;
  onSubmitted: (res: ConfirmSubmitResponse) => void;
}

export const SubmissionReviewModal: React.FC<SubmissionReviewModalProps> = ({
  opportunity,
  isOpen,
  onClose,
  onSubmitted,
}) => {
  const [loadingApp, setLoadingApp] = useState(false);
  const [application, setApplication] = useState<ApplicationResponse | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const [confirmedCheckbox, setConfirmedCheckbox] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Fetch application attempts when modal opens
  useEffect(() => {
    if (!isOpen || !opportunity) {
      setApplication(null);
      setFetchError(null);
      setConfirmedCheckbox(false);
      setSubmitting(false);
      setSubmitError(null);
      return;
    }

    let isMounted = true;
    setLoadingApp(true);
    setFetchError(null);
    setConfirmedCheckbox(false);
    setSubmitError(null);

    api
      .listApplications(opportunity.id)
      .then((apps) => {
        if (!isMounted) return;
        if (apps && apps.length > 0) {
          // Sort descending by id to get latest attempt
          const sorted = [...apps].sort((a, b) => b.id - a.id);
          // Prefer form_filled, then pending, or latest
          const target =
            sorted.find((a) => a.status === 'form_filled') ||
            sorted.find((a) => a.status === 'pending') ||
            sorted[0];
          setApplication(target);
        } else {
          setApplication(null);
          setFetchError('No application attempt found for this opportunity.');
        }
      })
      .catch((err: any) => {
        if (!isMounted) return;
        setFetchError(err?.message || 'Failed to load application details');
      })
      .finally(() => {
        if (isMounted) setLoadingApp(false);
      });

    return () => {
      isMounted = false;
    };
  }, [isOpen, opportunity]);

  // Parse notes JSON from application
  const notesData: ApplicationNotesData = useMemo(() => {
    if (!application?.notes) return {};
    try {
      return JSON.parse(application.notes);
    } catch {
      return {};
    }
  }, [application]);

  if (!isOpen || !opportunity) return null;

  const isAlreadySubmitted = application?.status === 'submitted';
  const customAnswers = notesData.custom_answers || [];
  const adapterName = notesData.adapter || application?.adapter_name || opportunity.source || 'Standard';
  const isBrowserTier =
    notesData.tier === 'experimental' ||
    opportunity.reliability_tier === 'experimental' ||
    adapterName.toLowerCase() === 'unstop' ||
    adapterName.toLowerCase() === 'internshala';

  const handleConfirmSubmit = async () => {
    if (!application) return;
    if (isAlreadySubmitted) return;
    if (!confirmedCheckbox) return;

    setSubmitting(true);
    setSubmitError(null);

    try {
      const res = await api.confirmSubmitApplication(application.id, {
        approval_token: HUMAN_SUBMISSION_APPROVAL_TOKEN,
        approved_by: 'human_user',
        platform_confirmed: true,
        confirmation_detail: 'Human confirmed submission via review UI',
      });

      if (!res.success) {
        setSubmitError(
          res.reason ||
            res.error ||
            'Submission was rejected or platform confirmation was not observed.'
        );
        return;
      }

      onSubmitted(res);
      onClose();
    } catch (err: any) {
      setSubmitError(err?.message || 'Submission confirmation request failed.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="submission-review-title"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 65,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 'var(--space-md)',
        background: 'rgba(5, 5, 8, 0.82)',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="animate-fade-in"
        style={{
          width: '100%',
          maxWidth: '680px',
          maxHeight: '90vh',
          background: 'var(--bg-surface)',
          borderRadius: 'var(--radius-xl)',
          border: '1px solid var(--border-medium)',
          boxShadow: 'var(--shadow-lg), 0 0 50px rgba(14, 165, 233, 0.15)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Modal Header */}
        <div
          style={{
            padding: '20px 28px',
            borderBottom: '1px solid var(--border-subtle)',
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            background: 'linear-gradient(180deg, rgba(14, 165, 233, 0.08), transparent)',
          }}
        >
          <div>
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                fontSize: '0.75rem',
                fontWeight: 600,
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
                color: '#38bdf8',
                marginBottom: '4px',
              }}
            >
              <Sparkles size={14} />
              U4 Human Intelligence & Submission Gate
            </div>
            <h2
              id="submission-review-title"
              className="editorial-title"
              style={{ fontSize: '1.45rem', color: 'var(--text-primary)', margin: 0 }}
            >
              Review & Confirm Application Submission
            </h2>
          </div>
          <button
            onClick={onClose}
            disabled={submitting}
            aria-label="Close submission review modal"
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
              cursor: submitting ? 'not-allowed' : 'pointer',
              transition: 'background var(--transition-fast)',
            }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Scrollable Content Body */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '24px 28px',
            display: 'flex',
            flexDirection: 'column',
            gap: '22px',
          }}
        >
          {/* Opportunity Header Banner */}
          <div
            style={{
              padding: '16px 20px',
              borderRadius: 'var(--radius-lg)',
              background: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: '12px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <div
                style={{
                  width: '40px',
                  height: '40px',
                  borderRadius: 'var(--radius-md)',
                  background: 'rgba(255, 255, 255, 0.04)',
                  border: '1px solid var(--border-subtle)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#38bdf8',
                }}
              >
                <Building size={20} />
              </div>
              <div>
                <h3
                  style={{
                    fontSize: '1.05rem',
                    fontWeight: 600,
                    color: 'var(--text-primary)',
                    margin: 0,
                  }}
                >
                  {opportunity.title}
                </h3>
                <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', marginTop: '2px' }}>
                  {opportunity.company} {opportunity.location ? `• ${opportunity.location}` : ''}
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <ReliabilityBadge tier={opportunity.reliability_tier} />
              <StatusBadge status={opportunity.status} />
            </div>
          </div>

          {/* 3-Step Lifecycle Visual Indicator */}
          <div
            style={{
              padding: '14px 18px',
              borderRadius: 'var(--radius-lg)',
              background: 'rgba(255, 255, 255, 0.02)',
              border: '1px solid var(--border-subtle)',
              display: 'grid',
              gridTemplateColumns: '1fr 1fr 1fr',
              gap: '10px',
            }}
          >
            {/* Step 1: Form Filled by Worker */}
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '4px',
                padding: '8px 12px',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(16, 185, 129, 0.08)',
                border: '1px solid rgba(16, 185, 129, 0.25)',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  color: 'var(--emerald-400)',
                }}
              >
                <CheckCircle2 size={14} />
                <span>1. Form Filled</span>
              </div>
              <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>
                Worker completed autofill ({adapterName})
              </span>
            </div>

            {/* Step 2: Awaiting Human Approval */}
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '4px',
                padding: '8px 12px',
                borderRadius: 'var(--radius-md)',
                background: isAlreadySubmitted
                  ? 'rgba(16, 185, 129, 0.08)'
                  : 'rgba(56, 189, 248, 0.12)',
                border: isAlreadySubmitted
                  ? '1px solid rgba(16, 185, 129, 0.25)'
                  : '1px solid rgba(56, 189, 248, 0.35)',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  color: isAlreadySubmitted ? 'var(--emerald-400)' : '#38bdf8',
                }}
              >
                {isAlreadySubmitted ? <CheckCircle2 size={14} /> : <FileText size={14} />}
                <span>2. Human Review</span>
              </div>
              <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>
                {isAlreadySubmitted ? 'Human approved' : 'Current active gate'}
              </span>
            </div>

            {/* Step 3: Final Platform Submission */}
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '4px',
                padding: '8px 12px',
                borderRadius: 'var(--radius-md)',
                background: isAlreadySubmitted
                  ? 'rgba(16, 185, 129, 0.12)'
                  : 'rgba(255, 255, 255, 0.03)',
                border: isAlreadySubmitted
                  ? '1px solid rgba(16, 185, 129, 0.35)'
                  : '1px solid var(--border-subtle)',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  color: isAlreadySubmitted ? 'var(--emerald-400)' : 'var(--text-tertiary)',
                }}
              >
                {isAlreadySubmitted ? <CheckCircle2 size={14} /> : <Lock size={14} />}
                <span>3. Final Submit</span>
              </div>
              <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>
                {isAlreadySubmitted ? 'Submitted to platform' : 'Requires explicit action'}
              </span>
            </div>
          </div>

          {/* Loading state */}
          {loadingApp && (
            <div
              style={{
                padding: '36px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '12px',
                color: 'var(--text-secondary)',
              }}
            >
              <Loader2 size={28} className="animate-spin" color="#38bdf8" />
              <span style={{ fontSize: '0.85rem' }}>Loading prepared application details...</span>
            </div>
          )}

          {/* Fetch Error */}
          {fetchError && !loadingApp && (
            <div
              style={{
                padding: '14px 18px',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(239, 68, 68, 0.1)',
                border: '1px solid rgba(239, 68, 68, 0.3)',
                color: 'var(--rose-400)',
                fontSize: '0.85rem',
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
              }}
            >
              <AlertTriangle size={18} />
              <span>{fetchError}</span>
            </div>
          )}

          {/* Already Submitted Notice */}
          {isAlreadySubmitted && (
            <div
              style={{
                padding: '16px 20px',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(16, 185, 129, 0.1)',
                border: '1px solid rgba(16, 185, 129, 0.3)',
                color: 'var(--emerald-400)',
                display: 'flex',
                alignItems: 'flex-start',
                gap: '12px',
              }}
            >
              <CheckCircle2 size={20} style={{ marginTop: '2px', flexShrink: 0 }} />
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.92rem' }}>
                  Application Already Submitted
                </div>
                <div
                  style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', marginTop: '4px' }}
                >
                  This application has already been confirmed and finalized. Duplicate submission is
                  prevented.
                  {application?.confirmation_ref && (
                    <div style={{ marginTop: '4px', fontFamily: 'monospace', fontSize: '0.8rem' }}>
                      Confirmation Reference: {application.confirmation_ref}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Application Details & Custom Answers */}
          {!loadingApp && application && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  borderBottom: '1px solid var(--border-subtle)',
                  paddingBottom: '8px',
                }}
              >
                <span
                  style={{
                    fontSize: '0.85rem',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.04em',
                    color: 'var(--text-secondary)',
                  }}
                >
                  Prepared Form Data & Custom Answers
                </span>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                  Attempt #{application.attempt_number} ({adapterName})
                </span>
              </div>

              {/* Tier Notice */}
              {isBrowserTier ? (
                <div
                  style={{
                    padding: '12px 16px',
                    borderRadius: 'var(--radius-md)',
                    background: 'rgba(139, 92, 246, 0.08)',
                    border: '1px solid rgba(139, 92, 246, 0.25)',
                    fontSize: '0.82rem',
                    color: '#c4b5fd',
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '10px',
                  }}
                >
                  <Info size={16} style={{ marginTop: '2px', flexShrink: 0 }} />
                  <div>
                    <strong>Browser Tier ({adapterName}):</strong> Candidate details, resume, and
                    custom answers have been filled into the platform form. The worker left the form
                    ready for final review.
                  </div>
                </div>
              ) : (
                <div
                  style={{
                    padding: '12px 16px',
                    borderRadius: 'var(--radius-md)',
                    background: 'rgba(99, 102, 241, 0.08)',
                    border: '1px solid rgba(99, 102, 241, 0.25)',
                    fontSize: '0.82rem',
                    color: '#a5b4fc',
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '10px',
                  }}
                >
                  <ShieldCheck size={16} style={{ marginTop: '2px', flexShrink: 0 }} />
                  <div>
                    <strong>Stable HTTP Tier ({adapterName}):</strong> The worker assembled the draft
                    payload. Confirming will authorize the programmatic HTTP POST submission to the
                    platform.
                  </div>
                </div>
              )}

              {/* Custom Answers List */}
              {customAnswers.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  {customAnswers.map((item, idx) => (
                    <div
                      key={item.question_id || idx}
                      style={{
                        padding: '14px 16px',
                        borderRadius: 'var(--radius-md)',
                        background: 'var(--bg-card)',
                        border: '1px solid var(--border-subtle)',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '8px',
                      }}
                    >
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          gap: '8px',
                        }}
                      >
                        <span
                          style={{
                            fontSize: '0.82rem',
                            fontWeight: 600,
                            color: 'var(--text-primary)',
                          }}
                        >
                          {item.question_text || item.label || `Question ${idx + 1}`}
                        </span>
                        {item.is_ai_draft && (
                          <span
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '4px',
                              padding: '2px 8px',
                              borderRadius: 'var(--radius-full)',
                              fontSize: '0.68rem',
                              fontWeight: 600,
                              background: 'rgba(139, 92, 246, 0.15)',
                              color: '#c4b5fd',
                              border: '1px solid rgba(139, 92, 246, 0.3)',
                            }}
                          >
                            <Sparkles size={11} />
                            AI-Drafted Answer (Gemini)
                          </span>
                        )}
                      </div>
                      <div
                        style={{
                          fontSize: '0.82rem',
                          color: 'var(--text-secondary)',
                          lineHeight: 1.5,
                          whiteSpace: 'pre-wrap',
                          background: 'rgba(255, 255, 255, 0.02)',
                          padding: '10px 12px',
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid rgba(255, 255, 255, 0.04)',
                        }}
                      >
                        {item.answer || '<No answer provided>'}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div
                  style={{
                    padding: '16px',
                    borderRadius: 'var(--radius-md)',
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border-subtle)',
                    fontSize: '0.82rem',
                    color: 'var(--text-secondary)',
                    textAlign: 'center',
                  }}
                >
                  Standard profile information, contact details, and attached resume were filled
                  without additional custom questions.
                </div>
              )}
            </div>
          )}

          {/* Irreversible Boundary & Confirmation Checkbox */}
          {!isAlreadySubmitted && application && (
            <div
              style={{
                padding: '16px 20px',
                borderRadius: 'var(--radius-lg)',
                background: 'rgba(249, 115, 22, 0.06)',
                border: '1px solid rgba(249, 115, 22, 0.25)',
                display: 'flex',
                flexDirection: 'column',
                gap: '14px',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  fontSize: '0.85rem',
                  fontWeight: 600,
                  color: 'var(--orange-400)',
                }}
              >
                <Lock size={15} />
                <span>Irreversible Submission Boundary</span>
              </div>
              <p
                style={{
                  fontSize: '0.8rem',
                  color: 'var(--text-secondary)',
                  margin: 0,
                  lineHeight: 1.45,
                }}
              >
                Final submission cannot be undone. Reaching this review screen is not approval; an
                explicit human confirmation action is mandatory to authorize final platform
                submission.
              </p>

              <label
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '10px',
                  cursor: submitting ? 'not-allowed' : 'pointer',
                  fontSize: '0.82rem',
                  color: 'var(--text-primary)',
                  fontWeight: 500,
                  userSelect: 'none',
                }}
              >
                <input
                  type="checkbox"
                  id="confirm-submit-checkbox"
                  checked={confirmedCheckbox}
                  onChange={(e) => setConfirmedCheckbox(e.target.checked)}
                  disabled={submitting}
                  style={{ marginTop: '2px', cursor: 'pointer' }}
                />
                <span>
                  I have reviewed the prepared application and confirm this submission.
                </span>
              </label>
            </div>
          )}

          {/* Submission error banner */}
          {submitError && (
            <div
              style={{
                padding: '14px 18px',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(239, 68, 68, 0.1)',
                border: '1px solid rgba(239, 68, 68, 0.3)',
                color: 'var(--rose-400)',
                fontSize: '0.85rem',
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
              }}
            >
              <AlertTriangle size={18} style={{ flexShrink: 0 }} />
              <span>{submitError}</span>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div
          style={{
            padding: '16px 28px',
            borderTop: '1px solid var(--border-subtle)',
            background: 'var(--bg-card)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '12px',
          }}
        >
          <button
            onClick={onClose}
            disabled={submitting}
            style={{
              padding: '9px 18px',
              borderRadius: 'var(--radius-md)',
              fontSize: '0.82rem',
              fontWeight: 500,
              background: 'transparent',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border-subtle)',
              cursor: submitting ? 'not-allowed' : 'pointer',
            }}
          >
            Cancel
          </button>

          {!isAlreadySubmitted ? (
            <button
              onClick={handleConfirmSubmit}
              disabled={submitting || !confirmedCheckbox || !application}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                padding: '9px 24px',
                borderRadius: 'var(--radius-md)',
                fontSize: '0.85rem',
                fontWeight: 600,
                color: '#ffffff',
                background:
                  submitting || !confirmedCheckbox || !application
                    ? 'rgba(255, 255, 255, 0.08)'
                    : 'linear-gradient(135deg, #0284c7, #0369a1)',
                border:
                  submitting || !confirmedCheckbox || !application
                    ? '1px solid var(--border-subtle)'
                    : '1px solid rgba(14, 165, 233, 0.5)',
                boxShadow:
                  submitting || !confirmedCheckbox || !application
                    ? 'none'
                    : '0 0 20px rgba(14, 165, 233, 0.3)',
                cursor:
                  submitting || !confirmedCheckbox || !application ? 'not-allowed' : 'pointer',
                transition: 'all var(--transition-fast)',
              }}
            >
              {submitting ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  <span>Submitting Application...</span>
                </>
              ) : (
                <>
                  <Send size={15} />
                  <span>Confirm & Submit Application</span>
                </>
              )}
            </button>
          ) : (
            <button
              onClick={onClose}
              style={{
                padding: '9px 20px',
                borderRadius: 'var(--radius-md)',
                fontSize: '0.82rem',
                fontWeight: 600,
                color: 'var(--emerald-400)',
                background: 'rgba(16, 185, 129, 0.1)',
                border: '1px solid rgba(16, 185, 129, 0.3)',
                cursor: 'pointer',
              }}
            >
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
