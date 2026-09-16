import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Clock,
  RefreshCw,
  AlertTriangle,
  CheckCircle,
  Loader2,
  ExternalLink,
  Ban,
} from 'lucide-react';
import type { PendingQueueItem } from '../types';
import { api } from '../api/client';

const MANUAL_REVIEW_LABELS: Record<string, string> = {
  ambiguous_next_control: 'Final action needs manual review',
  ambiguous_controls: 'Application controls need manual review',
  no_final_control: 'Final submission control not found',
  multiple_final_controls: 'Multiple final controls detected',
  final_control_disabled: 'Final control is disabled',
  final_control_hidden: 'Final control is hidden',
};

export const SubmissionQueuePage: React.FC = () => {
  const [items, setItems] = useState<PendingQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedFilter, setSelectedFilter] = useState<string>('all');
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Revocation modal state
  const [cancelingItem, setCancelingItem] = useState<PendingQueueItem | null>(null);
  const [cancelReason, setCancelReason] = useState<string>('');
  const [isRevoking, setIsRevoking] = useState<boolean>(false);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  const fetchQueue = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getPendingQueue();
      setItems(data);
    } catch (err: any) {
      showToast(`Failed to load queue: ${err?.message || 'Server error'}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchQueue();
    const timer = setInterval(fetchQueue, 15000);
    return () => clearInterval(timer);
  }, [fetchQueue]);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => {
      setToastMessage((cur) => (cur === msg ? null : cur));
    }, 4500);
  };

  const handleOpenCancelModal = (item: PendingQueueItem) => {
    setCancelingItem(item);
    setCancelReason('');
    setRevokeError(null);
  };

  const handleCloseCancelModal = () => {
    setCancelingItem(null);
    setCancelReason('');
    setRevokeError(null);
  };

  const handleConfirmRevoke = async () => {
    if (!cancelingItem) return;
    setIsRevoking(true);
    setRevokeError(null);

    try {
      await api.revokeApproval(cancelingItem.application_id, cancelReason || undefined);
      showToast(`Approval canceled for #${cancelingItem.application_id}`);
      handleCloseCancelModal();
      // Re-fetch queue immediately from server
      await fetchQueue();
    } catch (err: any) {
      setRevokeError(err?.message || 'Failed to cancel approval. The submission may already be claimed.');
    } finally {
      setIsRevoking(false);
    }
  };

  const filteredItems = useMemo(() => {
    if (selectedFilter === 'all') return items;
    return items.filter((item) => item.queue_state.toLowerCase() === selectedFilter.toLowerCase());
  }, [items, selectedFilter]);

  const counts = useMemo(() => {
    const res: Record<string, number> = {
      all: items.length,
      approved_pending: 0,
      claimed_in_progress: 0,
      manual_review: 0,
      revoked: 0,
      submitted: 0,
    };
    for (const item of items) {
      const key = item.queue_state.toLowerCase();
      if (key in res) {
        res[key]++;
      }
    }
    return res;
  }, [items]);

  const renderStateBadge = (state: string) => {
    switch (state.toUpperCase()) {
      case 'APPROVED_PENDING':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: 'var(--radius-full)',
              background: 'rgba(245, 158, 11, 0.15)',
              color: 'var(--amber-400, #fbbf24)',
              border: '1px solid rgba(245, 158, 11, 0.3)',
              fontSize: '0.75rem',
              fontWeight: 600,
            }}
          >
            <span
              style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                background: 'var(--amber-400, #fbbf24)',
                boxShadow: '0 0 6px var(--amber-400, #fbbf24)',
              }}
            />
            Approved • Pending Worker
          </span>
        );
      case 'CLAIMED_IN_PROGRESS':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: 'var(--radius-full)',
              background: 'rgba(59, 130, 246, 0.15)',
              color: '#60a5fa',
              border: '1px solid rgba(59, 130, 246, 0.3)',
              fontSize: '0.75rem',
              fontWeight: 600,
            }}
          >
            <Loader2 size={12} className="animate-spin" />
            Claimed • Executing
          </span>
        );
      case 'MANUAL_REVIEW':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: 'var(--radius-full)',
              background: 'rgba(249, 115, 22, 0.15)',
              color: '#fb923c',
              border: '1px solid rgba(249, 115, 22, 0.3)',
              fontSize: '0.75rem',
              fontWeight: 600,
            }}
          >
            <AlertTriangle size={12} />
            Manual Review Required
          </span>
        );
      case 'REVOKED':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: 'var(--radius-full)',
              background: 'rgba(239, 68, 68, 0.15)',
              color: '#f87171',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              fontSize: '0.75rem',
              fontWeight: 600,
            }}
          >
            <Ban size={12} />
            Revoked
          </span>
        );
      case 'SUBMITTED':
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: 'var(--radius-full)',
              background: 'rgba(16, 185, 129, 0.15)',
              color: 'var(--emerald-400, #34d399)',
              border: '1px solid rgba(16, 185, 129, 0.3)',
              fontSize: '0.75rem',
              fontWeight: 600,
            }}
          >
            <CheckCircle size={12} />
            Submitted
          </span>
        );
      default:
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: 'var(--radius-full)',
              background: 'rgba(156, 163, 175, 0.15)',
              color: '#9ca3af',
              border: '1px solid rgba(156, 163, 175, 0.3)',
              fontSize: '0.75rem',
              fontWeight: 600,
            }}
          >
            {state}
          </span>
        );
    }
  };

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: 'var(--space-xl) var(--space-lg)' }}>
      {/* Toast */}
      {toastMessage && (
        <div
          style={{
            position: 'fixed',
            bottom: '24px',
            right: '24px',
            zIndex: 100,
            background: 'var(--bg-card, #181622)',
            border: '1px solid var(--border-medium, #2e2b3e)',
            borderRadius: 'var(--radius-md, 8px)',
            padding: '12px 18px',
            color: 'var(--text-primary, #ffffff)',
            boxShadow: '0 8px 30px rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
          }}
        >
          <span>{toastMessage}</span>
          <button
            onClick={() => setToastMessage(null)}
            style={{ background: 'none', border: 'none', color: 'var(--text-tertiary, #8a879a)', cursor: 'pointer' }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Page Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          marginBottom: 'var(--space-xl)',
          flexWrap: 'wrap',
          gap: '16px',
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
            <h1
              style={{
                fontSize: '1.75rem',
                fontWeight: 700,
                color: 'var(--text-primary, #ffffff)',
                margin: 0,
                letterSpacing: '-0.02em',
              }}
            >
              Submission Queue
            </h1>
            <span
              style={{
                fontSize: '0.75rem',
                fontWeight: 600,
                padding: '3px 9px',
                borderRadius: 'var(--radius-full)',
                background: 'rgba(139, 92, 246, 0.15)',
                color: '#c4b5fd',
                border: '1px solid rgba(139, 92, 246, 0.3)',
              }}
            >
              M5 Live Safe-Queue
            </span>
          </div>
          <p style={{ margin: 0, fontSize: '0.875rem', color: 'var(--text-tertiary, #9ca3af)' }}>
            Cancelable queue of approved submissions before background worker claims them. Revocation is race-safe.
          </p>
        </div>

        <button
          onClick={fetchQueue}
          disabled={loading}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 16px',
            borderRadius: 'var(--radius-md, 8px)',
            background: 'var(--bg-surface, #1e1b2e)',
            border: '1px solid var(--border-medium, #2e2b3e)',
            color: 'var(--text-primary, #ffffff)',
            fontSize: '0.875rem',
            fontWeight: 500,
            cursor: loading ? 'not-allowed' : 'pointer',
            transition: 'background 0.2s',
          }}
        >
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          <span>Refresh</span>
        </button>
      </div>

      {/* Filter Tabs */}
      <div
        style={{
          display: 'flex',
          gap: '8px',
          overflowX: 'auto',
          paddingBottom: '8px',
          marginBottom: 'var(--space-lg)',
          borderBottom: '1px solid var(--border-subtle, #1e1b2e)',
        }}
      >
        {[
          { key: 'all', label: 'All Items', count: counts.all },
          { key: 'approved_pending', label: 'Pending Approval', count: counts.approved_pending },
          { key: 'claimed_in_progress', label: 'Claimed / In Progress', count: counts.claimed_in_progress },
          { key: 'manual_review', label: 'Manual Review', count: counts.manual_review },
          { key: 'revoked', label: 'Revoked (Audit)', count: counts.revoked },
          { key: 'submitted', label: 'Submitted', count: counts.submitted },
        ].map((tab) => {
          const active = selectedFilter === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => setSelectedFilter(tab.key)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                padding: '7px 14px',
                borderRadius: 'var(--radius-md, 8px)',
                fontSize: '0.825rem',
                fontWeight: 500,
                cursor: 'pointer',
                border: active ? '1px solid var(--purple-500, #8b5cf6)' : '1px solid transparent',
                background: active ? 'rgba(139, 92, 246, 0.15)' : 'transparent',
                color: active ? '#ffffff' : 'var(--text-secondary, #9ca3af)',
                transition: 'all 0.15s',
              }}
            >
              <span>{tab.label}</span>
              <span
                style={{
                  fontSize: '0.7rem',
                  padding: '1px 6px',
                  borderRadius: 'var(--radius-full)',
                  background: active ? 'rgba(255, 255, 255, 0.2)' : 'rgba(255, 255, 255, 0.05)',
                  color: active ? '#ffffff' : 'var(--text-tertiary, #6b7280)',
                }}
              >
                {tab.count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Queue Items Table / Cards */}
      {loading && items.length === 0 ? (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '60px 20px',
            color: 'var(--text-tertiary, #9ca3af)',
          }}
        >
          <Loader2 size={32} className="animate-spin" style={{ marginBottom: '12px' }} />
          <p>Loading submission queue...</p>
        </div>
      ) : filteredItems.length === 0 ? (
        <div
          style={{
            background: 'var(--bg-card, #181622)',
            border: '1px dashed var(--border-medium, #2e2b3e)',
            borderRadius: 'var(--radius-lg, 12px)',
            padding: '48px 24px',
            textAlign: 'center',
            color: 'var(--text-secondary, #9ca3af)',
          }}
        >
          <Clock size={36} style={{ color: 'var(--text-tertiary, #6b7280)', marginBottom: '12px' }} />
          <h3 style={{ margin: '0 0 6px 0', color: 'var(--text-primary, #ffffff)', fontSize: '1.1rem' }}>
            No queue items found
          </h3>
          <p style={{ margin: 0, fontSize: '0.875rem' }}>
            {selectedFilter === 'all'
              ? 'When applications are approved for submission, they will appear here.'
              : `No items matching filter "${selectedFilter}".`}
          </p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {filteredItems.map((item) => {
            const isPending = item.queue_state.toUpperCase() === 'APPROVED_PENDING';
            const isClaimed = item.queue_state.toUpperCase() === 'CLAIMED_IN_PROGRESS';
            const isManual = item.queue_state.toUpperCase() === 'MANUAL_REVIEW';

            return (
              <div
                key={item.application_id}
                style={{
                  background: 'var(--bg-card, #181622)',
                  border: isPending
                    ? '1px solid rgba(245, 158, 11, 0.35)'
                    : '1px solid var(--border-subtle, #1e1b2e)',
                  borderRadius: 'var(--radius-lg, 12px)',
                  padding: '18px 20px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '12px',
                  transition: 'border-color 0.2s',
                }}
              >
                {/* Card Top Row */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    justifyContent: 'space-between',
                    flexWrap: 'wrap',
                    gap: '12px',
                  }}
                >
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                      <span
                        style={{
                          fontSize: '0.75rem',
                          fontFamily: 'monospace',
                          padding: '2px 6px',
                          borderRadius: '4px',
                          background: 'rgba(255, 255, 255, 0.06)',
                          color: 'var(--text-tertiary, #9ca3af)',
                        }}
                      >
                        App #{item.application_id}
                      </span>
                      <h2
                        style={{
                          margin: 0,
                          fontSize: '1.05rem',
                          fontWeight: 600,
                          color: 'var(--text-primary, #ffffff)',
                        }}
                      >
                        {item.opportunity_title || `Opportunity #${item.opportunity_id}`}
                      </h2>
                      <span style={{ fontSize: '0.9rem', color: 'var(--text-secondary, #a1a1aa)' }}>
                        • {item.opportunity_company || 'Unknown Company'}
                      </span>
                    </div>

                    {item.opportunity_url && (
                      <a
                        href={item.opportunity_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '4px',
                          fontSize: '0.75rem',
                          color: '#818cf8',
                          marginTop: '4px',
                          textDecoration: 'none',
                        }}
                      >
                        <span>Open listing</span>
                        <ExternalLink size={12} />
                      </a>
                    )}
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    {renderStateBadge(item.queue_state)}

                    {/* Action Button */}
                    {isPending && (
                      <button
                        onClick={() => handleOpenCancelModal(item)}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '6px',
                          padding: '6px 14px',
                          borderRadius: 'var(--radius-md, 8px)',
                          background: 'rgba(239, 68, 68, 0.12)',
                          border: '1px solid rgba(239, 68, 68, 0.3)',
                          color: '#f87171',
                          fontSize: '0.8rem',
                          fontWeight: 600,
                          cursor: 'pointer',
                          transition: 'all 0.15s',
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.background = 'rgba(239, 68, 68, 0.22)';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = 'rgba(239, 68, 68, 0.12)';
                        }}
                      >
                        <Ban size={13} />
                        <span>Cancel Approval</span>
                      </button>
                    )}
                  </div>
                </div>

                {/* Card Metadata Details */}
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                    gap: '12px',
                    padding: '12px 14px',
                    borderRadius: 'var(--radius-md, 8px)',
                    background: 'rgba(0, 0, 0, 0.2)',
                    fontSize: '0.8rem',
                  }}
                >
                  {item.approved_at && (
                    <div>
                      <span style={{ color: 'var(--text-tertiary, #6b7280)', display: 'block' }}>Approved At:</span>
                      <span style={{ color: 'var(--text-secondary, #d1d5db)' }}>
                        {new Date(item.approved_at).toLocaleString()} {item.approved_by ? `by ${item.approved_by}` : ''}
                      </span>
                    </div>
                  )}

                  {item.claimed_at && (
                    <div>
                      <span style={{ color: 'var(--text-tertiary, #6b7280)', display: 'block' }}>Claimed By Worker:</span>
                      <span style={{ color: '#60a5fa' }}>
                        {item.claimed_by || 'worker'} at {new Date(item.claimed_at).toLocaleTimeString()}
                      </span>
                    </div>
                  )}

                  {item.approval_revoked_at && (
                    <div>
                      <span style={{ color: 'var(--text-tertiary, #6b7280)', display: 'block' }}>Revoked:</span>
                      <span style={{ color: '#f87171' }}>
                        {new Date(item.approval_revoked_at).toLocaleString()}{' '}
                        {item.approval_revoked_by ? `by ${item.approval_revoked_by}` : ''}
                      </span>
                      {item.approval_revocation_reason && (
                        <div style={{ color: 'var(--text-tertiary, #9ca3af)', fontStyle: 'italic', marginTop: '2px' }}>
                          "{item.approval_revocation_reason}"
                        </div>
                      )}
                    </div>
                  )}

                  {item.confirmation_ref && (
                    <div>
                      <span style={{ color: 'var(--text-tertiary, #6b7280)', display: 'block' }}>Confirmation Ref:</span>
                      <span style={{ color: 'var(--emerald-400, #34d399)', fontFamily: 'monospace' }}>
                        {item.confirmation_ref}
                      </span>
                    </div>
                  )}

                  {isManual && item.manual_review_reason && (
                    <div>
                      <span style={{ color: 'var(--text-tertiary, #6b7280)', display: 'block' }}>Manual Review Reason:</span>
                      <span style={{ color: '#fb923c', fontFamily: 'monospace' }}>
                        {MANUAL_REVIEW_LABELS[item.manual_review_reason] || 'Manual review required'}
                      </span>
                    </div>
                  )}

                  <div>
                    <span style={{ color: 'var(--text-tertiary, #6b7280)', display: 'block' }}>Internal Status:</span>
                    <span style={{ color: 'var(--text-secondary, #d1d5db)', fontFamily: 'monospace' }}>
                      {item.application_status}
                    </span>
                  </div>
                </div>

                {/* State explanations */}
                {isClaimed && (
                  <div style={{ fontSize: '0.775rem', color: '#93c5fd' }}>
                    ⚡ Background worker is currently filling / submitting this form. Cancellation is locked.
                  </div>
                )}
                {isManual && (
                  <div style={{ fontSize: '0.775rem', color: '#fdba74' }}>
                    ⚠️ Application requires manual intervention or user review.
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Revoke Confirmation Modal */}
      {cancelingItem && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 999,
            background: 'rgba(0, 0, 0, 0.75)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '20px',
          }}
        >
          <div
            style={{
              width: '100%',
              maxWidth: '480px',
              background: 'var(--bg-card, #181622)',
              border: '1px solid var(--border-medium, #2e2b3e)',
              borderRadius: 'var(--radius-lg, 12px)',
              padding: '24px',
              boxShadow: '0 20px 40px rgba(0,0,0,0.6)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '14px' }}>
              <div
                style={{
                  width: '36px',
                  height: '36px',
                  borderRadius: '50%',
                  background: 'rgba(239, 68, 68, 0.15)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#f87171',
                }}
              >
                <Ban size={20} />
              </div>
              <h3 style={{ margin: 0, fontSize: '1.2rem', color: 'var(--text-primary, #ffffff)' }}>
                Cancel Pending Approval
              </h3>
            </div>

            <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary, #d1d5db)', marginBottom: '16px' }}>
              Are you sure you want to cancel approval for{' '}
              <strong>{cancelingItem.opportunity_title || `App #${cancelingItem.application_id}`}</strong> at{' '}
              <strong>{cancelingItem.opportunity_company || 'the employer'}</strong>?
            </p>

            <p style={{ fontSize: '0.8rem', color: 'var(--text-tertiary, #9ca3af)', marginBottom: '16px' }}>
              This will atomically withdraw authorization so background workers will NOT submit this application.
              Revocation is recorded in the audit trail.
            </p>

            {revokeError && (
              <div
                style={{
                  padding: '10px 14px',
                  borderRadius: 'var(--radius-md, 8px)',
                  background: 'rgba(239, 68, 68, 0.15)',
                  border: '1px solid rgba(239, 68, 68, 0.3)',
                  color: '#fca5a5',
                  fontSize: '0.825rem',
                  marginBottom: '16px',
                }}
              >
                {revokeError}
              </div>
            )}

            <div style={{ marginBottom: '20px' }}>
              <label
                style={{
                  display: 'block',
                  fontSize: '0.8rem',
                  fontWeight: 500,
                  color: 'var(--text-secondary, #d1d5db)',
                  marginBottom: '6px',
                }}
              >
                Optional Reason:
              </label>
              <textarea
                value={cancelReason}
                onChange={(e) => setCancelReason(e.target.value)}
                placeholder="e.g., Wrong resume selected, position no longer desired..."
                rows={3}
                style={{
                  width: '100%',
                  padding: '10px',
                  borderRadius: 'var(--radius-md, 8px)',
                  background: 'var(--bg-surface, #100e19)',
                  border: '1px solid var(--border-medium, #2e2b3e)',
                  color: 'var(--text-primary, #ffffff)',
                  fontSize: '0.85rem',
                  boxSizing: 'border-box',
                  resize: 'vertical',
                }}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
              <button
                onClick={handleCloseCancelModal}
                disabled={isRevoking}
                style={{
                  padding: '8px 16px',
                  borderRadius: 'var(--radius-md, 8px)',
                  background: 'transparent',
                  border: '1px solid var(--border-medium, #2e2b3e)',
                  color: 'var(--text-secondary, #d1d5db)',
                  fontSize: '0.875rem',
                  cursor: 'pointer',
                }}
              >
                Keep Approval
              </button>
              <button
                onClick={handleConfirmRevoke}
                disabled={isRevoking}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '8px 18px',
                  borderRadius: 'var(--radius-md, 8px)',
                  background: '#dc2626',
                  border: 'none',
                  color: '#ffffff',
                  fontSize: '0.875rem',
                  fontWeight: 600,
                  cursor: isRevoking ? 'not-allowed' : 'pointer',
                }}
              >
                {isRevoking && <Loader2 size={14} className="animate-spin" />}
                <span>Confirm Cancellation</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SubmissionQueuePage;
