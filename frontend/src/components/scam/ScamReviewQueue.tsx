import React, { useState, useEffect } from 'react';
import {
  ShieldAlert,
  ShieldCheck,
  XCircle,
  AlertTriangle,
  Bot,
  RefreshCw,
  Loader2,
} from 'lucide-react';
import type { DashboardOpportunityItem } from '../../types';
import { ReliabilityBadge } from '../common/Badge';
import { api } from '../../api/client';

export const ScamReviewQueue: React.FC = () => {
  const [items, setItems] = useState<DashboardOpportunityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionInProgress, setActionInProgress] = useState<number | null>(null);
  const [notification, setNotification] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const fetchQueue = async () => {
    setLoading(true);
    try {
      const pendingItems = await api.getPendingScamReviews(50, 0);
      setItems(pendingItems);
    } catch (err: any) {
      setNotification({
        text: err?.message || 'Failed to load scam review queue',
        type: 'error',
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchQueue();
  }, []);

  const handleClear = async (id: number) => {
    setActionInProgress(id);
    setNotification(null);
    try {
      await api.clearScamReview(id);
      setItems((prev) => prev.filter((item) => item.id !== id));
      setNotification({
        text: `Opportunity #${id} cleared! Listing returned to funnel for Stage 3 Relevance Scoring.`,
        type: 'success',
      });
    } catch (err: any) {
      setNotification({
        text: err?.message || `Failed to clear opportunity #${id}`,
        type: 'error',
      });
    } finally {
      setActionInProgress(null);
    }
  };

  const handleReject = async (id: number) => {
    setActionInProgress(id);
    setNotification(null);
    try {
      await api.rejectScamReview(id, 'Confirmed scam during human review');
      setItems((prev) => prev.filter((item) => item.id !== id));
      setNotification({
        text: `Opportunity #${id} rejected as scam and content hash recorded.`,
        type: 'success',
      });
    } catch (err: any) {
      setNotification({
        text: err?.message || `Failed to reject opportunity #${id}`,
        type: 'error',
      });
    } finally {
      setActionInProgress(null);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '28px' }}>
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '16px',
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
              color: 'var(--amber-400)',
              marginBottom: '6px',
            }}
          >
            <ShieldAlert size={14} />
            Stage 2 Funnel Gate
          </div>
          <h1
            className="editorial-title"
            style={{ fontSize: '2.1rem', color: 'var(--text-primary)', margin: 0 }}
          >
            Scam & Risk Review Queue
          </h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginTop: '6px', maxWidth: '780px' }}>
            Opportunities flagged as <em>ambiguous</em> by deterministic rules or Gemini LLM evaluations.
            Clearing an item confirms it is legitimate and forwards it to Stage 3 for relevance scoring.
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
            borderRadius: 'var(--radius-md)',
            fontSize: '0.85rem',
            fontWeight: 500,
            background: 'rgba(255, 255, 255, 0.05)',
            color: 'var(--text-secondary)',
            border: '1px solid var(--border-medium)',
            cursor: loading ? 'not-allowed' : 'pointer',
            transition: 'all var(--transition-fast)',
          }}
        >
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          <span>Refresh Queue</span>
        </button>
      </div>

      {/* ── Notification Banner ─────────────────────────────────────── */}
      {notification && (
        <div
          className="animate-fade-in"
          style={{
            padding: '12px 18px',
            borderRadius: 'var(--radius-md)',
            fontSize: '0.85rem',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            background:
              notification.type === 'success'
                ? 'rgba(16, 185, 129, 0.12)'
                : 'rgba(239, 68, 68, 0.12)',
            border:
              notification.type === 'success'
                ? '1px solid rgba(16, 185, 129, 0.3)'
                : '1px solid rgba(239, 68, 68, 0.3)',
            color:
              notification.type === 'success' ? 'var(--emerald-400)' : 'var(--rose-400)',
          }}
        >
          {notification.type === 'success' ? <ShieldCheck size={18} /> : <AlertTriangle size={18} />}
          <span>{notification.text}</span>
        </div>
      )}

      {/* ── Queue List or Empty State ───────────────────────────────── */}
      {loading ? (
        <div
          style={{
            padding: '60px',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '14px',
            color: 'var(--text-tertiary)',
          }}
        >
          <Loader2 size={32} className="animate-spin" color="var(--purple-500)" />
          <span>Inspecting Stage 2 scam review queue...</span>
        </div>
      ) : items.length === 0 ? (
        <div
          style={{
            padding: '70px 24px',
            borderRadius: 'var(--radius-xl)',
            background: 'var(--bg-card)',
            border: '1px dashed var(--border-medium)',
            textAlign: 'center',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '12px',
          }}
        >
          <div
            style={{
              width: '48px',
              height: '48px',
              borderRadius: 'var(--radius-full)',
              background: 'rgba(16, 185, 129, 0.1)',
              border: '1px solid rgba(16, 185, 129, 0.25)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--emerald-400)',
            }}
          >
            <ShieldCheck size={26} />
          </div>
          <h2 style={{ fontSize: '1.2rem', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
            Queue Clean — Zero Ambiguous Cases
          </h2>
          <p style={{ color: 'var(--text-tertiary)', fontSize: '0.85rem', maxWidth: '460px', margin: 0 }}>
            No listings are currently held at Stage 2. Verified opportunities automatically flow through to
            Relevance Scoring and appear in the Opportunities Feed.
          </p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {items.map((item) => {
            const isProcessing = actionInProgress === item.id;
            return (
              <div
                key={item.id}
                style={{
                  borderRadius: 'var(--radius-xl)',
                  background: 'var(--bg-card)',
                  border: '1px solid rgba(245, 158, 11, 0.25)',
                  boxShadow: '0 4px 24px rgba(0, 0, 0, 0.5)',
                  padding: '24px 28px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '18px',
                }}
              >
                {/* Card Header */}
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
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                      <span style={{ fontSize: '0.95rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                        {item.company}
                      </span>
                      {item.source && (
                        <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>
                          via {item.source}
                        </span>
                      )}
                    </div>
                    <h2
                      className="editorial-title"
                      style={{ fontSize: '1.35rem', color: 'var(--text-primary)', margin: 0 }}
                    >
                      {item.title}
                    </h2>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <ReliabilityBadge tier={item.reliability_tier} />
                    <span
                      style={{
                        padding: '4px 10px',
                        borderRadius: 'var(--radius-full)',
                        fontSize: '0.72rem',
                        fontWeight: 600,
                        textTransform: 'uppercase',
                        background: 'rgba(245, 158, 11, 0.14)',
                        color: '#fcd34d',
                        border: '1px solid rgba(245, 158, 11, 0.32)',
                      }}
                    >
                      Ambiguous Risk
                    </span>
                  </div>
                </div>

                {/* Stated Reason & LLM Analysis Strip */}
                <div
                  style={{
                    borderRadius: 'var(--radius-md)',
                    background: 'rgba(245, 158, 11, 0.08)',
                    border: '1px solid rgba(245, 158, 11, 0.22)',
                    padding: '16px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Bot size={16} color="var(--amber-400)" />
                    <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--amber-400)' }}>
                      Stage 2 Inspection Signals
                    </span>
                    {item.llm_verdict && (
                      <span
                        style={{
                          fontSize: '0.7rem',
                          padding: '2px 6px',
                          borderRadius: '4px',
                          background: 'rgba(255, 255, 255, 0.08)',
                          color: '#ffffff',
                        }}
                      >
                        LLM: {item.llm_verdict}
                      </span>
                    )}
                  </div>

                  <div style={{ fontSize: '0.84rem', color: '#fef3c7', lineHeight: 1.5 }}>
                    {item.llm_reasoning ||
                      item.scam_reason?.detail ||
                      (item.scam_reason?.rule
                        ? `Triggered rule: ${item.scam_reason.rule}`
                        : 'Rule heuristics flagged non-standard compensation or atypical recruitment phrasing.')}
                  </div>
                </div>

                {/* Listing Snippet */}
                {item.description && (
                  <div
                    style={{
                      fontSize: '0.82rem',
                      color: 'var(--text-secondary)',
                      lineHeight: 1.5,
                      maxHeight: '120px',
                      overflowY: 'auto',
                      padding: '12px 14px',
                      borderRadius: 'var(--radius-md)',
                      background: 'rgba(255, 255, 255, 0.02)',
                      border: '1px solid var(--border-subtle)',
                    }}
                  >
                    {item.description}
                  </div>
                )}

                {/* Action Bar */}
                <div
                  style={{
                    paddingTop: '12px',
                    borderTop: '1px solid var(--border-subtle)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    flexWrap: 'wrap',
                    gap: '12px',
                  }}
                >
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                    ID: #{item.id} • Discovered {new Date(item.discovered_at).toLocaleDateString()}
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <button
                      onClick={() => handleReject(item.id)}
                      disabled={isProcessing}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '6px',
                        padding: '8px 16px',
                        borderRadius: 'var(--radius-md)',
                        fontSize: '0.82rem',
                        fontWeight: 600,
                        background: 'transparent',
                        color: 'var(--rose-400)',
                        border: '1px solid rgba(239, 68, 68, 0.35)',
                        cursor: isProcessing ? 'not-allowed' : 'pointer',
                        transition: 'all var(--transition-fast)',
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)';
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = 'transparent';
                      }}
                    >
                      <XCircle size={15} />
                      <span>Reject as Scam</span>
                    </button>

                    <button
                      onClick={() => handleClear(item.id)}
                      disabled={isProcessing}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '6px',
                        padding: '8px 20px',
                        borderRadius: 'var(--radius-md)',
                        fontSize: '0.82rem',
                        fontWeight: 600,
                        color: '#ffffff',
                        background: 'linear-gradient(135deg, var(--indigo-500), var(--purple-600))',
                        border: '1px solid rgba(99, 102, 241, 0.4)',
                        boxShadow: '0 4px 16px rgba(99, 102, 241, 0.25)',
                        cursor: isProcessing ? 'not-allowed' : 'pointer',
                        transition: 'all var(--transition-fast)',
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.transform = 'translateY(-1px)';
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.transform = 'translateY(0)';
                      }}
                    >
                      {isProcessing ? (
                        <Loader2 size={15} className="animate-spin" />
                      ) : (
                        <ShieldCheck size={15} />
                      )}
                      <span>Clear Listing (Proceed to Funnel)</span>
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
