import React, { useEffect, useState } from 'react';
import { Outlet, NavLink, useLocation } from 'react-router-dom';
import {
  Layers,
  ShieldAlert,
  Inbox,
  Bell,
  Sparkles,
  ExternalLink,
  Cpu,
} from 'lucide-react';
import { api } from '../../api/client';

export const AppLayout: React.FC = () => {
  const location = useLocation();
  const [scamCount, setScamCount] = useState<number>(0);

  // Poll or fetch scam review pending count periodically
  useEffect(() => {
    let isMounted = true;
    const fetchPendingCount = async () => {
      try {
        const items = await api.getPendingScamReviews(10, 0);
        if (isMounted) {
          setScamCount(items.length);
        }
      } catch {
        // Silently tolerate if backend is not yet started
      }
    };

    fetchPendingCount();
    const interval = setInterval(fetchPendingCount, 30000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [location.pathname]);

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* ── Top Header Navigation Bar ───────────────────────────────────── */}
      <header
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 40,
          background: 'rgba(10, 9, 16, 0.82)',
          backdropFilter: 'blur(16px)',
          WebkitBackdropFilter: 'blur(16px)',
          borderBottom: '1px solid var(--border-subtle)',
          padding: '0 var(--space-xl)',
          height: '68px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        {/* Brand / Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div
            style={{
              width: '36px',
              height: '36px',
              borderRadius: 'var(--radius-md)',
              background: 'linear-gradient(135deg, var(--purple-600), var(--orange-500))',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 0 16px rgba(139, 92, 246, 0.3)',
            }}
          >
            <Sparkles size={20} color="#ffffff" />
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span
                style={{
                  fontSize: '1.05rem',
                  fontWeight: 700,
                  letterSpacing: '-0.01em',
                  color: 'var(--text-primary)',
                }}
              >
                Career Intelligence Core
              </span>
              <span
                style={{
                  fontSize: '0.68rem',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  padding: '2px 7px',
                  borderRadius: 'var(--radius-full)',
                  background: 'rgba(99, 102, 241, 0.15)',
                  color: '#a5b4fc',
                  border: '1px solid rgba(99, 102, 241, 0.3)',
                  letterSpacing: '0.05em',
                }}
              >
                Phase 8 • MVP
              </span>
            </div>
            <div
              style={{
                fontSize: '0.75rem',
                color: 'var(--text-tertiary)',
                letterSpacing: '0.01em',
              }}
            >
              Approval, Review, Responses & Notifications
            </div>
          </div>
        </div>

        {/* Center Nav Links */}
        <nav style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <NavLink
            to="/"
            end
            style={({ isActive }) => ({
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 16px',
              borderRadius: 'var(--radius-md)',
              fontSize: '0.875rem',
              fontWeight: 500,
              textDecoration: 'none',
              transition: 'all var(--transition-fast)',
              color: isActive ? '#f5f5f7' : 'var(--text-secondary)',
              background: isActive ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
              border: isActive ? '1px solid var(--border-medium)' : '1px solid transparent',
              boxShadow: isActive ? '0 2px 10px rgba(0,0,0,0.3)' : 'none',
            })}
          >
            <Layers size={16} />
            <span>Opportunities</span>
          </NavLink>

          <NavLink
            to="/scam-review"
            style={({ isActive }) => ({
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 16px',
              borderRadius: 'var(--radius-md)',
              fontSize: '0.875rem',
              fontWeight: 500,
              textDecoration: 'none',
              transition: 'all var(--transition-fast)',
              color: isActive ? '#f5f5f7' : 'var(--text-secondary)',
              background: isActive ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
              border: isActive ? '1px solid var(--border-medium)' : '1px solid transparent',
              boxShadow: isActive ? '0 2px 10px rgba(0,0,0,0.3)' : 'none',
            })}
          >
            <ShieldAlert size={16} />
            <span>Scam Review</span>
            {scamCount > 0 && (
              <span
                style={{
                  background: 'var(--amber-500)',
                  color: '#07070b',
                  fontSize: '0.7rem',
                  fontWeight: 700,
                  padding: '1px 6px',
                  borderRadius: 'var(--radius-full)',
                  marginLeft: '2px',
                }}
              >
                {scamCount}
              </span>
            )}
          </NavLink>

          <NavLink
            to="/responses"
            style={({ isActive }) => ({
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 16px',
              borderRadius: 'var(--radius-md)',
              fontSize: '0.875rem',
              fontWeight: 500,
              textDecoration: 'none',
              transition: 'all var(--transition-fast)',
              color: isActive ? '#f5f5f7' : 'var(--text-secondary)',
              background: isActive ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
              border: isActive ? '1px solid var(--border-medium)' : '1px solid transparent',
              boxShadow: isActive ? '0 2px 10px rgba(0,0,0,0.3)' : 'none',
            })}
          >
            <Inbox size={16} />
            <span>Response Center</span>
          </NavLink>

          <NavLink
            to="/notifications"
            style={({ isActive }) => ({
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 16px',
              borderRadius: 'var(--radius-md)',
              fontSize: '0.875rem',
              fontWeight: 500,
              textDecoration: 'none',
              transition: 'all var(--transition-fast)',
              color: isActive ? '#f5f5f7' : 'var(--text-secondary)',
              background: isActive ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
              border: isActive ? '1px solid var(--border-medium)' : '1px solid transparent',
              boxShadow: isActive ? '0 2px 10px rgba(0,0,0,0.3)' : 'none',
            })}
          >
            <Bell size={16} />
            <span>Notifications</span>
          </NavLink>
        </nav>

        {/* System Telemetry Indicator */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '7px',
              padding: '5px 12px',
              borderRadius: 'var(--radius-full)',
              background: 'rgba(16, 185, 129, 0.1)',
              border: '1px solid rgba(16, 185, 129, 0.25)',
              fontSize: '0.75rem',
              color: 'var(--emerald-400)',
              fontWeight: 500,
            }}
          >
            <span
              style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                background: 'var(--emerald-400)',
                boxShadow: '0 0 8px var(--emerald-400)',
              }}
            />
            Funnel Engine Online
          </div>

          <a
            href="http://127.0.0.1:8000/docs"
            target="_blank"
            rel="noreferrer"
            title="FastAPI Interactive Swagger Docs"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '0.75rem',
              color: 'var(--text-secondary)',
              textDecoration: 'none',
              background: 'rgba(255, 255, 255, 0.04)',
              border: '1px solid var(--border-subtle)',
              transition: 'background var(--transition-fast)',
            }}
          >
            <Cpu size={14} />
            <span>API Docs</span>
            <ExternalLink size={12} />
          </a>
        </div>
      </header>

      {/* ── Main Content Outlet ─────────────────────────────────────────── */}
      <main style={{ flex: 1, padding: 'var(--space-xl) var(--space-xl)' }}>
        <div style={{ maxWidth: '1440px', margin: '0 auto' }}>
          <Outlet />
        </div>
      </main>
    </div>
  );
};
