import React, { useEffect, useState, useCallback } from 'react';
import {
  User,
  Save,
  Loader2,
  AlertCircle,
  CheckCircle2,
  Upload,
  FileText,
} from 'lucide-react';
import { api } from '../api/client';
import type { CandidateProfile, CandidateProfileUpdate, ResumeOption } from '../types';

const FIELD_STYLE: React.CSSProperties = {
  width: '100%',
  padding: '9px 12px',
  borderRadius: 'var(--radius-md)',
  background: 'var(--bg-card)',
  border: '1px solid var(--border-medium)',
  color: 'var(--text-primary)',
  fontSize: '0.875rem',
  outline: 'none',
};

const LABEL_STYLE: React.CSSProperties = {
  display: 'block',
  fontSize: '0.78rem',
  fontWeight: 600,
  color: 'var(--text-secondary)',
  marginBottom: '6px',
  letterSpacing: '0.01em',
};

interface TextFieldProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  required?: boolean;
}

const TextField: React.FC<TextFieldProps> = ({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
  required,
}) => (
  <div>
    <label style={LABEL_STYLE}>
      {label}
      {required && <span style={{ color: 'var(--rose-400)' }}> *</span>}
    </label>
    <input
      type={type}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      style={FIELD_STYLE}
    />
  </div>
);

