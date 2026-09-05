import React, { useEffect, useState, useCallback } from 'react';
import {
  Inbox,
  Mail,
  Link2,
  LinkIcon,
  Unlink,
  Calendar,
  XCircle,
  Gift,
  MessageSquare,
  HelpCircle,
  MailCheck,
  RefreshCw,
  Activity,
  CheckCircle2,
  AlertTriangle,
  XOctagon,
  ChevronDown,
  ExternalLink,
  Clock,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  RecruiterMessage,
  MessageStats,
  IntegrationHealthEvent,
  MessageClassification,
} from '../types';

const CLASSIFICATION_CONFIG: Record<
  string,
  { label: string; color: string; bg: string; icon: React.ReactNode }
> = {
  interview_invite: {
    label: 'Interview',
    color: '#34d399',
    bg: 'rgba(16, 185, 129, 0.12)',
    icon: <Calendar size={13} />,
  },
  rejection: {
    label: 'Rejection',
    color: '#f87171',
    bg: 'rgba(239, 68, 68, 0.12)',
    icon: <XCircle size={13} />,
  },
  offer: {
    label: 'Offer',
    color: '#fbbf24',
    bg: 'rgba(245, 158, 11, 0.12)',
    icon: <Gift size={13} />,
  },
  follow_up: {
    label: 'Follow-up',
    color: '#a5b4fc',
    bg: 'rgba(99, 102, 241, 0.12)',
    icon: <MessageSquare size={13} />,
  },
  screening_question: {
    label: 'Screening',
    color: '#f97316',
    bg: 'rgba(249, 115, 22, 0.12)',
    icon: <HelpCircle size={13} />,
  },
  generic: {
    label: 'Generic',
    color: 'var(--text-tertiary)',
    bg: 'rgba(255, 255, 255, 0.04)',
    icon: <MailCheck size={13} />,
  },
  unclassified: {
    label: 'Unclassified',
    color: 'var(--text-tertiary)',
    bg: 'rgba(255, 255, 255, 0.04)',
    icon: <Mail size={13} />,
  },
};

const ALL_FILTERS: (MessageClassification | 'all')[] = [
  'all',
  'interview_invite',
  'rejection',
  'offer',
  'screening_question',
  'follow_up',
  'generic',
  'unclassified',
];

function formatTime(dateStr: string | null | undefined): string {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'Just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHrs = Math.floor(diffMin / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  const diffDays = Math.floor(diffHrs / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function getHealthStatus(events: IntegrationHealthEvent[]): {
  status: 'healthy' | 'warning' | 'error' | 'unknown';
  label: string;
  color: string;
  bg: string;
  detail: string;
} {
  if (events.length === 0) {
    return {
      status: 'unknown',
      label: 'No data',
      color: 'var(--text-tertiary)',
      bg: 'rgba(255,255,255,0.04)',
      detail: 'Response poller has not run yet',
    };
  }
  const latest = events[0];
  const age = Date.now() - new Date(latest.occurred_at).getTime();
  const ageMin = age / 60000;

  if (latest.event_type === 'token_refresh_failure' || latest.event_type === 'auth_expired') {
    return {
      status: 'error',
      label: 'Auth Error',
      color: '#f87171',
      bg: 'rgba(239, 68, 68, 0.12)',
      detail: latest.detail || 'OAuth token refresh failed',
    };
  }
  if (latest.event_type === 'poll_error') {
    return {
      status: 'warning',
      label: 'Poll Error',
      color: '#fbbf24',
      bg: 'rgba(245, 158, 11, 0.12)',
      detail: latest.detail || 'Last poll encountered an error',
    };
  }
  if (ageMin > 10) {
    return {
      status: 'warning',
      label: 'Stale',
      color: '#fbbf24',
      bg: 'rgba(245, 158, 11, 0.12)',
      detail: `Last successful poll was ${Math.floor(ageMin)}m ago`,
    };
  }
  return {
    status: 'healthy',
    label: 'Online',
    color: '#34d399',
    bg: 'rgba(16, 185, 129, 0.1)',
    detail: `Last poll ${formatTime(latest.occurred_at)}`,
  };
}

