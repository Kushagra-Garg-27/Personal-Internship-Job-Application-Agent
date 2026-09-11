export type ReliabilityTier = 'stable' | 'experimental' | 'discovery_only';

export type OpportunityStatus =
  | 'discovered'
  | 'ineligible'
  | 'scam_risk_rejected'
  | 'scam_review_pending'
  | 'recommended'
  | 'rejected_by_user'
  | 'ready_to_apply'
  | 'awaiting_submission'
  | 'manual_application_required'
  | 'applied'
  | 'submitted'
  | 'interview'
  | 'offered'
  | 'accepted'
  | 'rejected_by_recruiter'
  | 'withdrawn'
  | 'expired'
  | 'dismissed';

export interface ResumeOption {
  id: number;
  version: number;
  original_filename: string;
  is_active: boolean;
  uploaded_at?: string | null;
}

// ── Candidate Profile (U4.1) ───────────────────────────────────────────
export interface ProfileEducation {
  id?: number;
  degree: string;
  branch: string;
  institution: string;
  graduation_year?: number | null;
}

export interface ProfileSkill {
  id?: number;
  skill_name: string;
  proficiency?: string | null;
}

export interface ProfileLink {
  id?: number;
  link_type: string;
  url: string;
}

export interface CandidateProfile {
  id: number;
  name: string;
  full_name?: string | null;
  email?: string | null;
  phone?: string | null;
  location?: string | null;
  location_preference?: string | null;
  remote_preference?: string | null;
  salary_floor?: number | null;
  role_types?: string[] | null;

  // Candidate application attributes (Unstop required)
  organization?: string | null;
  designation?: string | null;
  work_experience?: string | null;
  user_type?: string | null;
  gender?: string | null;
  differently_abled?: string | null;

  education: ProfileEducation[];
  skills: ProfileSkill[];
  links: ProfileLink[];

  created_at?: string;
  updated_at?: string;
}

export type CandidateProfileUpdate = Partial<
  Omit<CandidateProfile, 'id' | 'created_at' | 'updated_at' | 'education' | 'skills' | 'links'>
>;

export interface RelevanceExplanation {
  top_skills?: string[];
  matched_skills?: string[];
  match_summary?: string;
  matched_sentences?: Array<{ sentence: string; similarity: number }>;
  breakdown?: Record<string, any>;
}

export interface EligibilityReason {
  rule?: string;
  detail?: string;
  [key: string]: any;
}

export interface ScamReason {
  rule?: string;
  detail?: string;
  keyword?: string;
  matched?: string[];
  [key: string]: any;
}

export interface DashboardOpportunityItem {
  id: number;
  dedup_hash: string;
  status: OpportunityStatus;
  reliability_tier: ReliabilityTier;
  source?: string | null;
  title: string;
  company: string;
  description?: string | null;
  url?: string | null;
  location?: string | null;
  salary_min?: number | null;
  salary_max?: number | null;
  posted_at?: string | null;
  deadline_at?: string | null;
  discovered_at: string;
  profile_id?: number | null;
  selected_resume_id?: number | null;

  // Relevance & Scoring
  relevance_score?: number | null;
  relevance_explanation?: RelevanceExplanation | null;
  model_name?: string | null;

  // Eligibility
  eligibility_passed?: boolean | null;
  eligibility_reason?: EligibilityReason | null;

  // Scam & Risk
  scam_verdict?: 'clear' | 'reject' | 'ambiguous' | 'deferred' | null;
  scam_reason?: ScamReason | null;
  llm_verdict?: 'scam' | 'legitimate' | 'suspicious' | null;
  llm_reasoning?: string | null;
  quota_deferred_at?: string | null;
  funnel_completed_at?: string | null;

  // Resumes
  available_resumes: ResumeOption[];
}

export interface DashboardFeedResponse {
  items: DashboardOpportunityItem[];
  total: number;
  limit: number;
  offset: number;
}

// ── Phase 7: Recruiter Messages ─────────────────────────────────────────

export type MessageClassification =
  | 'interview_invite'
  | 'rejection'
  | 'offer'
  | 'follow_up'
  | 'screening_question'
  | 'generic'
  | 'unclassified';

