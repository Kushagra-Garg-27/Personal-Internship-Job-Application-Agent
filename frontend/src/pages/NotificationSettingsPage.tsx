import React, { useEffect, useState, useCallback } from 'react';
import {
  Bell,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Send,
  RefreshCw,
  Sliders,
  ShieldAlert,
  Clock,
  ExternalLink,
  MessageSquare,
  Sparkles,
  Zap,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  NotificationSettings,
  NotificationLogItem,
  TestNotificationResponse,
} from '../types';

const EVENT_METADATA: Record<
  string,
  { label: string; description: string; tagColor: string; defaultOn: boolean }
> = {
  interview_invite: {
    label: 'Interview Invitations',
    description: 'Recruiter scheduling requests, phone screens, and interview invites',
    tagColor: '#34d399',
    defaultOn: true,
  },
  offer: {
    label: 'Job Offers',
    description: 'Formal job offers, compensation packages, and offer letters',
    tagColor: '#fbbf24',
    defaultOn: true,
  },
  screening_question: {
    label: 'Screening & Assessments',
    description: 'Pre-interview questionnaires, take-home tasks, and coding challenges',
    tagColor: '#f97316',
    defaultOn: true,
  },
  follow_up: {
    label: 'Recruiter Follow-ups',
    description: 'Status updates and check-ins on existing submitted applications',
    tagColor: '#a5b4fc',
    defaultOn: true,
  },
  rejection: {
    label: 'Application Rejections',
    description: 'Not moving forward notifications and position filled updates',
    tagColor: '#f87171',
    defaultOn: true,
  },
  unclassified: {
    label: 'Unclassified Messages',
    description: 'Inbound recruiter replies that could not be confidently categorized',
    tagColor: '#94a3b8',
    defaultOn: true,
  },
  integration_unhealthy: {
    label: 'Integration Health Alerts',
    description: 'OAuth token expiration, refresh failures, and inbox polling errors',
    tagColor: '#ef4444',
    defaultOn: true,
  },
  quota_exhausted: {
    label: 'AI Quota Exhaustion Alerts',
    description: 'Gemini free-tier daily quota exhaustion causing fail-closed item deferrals',
    tagColor: '#eab308',
    defaultOn: true,
  },
  channel_degraded: {
    label: 'Channel Degraded Warnings',
    description: 'Alert sent via Telegram when the optional secondary mirror provider fails',
    tagColor: '#f59e0b',
    defaultOn: true,
  },
  generic: {
    label: 'Generic Auto-Replies',
    description: 'Automated receipt confirmations ("We received your application")',
    tagColor: '#64748b',
    defaultOn: false,
  },
};

function formatTime(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'Just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHrs = Math.floor(diffMin / 60);
    if (diffHrs < 24) return `${diffHrs}h ago`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch {
    return dateStr;
  }
}