export const ResponseCenterPlaceholder: React.FC = () => {
  const [messages, setMessages] = useState<RecruiterMessage[]>([]);
  const [stats, setStats] = useState<MessageStats | null>(null);
  const [healthEvents, setHealthEvents] = useState<IntegrationHealthEvent[]>([]);
  const [activeFilter, setActiveFilter] = useState<MessageClassification | 'all'>('all');
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [msgRes, statsRes, healthRes] = await Promise.all([
        api.getMessages({
          classification: activeFilter === 'all' ? undefined : activeFilter,
          limit: 50,
        }),
        api.getMessageStats(),
        api.getIntegrationHealth('gmail_response_poller', 5),
      ]);
      setMessages(msgRes.items);
      setTotal(msgRes.total);
      setStats(statsRes);
      setHealthEvents(healthRes.items);
    } catch (err: any) {
      setError(err?.message || 'Failed to load messages');
    } finally {
      setLoading(false);
    }
  }, [activeFilter]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const health = getHealthStatus(healthEvents);

  return (
    <div className="animate-fade-in" style={{ maxWidth: '1200px', margin: '0 auto' }}>
      {/* ── Page Header ──────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          marginBottom: 'var(--space-xl)',
          gap: 'var(--space-lg)',
          flexWrap: 'wrap',
        }}
      >
        <div>
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: '0.72rem',
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              color: '#c4b5fd',
              marginBottom: '6px',
            }}
          >
            <Inbox size={14} />
            Recruiter Communications
          </div>
          <h1
            className="editorial-title"
            style={{
              fontSize: '2.2rem',
              color: 'var(--text-primary)',
              margin: '0 0 6px 0',
            }}
          >
            Response Center
          </h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', margin: 0 }}>
            Classified recruiter replies linked to your active applications.
          </p>
        </div>

        {/* Health indicator */}
        <div
          title={health.detail}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 14px',
            borderRadius: 'var(--radius-full)',
            background: health.bg,
            border: `1px solid ${health.color}33`,
            fontSize: '0.78rem',
            fontWeight: 500,
            color: health.color,
            cursor: 'default',
            whiteSpace: 'nowrap',
          }}
        >
          {health.status === 'healthy' && <CheckCircle2 size={14} />}
          {health.status === 'warning' && <AlertTriangle size={14} />}
          {health.status === 'error' && <XOctagon size={14} />}
          {health.status === 'unknown' && <Activity size={14} />}
          Gmail Poller: {health.label}
        </div>
      </div>

      {/* ── Stats Row ────────────────────────────────────────────────── */}
      {stats && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
            gap: '14px',
            marginBottom: 'var(--space-xl)',
          }}
        >
          {[
            {
              label: 'Total Messages',
              value: stats.total,
              icon: <Mail size={16} />,
              color: 'var(--purple-500)',
              bg: 'var(--purple-glow)',
            },
            {
              label: 'Classified',
              value: stats.total - (stats.by_classification['unclassified'] || 0),
              icon: <MailCheck size={16} />,
              color: 'var(--indigo-500)',
              bg: 'var(--indigo-glow)',
            },
            {
              label: 'Linked',
              value: stats.linked,
              icon: <Link2 size={16} />,
              color: 'var(--emerald-400)',
              bg: 'var(--emerald-soft)',
            },
            {
              label: 'Unlinked',
              value: stats.unlinked,
              icon: <Unlink size={16} />,
              color: 'var(--amber-400)',
              bg: 'var(--amber-soft)',
            },
          ].map((stat) => (
            <div
              key={stat.label}
              style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-lg)',
                padding: '18px 20px',
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
              }}
            >
              <div
                style={{
                  width: '36px',
                  height: '36px',
                  borderRadius: 'var(--radius-md)',
                  background: stat.bg,
                  color: stat.color,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                {stat.icon}
              </div>
              <div>
                <div
                  style={{
                    fontSize: '1.4rem',
                    fontWeight: 700,
                    color: 'var(--text-primary)',
                    lineHeight: 1.1,
                  }}
                >
                  {stat.value}
                </div>
                <div
                  style={{
                    fontSize: '0.72rem',
                    color: 'var(--text-tertiary)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.04em',
                    fontWeight: 500,
                  }}
                >
                  {stat.label}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Filter Chips ─────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          marginBottom: 'var(--space-lg)',
          flexWrap: 'wrap',
        }}
      >
        {ALL_FILTERS.map((filter) => {
          const isActive = activeFilter === filter;
          const cfg = filter === 'all' ? null : CLASSIFICATION_CONFIG[filter];
          return (
            <button
              key={filter}
              onClick={() => setActiveFilter(filter)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                padding: '6px 14px',
                borderRadius: 'var(--radius-full)',
                fontSize: '0.78rem',
                fontWeight: 500,
                border: isActive
                  ? `1px solid ${cfg?.color || 'var(--purple-500)'}55`
                  : '1px solid var(--border-subtle)',
                background: isActive
                  ? cfg?.bg || 'var(--purple-glow)'
                  : 'transparent',
                color: isActive
                  ? cfg?.color || 'var(--purple-500)'
                  : 'var(--text-secondary)',
                cursor: 'pointer',
                transition: 'all var(--transition-fast)',
              }}
            >
              {cfg?.icon}
              {filter === 'all' ? 'All' : cfg?.label}
              {stats && filter !== 'all' && (
                <span style={{ opacity: 0.7, fontSize: '0.7rem' }}>
                  {stats.by_classification[filter] || 0}
                </span>
              )}
            </button>
          );
        })}

        <button
          onClick={fetchData}
          title="Refresh"
          style={{
            marginLeft: 'auto',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '5px',
            padding: '6px 12px',
            borderRadius: 'var(--radius-md)',
            fontSize: '0.78rem',
            fontWeight: 500,
            border: '1px solid var(--border-subtle)',
            background: 'transparent',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
          }}
        >
          <RefreshCw size={13} className={loading ? 'spin' : ''} />
          Refresh
        </button>
      </div>

      {/* ── Error State ──────────────────────────────────────────────── */}
      {error && (
        <div
          style={{
            background: 'var(--rose-soft)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            borderRadius: 'var(--radius-md)',
            padding: '14px 18px',
            color: 'var(--rose-400)',
            fontSize: '0.85rem',
            marginBottom: 'var(--space-lg)',
          }}
        >
          {error}
        </div>
      )}

      {/* ── Empty State ──────────────────────────────────────────────── */}
      {!loading && !error && messages.length === 0 && (
        <div
          style={{
            textAlign: 'center',
            padding: 'var(--space-2xl) var(--space-xl)',
            background: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-lg)',
          }}
        >
          <div
            style={{
              width: '56px',
              height: '56px',
              borderRadius: 'var(--radius-xl)',
              background: 'rgba(139, 92, 246, 0.1)',
              border: '1px solid rgba(139, 92, 246, 0.25)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              margin: '0 auto 16px',
              color: 'var(--purple-500)',
            }}
          >
            <Inbox size={28} />
          </div>
          <h3
            style={{
              color: 'var(--text-primary)',
              fontSize: '1.15rem',
              fontWeight: 600,
              margin: '0 0 8px 0',
            }}
          >
            {activeFilter === 'all' ? 'No messages yet' : `No ${CLASSIFICATION_CONFIG[activeFilter]?.label || activeFilter} messages`}
          </h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', margin: 0 }}>
            {activeFilter === 'all'
              ? 'When the response poller detects recruiter replies, they'll appear here — classified and linked to your applications.'
              : 'Try selecting a different filter or wait for new messages.'}
          </p>
        </div>
      )}

      {/* ── Message List ─────────────────────────────────────────────── */}
      {messages.length > 0 && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
          }}
        >
          {messages.map((msg) => {
            const cfg = CLASSIFICATION_CONFIG[msg.classification || 'unclassified'] ||
              CLASSIFICATION_CONFIG['unclassified'];
            const isExpanded = expandedId === msg.id;

            return (
              <div
                key={msg.id}
                onClick={() => setExpandedId(isExpanded ? null : msg.id)}
                style={{
                  background: 'var(--bg-card)',
                  border: `1px solid ${isExpanded ? 'var(--border-medium)' : 'var(--border-subtle)'}`,
                  borderRadius: 'var(--radius-lg)',
                  padding: '16px 20px',
                  cursor: 'pointer',
                  transition: 'all var(--transition-fast)',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLDivElement).style.background = 'var(--bg-card-hover)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLDivElement).style.background = 'var(--bg-card)';
                }}
              >
                {/* Main Row */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '14px',
                  }}
                >
                  {/* Classification badge */}
                  <div
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '5px',
                      padding: '4px 10px',
                      borderRadius: 'var(--radius-full)',
                      fontSize: '0.72rem',
                      fontWeight: 600,
                      color: cfg.color,
                      background: cfg.bg,
                      whiteSpace: 'nowrap',
                      flexShrink: 0,
                      minWidth: '90px',
                      justifyContent: 'center',
                    }}
                  >
                    {cfg.icon}
                    {cfg.label}
                  </div>

                  {/* Sender & Subject */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        marginBottom: '3px',
                      }}
                    >
                      <span
                        style={{
                          fontSize: '0.88rem',
                          fontWeight: 600,
                          color: 'var(--text-primary)',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {msg.sender_domain || msg.sender?.split('@')[1]?.split('>')[0] || 'Unknown'}
                      </span>
                      {msg.classification_source === 'llm' && (
                        <span
                          style={{
                            fontSize: '0.6rem',
                            padding: '1px 5px',
                            borderRadius: '4px',
                            background: 'rgba(139, 92, 246, 0.12)',
                            color: '#c4b5fd',
                            fontWeight: 600,
                          }}
                        >
                          LLM
                        </span>
                      )}
                    </div>
                    <div
                      style={{
                        fontSize: '0.82rem',
                        color: 'var(--text-secondary)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {msg.subject || 'No subject'}
                    </div>
                  </div>

                  {/* Linked opportunity */}
                  <div style={{ flexShrink: 0, textAlign: 'right', minWidth: '140px' }}>
                    {msg.application_id ? (
                      <div
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '5px',
                          fontSize: '0.75rem',
                          color: 'var(--emerald-400)',
                          fontWeight: 500,
                        }}
                      >
                        <LinkIcon size={12} />
                        <span style={{ maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {msg.opportunity_company || `App #${msg.application_id}`}
                        </span>
                      </div>
                    ) : (
                      <span
                        style={{
                          fontSize: '0.72rem',
                          color: msg.link_confidence === 'low' ? 'var(--amber-400)' : 'var(--text-tertiary)',
                          fontWeight: 500,
                        }}
                      >
                        {msg.link_confidence === 'low' ? 'Ambiguous' : 'Unlinked'}
                      </span>
                    )}
                  </div>

                  {/* Time & Expand */}
                  <div
                    style={{
                      flexShrink: 0,
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      color: 'var(--text-tertiary)',
                      fontSize: '0.75rem',
                    }}
                  >
                    <Clock size={12} />
                    {formatTime(msg.received_at)}
                    <ChevronDown
                      size={14}
                      style={{
                        transform: isExpanded ? 'rotate(180deg)' : 'rotate(0)',
                        transition: 'transform var(--transition-fast)',
                      }}
                    />
                  </div>
                </div>

                {/* Expanded Detail */}
                {isExpanded && (
                  <div
                    style={{
                      marginTop: '14px',
                      paddingTop: '14px',
                      borderTop: '1px solid var(--border-subtle)',
                    }}
                  >
                    <div
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '1fr 1fr',
                        gap: '12px',
                        marginBottom: '14px',
                      }}
                    >
                      <div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '4px' }}>
                          From
                        </div>
                        <div style={{ fontSize: '0.82rem', color: 'var(--text-primary)' }}>
                          {msg.sender}
                        </div>
                      </div>
                      <div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '4px' }}>
                          Received
                        </div>
                        <div style={{ fontSize: '0.82rem', color: 'var(--text-primary)' }}>
                          {msg.received_at
                            ? new Date(msg.received_at).toLocaleString()
                            : '—'}
                        </div>
                      </div>
                      {msg.opportunity_title && (
                        <div>
                          <div style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '4px' }}>
                            Linked Opportunity
                          </div>
                          <div style={{ fontSize: '0.82rem', color: 'var(--emerald-400)' }}>
                            {msg.opportunity_title} — {msg.opportunity_company}
                          </div>
                        </div>
                      )}
                      <div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '4px' }}>
                          Classification
                        </div>
                        <div style={{ fontSize: '0.82rem', color: cfg.color, display: 'flex', alignItems: 'center', gap: '6px' }}>
                          {cfg.icon} {cfg.label}
                          <span style={{ color: 'var(--text-tertiary)', fontSize: '0.72rem' }}>
                            via {msg.classification_source || 'unknown'}
                            {msg.classification_confidence != null && ` (${(msg.classification_confidence * 100).toFixed(0)}%)`}
                          </span>
                        </div>
                      </div>
                    </div>
                    {msg.body_preview && (
                      <div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '6px' }}>
                          Preview
                        </div>
                        <div
                          style={{
                            fontSize: '0.82rem',
                            color: 'var(--text-secondary)',
                            lineHeight: 1.6,
                            background: 'rgba(255, 255, 255, 0.02)',
                            borderRadius: 'var(--radius-md)',
                            padding: '12px 14px',
                            border: '1px solid var(--border-subtle)',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                          }}
                        >
                          {msg.body_preview}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {/* Pagination hint */}
          {total > messages.length && (
            <div
              style={{
                textAlign: 'center',
                padding: '12px',
                fontSize: '0.8rem',
                color: 'var(--text-tertiary)',
              }}
            >
              Showing {messages.length} of {total} messages
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export const ResponseCenterPage = ResponseCenterPlaceholder;
