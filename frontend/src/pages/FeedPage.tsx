import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Search,
  RefreshCw,
  Sparkles,
  Layers,
  Loader2,
} from 'lucide-react';
import type { DashboardOpportunityItem } from '../types';
import { api } from '../api/client';
import { OpportunityCard } from '../components/feed/OpportunityCard';
import { OpportunityDetailDrawer } from '../components/feed/OpportunityDetailDrawer';
import { ApprovalModal } from '../components/feed/ApprovalModal';

export const FeedPage: React.FC = () => {
  const [opportunities, setOpportunities] = useState<DashboardOpportunityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('recommended');
  const [tierFilter, setTierFilter] = useState<string>('all');
  const [minScore, setMinScore] = useState<number>(0);

  // Modals & Drawer State
  const [selectedForDrawer, setSelectedForDrawer] = useState<DashboardOpportunityItem | null>(null);
  const [selectedForApproval, setSelectedForApproval] = useState<DashboardOpportunityItem | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const fetchFeed = useCallback(async () => {
    setLoading(true);
    try {
      const feed = await api.getDashboardFeed({
        status: statusFilter === 'all' ? undefined : statusFilter,
        tier: tierFilter === 'all' ? undefined : tierFilter,
        limit: 100,
      });
      setOpportunities(feed.items || []);
    } catch (err: any) {
      setToastMessage(`Error fetching feed: ${err?.message || 'Server error'}`);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, tierFilter]);

  useEffect(() => {
    fetchFeed();
  }, [fetchFeed]);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => {
      setToastMessage((cur) => (cur === msg ? null : cur));
    }, 4500);
  };

  // Filtered & Searched items
  const filteredItems = useMemo(() => {
    return opportunities.filter((opp) => {
      if (minScore > 0 && (opp.relevance_score || 0) < minScore / 100) {
        return false;
      }
      if (!searchQuery.trim()) return true;

      const q = searchQuery.toLowerCase();
      const inTitle = opp.title.toLowerCase().includes(q);
      const inCompany = opp.company.toLowerCase().includes(q);
      const inSkills = opp.relevance_explanation?.matched_skills?.some((s) =>
        s.toLowerCase().includes(q)
      );
      return inTitle || inCompany || Boolean(inSkills);
    });
  }, [opportunities, searchQuery, minScore]);

  // Handle final approval completion
  const handleApprovalSuccess = (updatedData: any) => {
    showToast(`Approved "${selectedForApproval?.title}"! Status updated to Ready to Apply.`);
    // Update local state
    setOpportunities((prev) =>
      prev.map((item) =>
        item.id === selectedForApproval?.id
          ? {
              ...item,
              status: 'ready_to_apply',
              selected_resume_id: updatedData?.selected_resume_id || item.selected_resume_id,
            }
          : item
      )
    );
    // Also update drawer if viewing this item
    if (selectedForDrawer && selectedForDrawer.id === selectedForApproval?.id) {
      setSelectedForDrawer((prev) => (prev ? { ...prev, status: 'ready_to_apply' } : null));
    }
  };

  // Handle dismiss action
  const handleDismiss = async (opp: DashboardOpportunityItem) => {
    const confirmDismiss = window.confirm(`Dismiss "${opp.title}" from your queue?`);
    if (!confirmDismiss) return;

    try {
      await api.dismiss(opp.id, 'Candidate dismissed opportunity from dashboard');
      showToast(`Dismissed "${opp.title}".`);
      setOpportunities((prev) =>
        prev.map((item) => (item.id === opp.id ? { ...item, status: 'dismissed' } : item))
      );
      if (selectedForDrawer?.id === opp.id) {
        setSelectedForDrawer((prev) => (prev ? { ...prev, status: 'dismissed' } : null));
      }
    } catch (err: any) {
      showToast(`Dismiss failed: ${err?.message || 'Unknown error'}`);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '28px' }}>
      {/* ── Toast Notification Banner ─────────────────────────────────── */}
      {toastMessage && (
        <div
          className="animate-fade-in"
          style={{
            position: 'fixed',
            bottom: '24px',
            right: '24px',
            zIndex: 70,
            background: 'var(--bg-card)',
            border: '1px solid var(--purple-500)',
            boxShadow: 'var(--shadow-purple)',
            color: '#f5f5f7',
            padding: '12px 20px',
            borderRadius: 'var(--radius-lg)',
            fontSize: '0.875rem',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
          }}
        >
          <Sparkles size={16} color="var(--orange-500)" />
          <span>{toastMessage}</span>
        </div>
      )}

      {/* ── Page Hero Header ─────────────────────────────────────────── */}
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
              color: 'var(--purple-500)',
              marginBottom: '6px',
            }}
          >
            <Sparkles size={14} />
            Phase 6 Human Intelligence Gate
          </div>
          <h1
            className="editorial-title"
            style={{ fontSize: '2.3rem', color: 'var(--text-primary)', margin: 0 }}
          >
            Opportunity Intelligence Feed
          </h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.92rem', marginTop: '6px', maxWidth: '780px' }}>
            Curated, scored, and verified job opportunities. Review semantic skill alignment, verify
            reliability tiers, and approve listings to queue Phase 9 worker submissions.
          </p>
        </div>

        <button
          onClick={fetchFeed}
          disabled={loading}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '9px 18px',
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
          <span>Refresh Feed</span>
        </button>
      </div>

      {/* ── Filters & Search Control Bar ──────────────────────────────── */}
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
          gap: '16px',
        }}
      >
        {/* Search Input */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            background: 'rgba(255, 255, 255, 0.04)',
            border: '1px solid var(--border-subtle)',
            padding: '8px 14px',
            borderRadius: 'var(--radius-md)',
            minWidth: '280px',
            flex: '1 1 300px',
          }}
        >
          <Search size={16} color="var(--text-tertiary)" />
          <input
            type="text"
            placeholder="Search by title, company, or skill..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              background: 'transparent',
              fontSize: '0.85rem',
              color: 'var(--text-primary)',
              width: '100%',
            }}
          />
        </div>

        {/* Filter Controls Strip */}
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '14px' }}>
          {/* Status Tabs */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              background: 'rgba(255, 255, 255, 0.04)',
              borderRadius: 'var(--radius-md)',
              padding: '3px',
              border: '1px solid var(--border-subtle)',
            }}
          >
            {[
              { key: 'recommended', label: 'Recommended' },
              { key: 'ready_to_apply', label: 'Ready to Apply' },
              { key: 'dismissed', label: 'Dismissed' },
              { key: 'all', label: 'All Statuses' },
            ].map((tab) => (
              <button
                key={tab.key}
                onClick={() => setStatusFilter(tab.key)}
                style={{
                  padding: '6px 14px',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  transition: 'all var(--transition-fast)',
                  background: statusFilter === tab.key ? 'var(--purple-600)' : 'transparent',
                  color: statusFilter === tab.key ? '#ffffff' : 'var(--text-secondary)',
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Reliability Tier Select */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)' }}>Source Tier:</span>
            <select
              value={tierFilter}
              onChange={(e) => setTierFilter(e.target.value)}
              style={{
                background: 'rgba(255, 255, 255, 0.05)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '6px 12px',
                fontSize: '0.8rem',
                color: 'var(--text-primary)',
                cursor: 'pointer',
              }}
            >
              <option value="all" style={{ background: '#13121f' }}>All Sources</option>
              <option value="stable" style={{ background: '#13121f' }}>Stable Sources Only</option>
              <option value="discovery_only" style={{ background: '#13121f' }}>Discovery Only</option>
            </select>
          </div>

          {/* Match Score Threshold */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)' }}>Min Score:</span>
            <select
              value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))}
              style={{
                background: 'rgba(255, 255, 255, 0.05)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '6px 12px',
                fontSize: '0.8rem',
                color: 'var(--text-primary)',
                cursor: 'pointer',
              }}
            >
              <option value={0} style={{ background: '#13121f' }}>Any Score</option>
              <option value={60} style={{ background: '#13121f' }}>60%+ Match</option>
              <option value={75} style={{ background: '#13121f' }}>75%+ Match</option>
              <option value={85} style={{ background: '#13121f' }}>85%+ Match</option>
            </select>
          </div>
        </div>
      </div>

      {/* ── Feed List / Skeletons / Empty States ───────────────────────── */}
      {loading ? (
        <div
          style={{
            padding: '70px',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '14px',
            color: 'var(--text-tertiary)',
          }}
        >
          <Loader2 size={36} className="animate-spin" color="var(--purple-500)" />
          <span>Curating intelligence feed...</span>
        </div>
      ) : filteredItems.length === 0 ? (
        <div
          style={{
            padding: '80px 24px',
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
          <Layers size={36} color="var(--text-tertiary)" />
          <h2 style={{ fontSize: '1.25rem', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
            No Opportunities Found
          </h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.88rem', maxWidth: '440px', margin: 0 }}>
            {searchQuery
              ? `No results matching "${searchQuery}". Try clearing search or lowering match threshold.`
              : `No opportunities currently match the selected status filter (${statusFilter}).`}
          </p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {filteredItems.map((opp) => (
            <OpportunityCard
              key={opp.id}
              opportunity={opp}
              onInspect={(item) => setSelectedForDrawer(item)}
              onApprove={(item) => setSelectedForApproval(item)}
              onDismiss={(item) => handleDismiss(item)}
            />
          ))}
        </div>
      )}

      {/* ── Slide-over Detail Drawer ──────────────────────────────────── */}
      <OpportunityDetailDrawer
        opportunity={selectedForDrawer}
        isOpen={Boolean(selectedForDrawer)}
        onClose={() => setSelectedForDrawer(null)}
        onApproveClick={(item) => {
          setSelectedForDrawer(null);
          setSelectedForApproval(item);
        }}
        onDismissClick={(item) => {
          handleDismiss(item);
        }}
      />

      {/* ── Final Approval Modal (Resume selector) ────────────────────── */}
      <ApprovalModal
        opportunity={selectedForApproval}
        isOpen={Boolean(selectedForApproval)}
        onClose={() => setSelectedForApproval(null)}
        onApproved={handleApprovalSuccess}
      />
    </div>
  );
};