export const ProfilePage: React.FC = () => {
  const [profile, setProfile] = useState<CandidateProfile | null>(null);
  const [resumes, setResumes] = useState<ResumeOption[]>([]);
  const [draft, setDraft] = useState<CandidateProfileUpdate>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const profiles = await api.listProfiles();
      if (profiles.length === 0) {
        setProfile(null);
        setLoading(false);
        return;
      }
      const full = await api.getProfile(profiles[0].id);
      setProfile(full);
      try {
        setResumes(await api.listProfileResumes(full.id));
      } catch {
        setResumes([]);
      }
      setDraft({});
    } catch (err: any) {
      setError(err?.message || 'Failed to load profile');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const setField = (key: keyof CandidateProfileUpdate, value: string) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  };

  const current = (key: keyof CandidateProfile): string => {
    const v = draft[key as keyof CandidateProfileUpdate];
    if (v !== undefined) return v == null ? '' : String(v);
    const base = profile ? (profile[key] as unknown) : '';
    return base == null ? '' : String(base);
  };

  const handleSave = async () => {
    if (!profile) return;
    setSaving(true);
    setError(null);
    try {
      const payload: CandidateProfileUpdate = {};
      for (const [k, v] of Object.entries(draft)) {
        if (v !== undefined && v !== '') {
          (payload as any)[k] = v;
        }
      }
      const updated = await api.updateProfile(profile.id, payload);
      setProfile(updated);
      setDraft({});
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err: any) {
      setError(err?.message || 'Failed to save profile');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--text-secondary)' }}>
        <Loader2 size={18} className="animate-spin" />
        <span>Loading candidate profile…</span>
      </div>
    );
  }

  if (!profile) {
    return (
      <div
        style={{
          padding: '20px',
          borderRadius: 'var(--radius-lg)',
          background: 'rgba(239, 68, 68, 0.08)',
          border: '1px solid rgba(239, 68, 68, 0.25)',
          color: 'var(--rose-400)',
          display: 'flex',
          gap: '10px',
          alignItems: 'center',
        }}
      >
        <AlertCircle size={18} />
        <span>No candidate profile exists. Create one first.</span>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', maxWidth: '920px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <div
          style={{
            width: '40px',
            height: '40px',
            borderRadius: 'var(--radius-md)',
            background: 'linear-gradient(135deg, var(--purple-600), var(--orange-500))',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <User size={22} color="#fff" />
        </div>
        <div>
          <h1 className="editorial-title" style={{ fontSize: '1.6rem', margin: 0, color: 'var(--text-primary)' }}>
            Candidate Profile
          </h1>
          <div style={{ fontSize: '0.82rem', color: 'var(--text-tertiary)' }}>
            Explicit candidate data used for Unstop applications. Missing values are never guessed.
          </div>
        </div>
      </div>

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

      {saved && (
        <div
          style={{
            padding: '10px 14px',
            borderRadius: 'var(--radius-md)',
            background: 'rgba(16, 185, 129, 0.12)',
            border: '1px solid rgba(16, 185, 129, 0.3)',
            color: 'var(--emerald-400)',
            fontSize: '0.82rem',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <CheckCircle2 size={16} />
          <span>Profile saved.</span>
        </div>
      )}

      {/* Contact */}
      <section
        style={{
          padding: '20px',
          borderRadius: 'var(--radius-lg)',
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-subtle)',
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '16px',
        }}
      >
        <TextField label="Full Name" required value={current('full_name')} onChange={(v) => setField('full_name', v)} />
        <TextField label="Email" type="email" value={current('email')} onChange={(v) => setField('email', v)} />
        <TextField label="Phone" value={current('phone')} onChange={(v) => setField('phone', v)} />
        <TextField label="Location" required value={current('location')} onChange={(v) => setField('location', v)} />
      </section>

      {/* Application attributes (Unstop-required demographic/professional) */}
      <section
        style={{
          padding: '20px',
          borderRadius: 'var(--radius-lg)',
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-subtle)',
          display: 'flex',
          flexDirection: 'column',
          gap: '16px',
        }}
      >
        <div style={{ fontSize: '0.95rem', fontWeight: 600, color: 'var(--text-primary)' }}>
          Application Attributes
        </div>
        <div
          style={{
            padding: '10px 14px',
            borderRadius: 'var(--radius-md)',
            background: 'rgba(249, 115, 22, 0.08)',
            border: '1px solid rgba(249, 115, 22, 0.2)',
            fontSize: '0.78rem',
            color: '#fdba74',
            lineHeight: 1.45,
          }}
        >
          These values are required by the Unstop form. Enter them exactly as you want them
          submitted. They are never inferred and are treated as missing until you provide them.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          <TextField
            label="Organization / Institution"
            value={current('organization')}
            onChange={(v) => setField('organization', v)}
          />
          <TextField
            label="Designation"
            value={current('designation')}
            onChange={(v) => setField('designation', v)}
          />
          <TextField
            label="Work Experience"
            value={current('work_experience')}
            onChange={(v) => setField('work_experience', v)}
          />
          <TextField label="User Type" value={current('user_type')} onChange={(v) => setField('user_type', v)} />
          <TextField label="Gender" value={current('gender')} onChange={(v) => setField('gender', v)} />
          <TextField
            label="Differently Abled"
            value={current('differently_abled')}
            onChange={(v) => setField('differently_abled', v)}
          />
        </div>
      </section>

      {/* Resume */}
      <section
        style={{
          padding: '20px',
          borderRadius: 'var(--radius-lg)',
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-subtle)',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
        }}
      >
        <div style={{ fontSize: '0.95rem', fontWeight: 600, color: 'var(--text-primary)' }}>Resume</div>
        {resumes.length > 0 ? (
          resumes.map((r) => (
            <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.85rem' }}>
              <FileText size={16} color="var(--text-secondary)" />
              <span style={{ color: 'var(--text-primary)' }}>{r.original_filename}</span>
              <span style={{ color: 'var(--text-tertiary)' }}>v{r.version}</span>
              {r.is_active && (
                <span
                  style={{
                    fontSize: '0.68rem',
                    fontWeight: 600,
                    padding: '1px 8px',
                    borderRadius: 'var(--radius-full)',
                    background: 'rgba(16, 185, 129, 0.15)',
                    color: 'var(--emerald-400)',
                  }}
                >
                  Active
                </span>
              )}
            </div>
          ))
        ) : (
          <div style={{ fontSize: '0.82rem', color: 'var(--amber-500)' }}>
            REAL_RESUME_REQUIRED — upload your real resume before applying on Unstop.
          </div>
        )}
        <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Upload size={14} />
          Upload via <code>POST /profiles/{profile.id}/resumes/</code>
        </div>
      </section>

      <div>
        <button
          onClick={handleSave}
          disabled={saving || Object.keys(draft).length === 0}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '9px 22px',
            borderRadius: 'var(--radius-md)',
            fontSize: '0.875rem',
            fontWeight: 600,
            color: '#ffffff',
            background: 'linear-gradient(135deg, var(--purple-600), var(--orange-500))',
            border: '1px solid rgba(139, 92, 246, 0.5)',
            cursor: saving || Object.keys(draft).length === 0 ? 'not-allowed' : 'pointer',
            opacity: saving || Object.keys(draft).length === 0 ? 0.6 : 1,
          }}
        >
          {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
          <span>{saving ? 'Saving…' : 'Save Profile'}</span>
        </button>
      </div>
    </div>
  );
};

export default ProfilePage;
