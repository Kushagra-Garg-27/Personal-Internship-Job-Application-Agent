import React, { useState, useEffect } from 'react';
import { CheckCircle, AlertCircle, FileText, X, Loader2, Sparkles } from 'lucide-react';
import type { DashboardOpportunityItem, ResumeOption } from '../../types';
import { api } from '../../api/client';

interface ApprovalModalProps {
  opportunity: DashboardOpportunityItem | null;
  isOpen: boolean;
  onClose: () => void;
  onApproved: (updated: any) => void;
}

export const ApprovalModal: React.FC<ApprovalModalProps> = ({
  opportunity,
  isOpen,
  onClose,
  onApproved,
}) => {
  const [selectedResumeId, setSelectedResumeId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Set default resume to active resume or first available
  useEffect(() => {
    if (opportunity && opportunity.available_resumes?.length > 0) {
      const active = opportunity.available_resumes.find((r) => r.is_active);
      setSelectedResumeId(active ? active.id : opportunity.available_resumes[0].id);
    } else {
      setSelectedResumeId(null);
    }
    setError(null);
  }, [opportunity]);

  if (!isOpen || !opportunity) return null;

  const handleApprove = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.finalApprove(opportunity.id, selectedResumeId);
      onApproved(res);
      onClose();
    } catch (err: any) {
      setError(err?.message || 'Failed to approve opportunity');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 60,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 'var(--space-md)',
        background: 'rgba(5, 5, 8, 0.78)',
        backdropFilter: 'blur(10px)',
        WebkitBackdropFilter: 'blur(10px)',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="animate-fade-in"
        style={{
          width: '100%',
          maxWidth: '560px',
          background: 'var(--bg-surface)',
          borderRadius: 'var(--radius-xl)',
          border: '1px solid var(--border-medium)',
          boxShadow: 'var(--shadow-lg), 0 0 40px rgba(124, 58, 237, 0.12)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: 'var(--space-lg) var(--space-xl)',
            borderBottom: '1px solid var(--border-subtle)',
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            background: 'linear-gradient(180deg, rgba(249, 115, 22, 0.06), transparent)',
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
                color: 'var(--orange-500)',
                marginBottom: '4px',
              }}
            >
              <Sparkles size={14} />
              Phase 6 Human Approval Gate
            </div>
            <h2
              id="modal-title"
              className="editorial-title"
              style={{ fontSize: '1.45rem', color: 'var(--text-primary)', margin: 0 }}
            >
              Approve for Application
            </h2>
          </div>
          <button
            onClick={onClose}
            disabled={submitting}
            aria-label="Close approval modal"
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

        {/* Content Body */}
        <div style={{ padding: 'var(--space-xl)', display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {/* Target Opportunity Details */}
          <div
            style={{
              padding: '14px 18px',
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Selected Opportunity
            </div>
            <div style={{ fontSize: '1.1rem', fontWeight: 600, color: 'var(--text-primary)', marginTop: '2px' }}>
              {opportunity.title}
            </div>
            <div style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginTop: '2px' }}>
              {opportunity.company} {opportunity.location ? `• ${opportunity.location}` : ''}
            </div>
          </div>

          {/* Resume Version Selector */}
          <div>
            <label
              htmlFor="resume-select-list"
              style={{
                display: 'block',
                fontSize: '0.85rem',
                fontWeight: 600,
                color: 'var(--text-primary)',
                marginBottom: '10px',
              }}
            >
              Select Resume Version to Attach:
            </label>

            {opportunity.available_resumes && opportunity.available_resumes.length > 0 ? (
              <div
                id="resume-select-list"
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '8px',
                  maxHeight: '200px',
                  overflowY: 'auto',
                }}
              >
                {opportunity.available_resumes.map((resume: ResumeOption) => {
                  const isSelected = selectedResumeId === resume.id;
                  return (
                    <div
                      key={resume.id}
                      onClick={() => setSelectedResumeId(resume.id)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '12px 16px',
                        borderRadius: 'var(--radius-md)',
                        cursor: 'pointer',
                        background: isSelected
                          ? 'rgba(249, 115, 22, 0.12)'
                          : 'var(--bg-card)',
                        border: isSelected
                          ? '1px solid rgba(249, 115, 22, 0.45)'
                          : '1px solid var(--border-subtle)',
                        transition: 'all var(--transition-fast)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                        <div
                          style={{
                            width: '32px',
                            height: '32px',
                            borderRadius: 'var(--radius-sm)',
                            background: isSelected ? 'var(--orange-500)' : 'rgba(255, 255, 255, 0.08)',
                            color: isSelected ? '#ffffff' : 'var(--text-secondary)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                          }}
                        >
                          <FileText size={18} />
                        </div>
                        <div>
                          <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                            {resume.original_filename}
                          </div>
                          <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                            Version {resume.version}
                            {resume.uploaded_at ? ` • ${new Date(resume.uploaded_at).toLocaleDateString()}` : ''}
                          </div>
                        </div>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {resume.is_active && (
                          <span
                            style={{
                              fontSize: '0.68rem',
                              fontWeight: 600,
                              textTransform: 'uppercase',
                              padding: '2px 8px',
                              borderRadius: 'var(--radius-full)',
                              background: 'rgba(16, 185, 129, 0.15)',
                              color: 'var(--emerald-400)',
                              border: '1px solid rgba(16, 185, 129, 0.3)',
                            }}
                          >
                            Active Default
                          </span>
                        )}
                        <input
                          type="radio"
                          name="resume-selection"
                          checked={isSelected}
                          onChange={() => setSelectedResumeId(resume.id)}
                          style={{ accentColor: 'var(--orange-500)', cursor: 'pointer' }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div
                style={{
                  padding: '14px',
                  borderRadius: 'var(--radius-md)',
                  background: 'rgba(255, 255, 255, 0.04)',
                  fontSize: '0.85rem',
                  color: 'var(--text-tertiary)',
                  textAlign: 'center',
                }}
              >
                No uploaded resume found in profile. The system will approve with default profile context.
              </div>
            )}
          </div>

          {/* Architecture Gate Note */}
          <div
            style={{
              padding: '12px 16px',
              borderRadius: 'var(--radius-md)',
              background: 'rgba(99, 102, 241, 0.08)',
              border: '1px solid rgba(99, 102, 241, 0.2)',
              fontSize: '0.78rem',
              color: '#c4b5fd',
              lineHeight: 1.45,
            }}
          >
            <strong>Downstream Effect:</strong> Approving transitions this opportunity from{' '}
            <code style={{ color: '#ffffff' }}>recommended</code> to{' '}
            <code style={{ color: '#ffffff' }}>ready_to_apply</code>. This satisfies the{' '}
            <strong>[HUMAN APPROVES]</strong> gate from §2, queuing it for Phase 9 application workers.
          </div>

          {/* Error Banner */}
          {error && (
            <div
              style={{
                padding: '10px 14px',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(239, 68, 68, 0.12)',
                border: '1px solid rgba(239, 68, 68, 0.3)',
                color: 'var(--rose-400)',
                fontSize: '0.82rem',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
              }}
            >
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div
          style={{
            padding: 'var(--space-md) var(--space-xl)',
            borderTop: '1px solid var(--border-subtle)',
            background: 'var(--bg-card)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: '12px',
          }}
        >
          <button
            onClick={onClose}
            disabled={submitting}
            style={{
              padding: '9px 18px',
              borderRadius: 'var(--radius-md)',
              fontSize: '0.875rem',
              fontWeight: 500,
              background: 'transparent',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border-medium)',
              transition: 'background var(--transition-fast)',
            }}
          >
            Cancel
          </button>

          <button
            onClick={handleApprove}
            disabled={submitting}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              padding: '9px 22px',
              borderRadius: 'var(--radius-md)',
              fontSize: '0.875rem',
              fontWeight: 600,
              color: '#ffffff',
              background: 'linear-gradient(135deg, var(--orange-500), var(--orange-600))',
              border: '1px solid rgba(249, 115, 22, 0.5)',
              boxShadow: 'var(--shadow-orange)',
              cursor: submitting ? 'not-allowed' : 'pointer',
              transition: 'all var(--transition-fast)',
            }}
          >
            {submitting ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                <span>Approving...</span>
              </>
            ) : (
              <>
                <CheckCircle size={16} />
                <span>Confirm & Approve</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