export const NotificationSettingsPage: React.FC = () => {
  const [settings, setSettings] = useState<NotificationSettings | null>(null);
  const [logs, setLogs] = useState<NotificationLogItem[]>([]);
  const [totalLogs, setTotalLogs] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestNotificationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [logFilter, setLogFilter] = useState<'all' | 'telegram' | 'whatsapp' | 'failed'>('all');

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [settingsData, logsData] = await Promise.all([
        api.getNotificationSettings(),
        api.getNotificationLogs({ limit: 40 }),
      ]);
      setSettings(settingsData);
      setLogs(logsData.items);
      setTotalLogs(logsData.total);
    } catch (err: any) {
      setError(err?.message || 'Failed to load notification settings.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleToggleWhatsApp = async () => {
    if (!settings) return;
    try {
      setSaving(true);
      const newEnabled = !settings.whatsapp_enabled;
      const updated = await api.updateNotificationSettings({ whatsapp_enabled: newEnabled });
      setSettings(updated);
    } catch (err: any) {
      alert(`Failed to update WhatsApp setting: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleToggleEvent = async (eventType: string) => {
    if (!settings) return;
    try {
      setSaving(true);
      const current = settings.enabled_events[eventType] ?? EVENT_METADATA[eventType]?.defaultOn ?? true;
      const updatedEvents = {
        ...settings.enabled_events,
        [eventType]: !current,
      };
      const updated = await api.updateNotificationSettings({ enabled_events: updatedEvents });
      setSettings(updated);
    } catch (err: any) {
      alert(`Failed to update event filter: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleSendTest = async () => {
    try {
      setTesting(true);
      setTestResult(null);
      const res = await api.sendTestNotification('test_event');
      setTestResult(res);
      // Refresh logs to show the new test log entry
      const logsData = await api.getNotificationLogs({ limit: 40 });
      setLogs(logsData.items);
      setTotalLogs(logsData.total);
    } catch (err: any) {
      alert(`Test notification failed: ${err.message}`);
    } finally {
      setTesting(false);
    }
  };

  const filteredLogs = logs.filter((log) => {
    if (logFilter === 'all') return true;
    if (logFilter === 'telegram') return log.channel === 'telegram';
    if (logFilter === 'whatsapp') return log.channel === 'whatsapp';
    if (logFilter === 'failed') return log.status === 'failed';
    return true;
  });

  return (
    <div style={{ maxWidth: '1100px', margin: '0 auto', paddingBottom: '60px' }}>
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          marginBottom: '28px',
          flexWrap: 'wrap',
          gap: '16px',
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
            <h1
              style={{
                fontFamily: 'var(--font-serif)',
                fontSize: '2rem',
                fontWeight: 600,
                letterSpacing: '-0.02em',
                color: 'var(--text-primary)',
                margin: 0,
              }}
            >
              Notification Settings
            </h1>
            <span
              style={{
                fontSize: '0.72rem',
                fontWeight: 700,
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
                padding: '3px 8px',
                borderRadius: 'var(--radius-full)',
                background: 'rgba(99, 102, 241, 0.15)',
                color: '#a5b4fc',
                border: '1px solid rgba(99, 102, 241, 0.3)',
              }}
            >
              Phase 8 • MVP Complete
            </span>
          </div>
          <p style={{ margin: 0, fontSize: '0.88rem', color: 'var(--text-secondary)' }}>
            Configure active dispatch channels, tune notification event filters, and review delivery audit logs.
          </p>
        </div>

        <div style={{ display: 'flex', gap: '10px' }}>
          <button
            onClick={fetchData}
            disabled={loading}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '8px 14px',
              borderRadius: 'var(--radius-md)',
              background: 'rgba(255, 255, 255, 0.05)',
              border: '1px solid var(--border-medium)',
              color: 'var(--text-primary)',
              fontSize: '0.84rem',
              cursor: 'pointer',
              transition: 'background var(--transition-fast)',
            }}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            <span>Refresh</span>
          </button>

          <button
            onClick={handleSendTest}
            disabled={testing || loading}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '7px',
              padding: '8px 16px',
              borderRadius: 'var(--radius-md)',
              background: 'var(--accent-primary)',
              border: 'none',
              color: '#ffffff',
              fontSize: '0.84rem',
              fontWeight: 600,
              cursor: testing ? 'not-allowed' : 'pointer',
              boxShadow: '0 2px 10px rgba(99, 102, 241, 0.3)',
              transition: 'opacity var(--transition-fast)',
            }}
          >
            <Send size={14} />
            <span>{testing ? 'Sending Test…' : 'Send Test Alert'}</span>
          </button>
        </div>
      </div>

      {error && (
        <div
          style={{
            padding: '14px 18px',
            borderRadius: 'var(--radius-md)',
            background: 'rgba(239, 68, 68, 0.12)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            color: '#fca5a5',
            marginBottom: '24px',
            fontSize: '0.88rem',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
          }}
        >
          <AlertTriangle size={18} />
          <span>{error}</span>
        </div>
      )}

      {testResult && (
        <div
          style={{
            padding: '14px 18px',
            borderRadius: 'var(--radius-md)',
            background: testResult.success ? 'rgba(16, 185, 129, 0.12)' : 'rgba(239, 68, 68, 0.12)',
            border: `1px solid ${testResult.success ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`,
            color: testResult.success ? '#86efac' : '#fca5a5',
            marginBottom: '24px',
            fontSize: '0.88rem',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 600, marginBottom: '6px' }}>
            {testResult.success ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
            <span>{testResult.success ? 'Test notification dispatched successfully!' : 'Test notification failed'}</span>
          </div>
          <div style={{ fontSize: '0.82rem', opacity: 0.9 }}>
            {testResult.results.map((r, i) => (
              <span key={i} style={{ marginRight: '14px' }}>
                • <strong>{r.channel.toUpperCase()}:</strong> {r.success ? 'Delivered' : `Failed (${r.error})`}
              </span>
            ))}
            {testResult.channel_degraded_alert_sent && (
              <span style={{ color: '#fbbf24' }}>• Secondary channel degraded warning sent via Telegram</span>
            )}
          </div>
        </div>
      )}

      {/* Grid: Provider Cards */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          gap: '20px',
          marginBottom: '32px',
        }}
      >
        {/* Telegram Card */}
        <div
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-medium)',
            borderRadius: 'var(--radius-lg)',
            padding: '22px',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
          }}
        >
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div
                  style={{
                    width: '36px',
                    height: '36px',
                    borderRadius: '10px',
                    background: 'rgba(56, 189, 248, 0.15)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#38bdf8',
                  }}
                >
                  <Zap size={20} />
                </div>
                <div>
                  <h3 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                    Telegram Bot
                  </h3>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>Primary Required Channel</span>
                </div>
              </div>

              <span
                style={{
                  fontSize: '0.72rem',
                  fontWeight: 600,
                  padding: '3px 8px',
                  borderRadius: 'var(--radius-full)',
                  background: settings?.telegram_configured ? 'rgba(16, 185, 129, 0.12)' : 'rgba(245, 158, 11, 0.12)',
                  color: settings?.telegram_configured ? '#34d399' : '#fbbf24',
                  border: `1px solid ${settings?.telegram_configured ? 'rgba(16, 185, 129, 0.25)' : 'rgba(245, 158, 11, 0.25)'}`,
                }}
              >
                {settings?.telegram_configured ? 'Configured' : 'Credentials Missing'}
              </span>
            </div>

            <p style={{ fontSize: '0.84rem', color: 'var(--text-secondary)', lineHeight: 1.5, margin: '0 0 16px 0' }}>
              The non-negotiable, 100% free default notification channel. Operates via unverified Bot API with automatic
              retries and exponential backoff.
            </p>
          </div>

          <div
            style={{
              padding: '10px 14px',
              borderRadius: 'var(--radius-sm)',
              background: 'rgba(255, 255, 255, 0.03)',
              border: '1px solid var(--border-light)',
              fontSize: '0.78rem',
              color: 'var(--text-tertiary)',
            }}
          >
            🔒 Credentials set via <code>TELEGRAM_BOT_TOKEN</code> & <code>TELEGRAM_CHAT_ID</code> in <code>.env</code>
          </div>
        </div>

        {/* WhatsApp Card */}
        <div
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-medium)',
            borderRadius: 'var(--radius-lg)',
            padding: '22px',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
          }}
        >
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div
                  style={{
                    width: '36px',
                    height: '36px',
                    borderRadius: '10px',
                    background: 'rgba(37, 211, 102, 0.15)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#25d366',
                  }}
                >
                  <MessageSquare size={20} />
                </div>
                <div>
                  <h3 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                    WhatsApp Cloud API
                  </h3>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>Optional Secondary Mirror</span>
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span
                  style={{
                    fontSize: '0.72rem',
                    fontWeight: 600,
                    padding: '3px 8px',
                    borderRadius: 'var(--radius-full)',
                    background: settings?.whatsapp_enabled ? 'rgba(37, 211, 102, 0.12)' : 'rgba(255, 255, 255, 0.05)',
                    color: settings?.whatsapp_enabled ? '#25d366' : 'var(--text-tertiary)',
                    border: `1px solid ${settings?.whatsapp_enabled ? 'rgba(37, 211, 102, 0.25)' : 'var(--border-light)'}`,
                  }}
                >
                  {settings?.whatsapp_enabled ? 'Mirror Enabled' : 'Disabled'}
                </span>
                <button
                  onClick={handleToggleWhatsApp}
                  disabled={saving || loading}
                  style={{
                    padding: '4px 10px',
                    borderRadius: 'var(--radius-sm)',
                    background: settings?.whatsapp_enabled ? 'rgba(239, 68, 68, 0.15)' : 'rgba(37, 211, 102, 0.15)',
                    border: `1px solid ${settings?.whatsapp_enabled ? 'rgba(239, 68, 68, 0.3)' : 'rgba(37, 211, 102, 0.3)'}`,
                    color: settings?.whatsapp_enabled ? '#f87171' : '#4ade80',
                    fontSize: '0.75rem',
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  {settings?.whatsapp_enabled ? 'Disable' : 'Enable'}
                </button>
              </div>
            </div>

            <p style={{ fontSize: '0.84rem', color: 'var(--text-secondary)', lineHeight: 1.5, margin: '0 0 16px 0' }}>
              Optional mirror provider. Fail-closed architecture: if WhatsApp billing or authentication fails, Telegram continues
              uninterrupted and a channel degradation notice is sent to Telegram.
            </p>
          </div>

          <div
            style={{
              padding: '10px 14px',
              borderRadius: 'var(--radius-sm)',
              background: 'rgba(255, 255, 255, 0.03)',
              border: '1px solid var(--border-light)',
              fontSize: '0.78rem',
              color: 'var(--text-tertiary)',
            }}
          >
            🔒 Credentials set via <code>WHATSAPP_ACCESS_TOKEN</code> & <code>PHONE_NUMBER_ID</code> in <code>.env</code>
          </div>
        </div>
      </div>

      {/* Event Filters Section */}
      <div
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-medium)',
          borderRadius: 'var(--radius-lg)',
          padding: '24px',
          marginBottom: '32px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
          <Sliders size={18} style={{ color: 'var(--accent-primary)' }} />
          <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 600, color: 'var(--text-primary)' }}>
            Event Notification Filters
          </h2>
        </div>
        <p style={{ margin: '0 0 20px 0', fontSize: '0.84rem', color: 'var(--text-secondary)' }}>
          Select which events dispatch instant alerts. Events toggled off are muted and recorded as skipped in the delivery log.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '12px' }}>
          {Object.entries(EVENT_METADATA).map(([key, meta]) => {
            const isEnabled = settings?.enabled_events?.[key] ?? meta.defaultOn;
            return (
              <div
                key={key}
                onClick={() => handleToggleEvent(key)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '14px 16px',
                  borderRadius: 'var(--radius-md)',
                  background: isEnabled ? 'rgba(255, 255, 255, 0.04)' : 'rgba(255, 255, 255, 0.01)',
                  border: `1px solid ${isEnabled ? 'var(--border-medium)' : 'var(--border-light)'}`,
                  cursor: 'pointer',
                  transition: 'all var(--transition-fast)',
                }}
              >
                <div style={{ paddingRight: '12px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                    <span
                      style={{
                        width: '8px',
                        height: '8px',
                        borderRadius: '50%',
                        background: meta.tagColor,
                        display: 'inline-block',
                      }}
                    />
                    <span style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {meta.label}
                    </span>
                  </div>
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', lineHeight: 1.3 }}>
                    {meta.description}
                  </div>
                </div>

                <div
                  style={{
                    width: '38px',
                    height: '20px',
                    borderRadius: '10px',
                    background: isEnabled ? 'var(--accent-primary)' : 'rgba(255, 255, 255, 0.1)',
                    position: 'relative',
                    flexShrink: 0,
                    transition: 'background var(--transition-fast)',
                  }}
                >
                  <div
                    style={{
                      width: '16px',
                      height: '16px',
                      borderRadius: '50%',
                      background: '#ffffff',
                      position: 'absolute',
                      top: '2px',
                      left: isEnabled ? '20px' : '2px',
                      transition: 'left var(--transition-fast)',
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Delivery Log Section */}
      <div
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border-medium)',
          borderRadius: 'var(--radius-lg)',
          padding: '24px',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '18px',
            flexWrap: 'wrap',
            gap: '12px',
          }}
        >
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
              <Clock size={18} style={{ color: 'var(--accent-primary)' }} />
              <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                Delivery Audit Log
              </h2>
            </div>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)' }}>
              Lightweight trace of dispatches, channel fallbacks, and delivery outcomes ({totalLogs} total records)
            </span>
          </div>

          <div style={{ display: 'flex', gap: '6px' }}>
            {(['all', 'telegram', 'whatsapp', 'failed'] as const).map((filter) => (
              <button
                key={filter}
                onClick={() => setLogFilter(filter)}
                style={{
                  padding: '5px 12px',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-medium)',
                  background: logFilter === filter ? 'rgba(255, 255, 255, 0.1)' : 'transparent',
                  color: logFilter === filter ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontSize: '0.78rem',
                  fontWeight: 500,
                  cursor: 'pointer',
                  textTransform: 'capitalize',
                }}
              >
                {filter}
              </button>
            ))}
          </div>
        </div>

        {filteredLogs.length === 0 ? (
          <div
            style={{
              padding: '40px 20px',
              textAlign: 'center',
              color: 'var(--text-tertiary)',
              fontSize: '0.88rem',
            }}
          >
            No notification logs match the current filter.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {filteredLogs.map((log) => {
              const isSuccess = log.status === 'delivered';
              const isSkipped = log.status === 'skipped';
              return (
                <div
                  key={log.id}
                  style={{
                    padding: '14px 16px',
                    borderRadius: 'var(--radius-md)',
                    background: 'rgba(255, 255, 255, 0.02)',
                    border: '1px solid var(--border-light)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '6px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span
                        style={{
                          fontSize: '0.72rem',
                          fontWeight: 700,
                          textTransform: 'uppercase',
                          padding: '2px 8px',
                          borderRadius: 'var(--radius-sm)',
                          background:
                            log.channel === 'telegram'
                              ? 'rgba(56, 189, 248, 0.12)'
                              : log.channel === 'whatsapp'
                              ? 'rgba(37, 211, 102, 0.12)'
                              : 'rgba(255, 255, 255, 0.06)',
                          color:
                            log.channel === 'telegram'
                              ? '#38bdf8'
                              : log.channel === 'whatsapp'
                              ? '#25d366'
                              : 'var(--text-secondary)',
                        }}
                      >
                        {log.channel}
                      </span>

                      <span style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                        {log.title}
                      </span>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span
                        style={{
                          fontSize: '0.72rem',
                          fontWeight: 600,
                          padding: '2px 8px',
                          borderRadius: 'var(--radius-full)',
                          background: isSuccess
                            ? 'rgba(16, 185, 129, 0.12)'
                            : isSkipped
                            ? 'rgba(255, 255, 255, 0.05)'
                            : 'rgba(239, 68, 68, 0.12)',
                          color: isSuccess ? '#34d399' : isSkipped ? 'var(--text-tertiary)' : '#f87171',
                          border: `1px solid ${
                            isSuccess
                              ? 'rgba(16, 185, 129, 0.25)'
                              : isSkipped
                              ? 'var(--border-light)'
                              : 'rgba(239, 68, 68, 0.25)'
                          }`,
                        }}
                      >
                        {log.status.toUpperCase()}
                      </span>

                      <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                        {formatTime(log.created_at)}
                      </span>
                    </div>
                  </div>

                  <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                    {log.body.slice(0, 160)}
                    {log.body.length > 160 ? '…' : ''}
                  </div>

                  {log.error_detail && (
                    <div
                      style={{
                        fontSize: '0.75rem',
                        color: '#fca5a5',
                        background: 'rgba(239, 68, 68, 0.08)',
                        padding: '6px 10px',
                        borderRadius: 'var(--radius-sm)',
                        fontFamily: 'monospace',
                      }}
                    >
                      Error: {log.error_detail}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