export type LinkConfidence = 'high' | 'low' | 'none';

export interface RecruiterMessage {
  id: number;
  gmail_id: string;
  thread_id?: string | null;
  application_id?: number | null;
  sender: string;
  sender_domain?: string | null;
  subject?: string | null;
  body_preview?: string | null;
  received_at?: string | null;
  classification?: MessageClassification | null;
  classification_source?: 'rules' | 'llm' | null;
  classification_confidence?: number | null;
  link_confidence?: LinkConfidence | null;
  created_at: string;
  opportunity_id?: number | null;
  opportunity_title?: string | null;
  opportunity_company?: string | null;
  opportunity_status?: string | null;
  suggested_reply?: string | null;
  draft_id?: string | null;
  action_taken?: 'approved' | 'acknowledged' | null;
  action_taken_at?: string | null;
}

export interface ApproveReplyResponse {
  message: RecruiterMessage;
  opportunity_id: number;
  opportunity_status: string;
  draft_id: string;
  detail: string;
}

export interface AcknowledgeMessageResponse {
  message: RecruiterMessage;
  opportunity_id: number;
  opportunity_status: string;
  detail: string;
}

export interface MessageListResponse {
  items: RecruiterMessage[];
  total: number;
  limit: number;
  offset: number;
}

export interface MessageStats {
  total: number;
  by_classification: Record<string, number>;
  linked: number;
  unlinked: number;
}

export interface IntegrationHealthEvent {
  id: number;
  integration_name: string;
  event_type: string;
  detail?: string | null;
  occurred_at: string;
}

export interface IntegrationHealthListResponse {
  items: IntegrationHealthEvent[];
  total: number;
}

// ── Notifications (Phase 8) ──────────────────────────────────────────
export interface NotificationSettings {
  whatsapp_enabled: boolean;
  enabled_events: Record<string, boolean>;
  telegram_configured: boolean;
  whatsapp_configured: boolean;
  updated_at?: string | null;
}

export interface NotificationLogItem {
  id: number;
  event_type: string;
  channel: 'telegram' | 'whatsapp' | string;
  title: string;
  body: string;
  status: 'delivered' | 'failed' | 'skipped';
  error_detail?: string | null;
  retry_count: number;
  payload_json?: Record<string, any> | null;
  created_at: string;
}

export interface NotificationLogListResponse {
  items: NotificationLogItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface TestNotificationResponse {
  success: boolean;
  event_type: string;
  skipped: boolean;
  skip_reason?: string | null;
  results: Array<{
    channel: string;
    success: boolean;
    error?: string | null;
    retries: number;
  }>;
  channel_degraded_alert_sent: boolean;
}

// ── U4 Human Submission Review & Confirmation Gate ─────────────────────────

export const HUMAN_SUBMISSION_APPROVAL_TOKEN = 'HUMAN_CONFIRMED_SUBMIT';

export type ApplicationStatus = 'pending' | 'form_filled' | 'submitted' | 'failed';

export interface ApplicationCustomAnswer {
  question_id?: string | null;
  question_text?: string;
  label?: string;
  answer?: string;
  is_ai_draft?: boolean;
}

export interface ApplicationNotesData {
  adapter?: string;
  tier?: string;
  custom_answers?: ApplicationCustomAnswer[];
  draft_payload?: Record<string, any> | null;
  filled_at?: string;
  [key: string]: any;
}

export interface ApplicationResponse {
  id: number;
  opportunity_id: number;
  resume_id?: number | null;
  attempt_number: number;
  status: ApplicationStatus;
  submitted_at?: string | null;
  confirmation_ref?: string | null;
  adapter_name?: string | null;
  notes?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConfirmSubmitRequest {
  approval_token: string;
  approved_by?: string;
  platform_confirmed?: boolean;
  confirmation_ref?: string | null;
  confirmation_detail?: string | null;
}

export interface ConfirmSubmitResponse {
  success: boolean;
  status?: string;
  confirmed?: boolean;
  confirmation_ref?: string | null;
  confirmation_detail?: string | null;
  submitted_at?: string | null;
  mode?: string;
  reason?: string;
  error?: string;
}

