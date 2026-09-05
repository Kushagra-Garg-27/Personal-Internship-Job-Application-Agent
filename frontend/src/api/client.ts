import type {
  DashboardFeedResponse,
  DashboardOpportunityItem,
  MessageListResponse,
  RecruiterMessage,
  ApproveReplyResponse,
  AcknowledgeMessageResponse,
  MessageStats,
  IntegrationHealthListResponse,
  NotificationSettings,
  NotificationLogListResponse,
  TestNotificationResponse,
} from '../types';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE}${path}`;
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };

  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    let errorDetail = 'API request failed';
    try {
      const errJson = await response.json();
      errorDetail = errJson.detail || JSON.stringify(errJson);
    } catch {
      errorDetail = response.statusText || `${response.status}`;
    }
    throw new Error(errorDetail);
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

export const api = {
  // ── Opportunity Feed (Phase 6) ──────────────────────────────────────────
  async getDashboardFeed(params: {
    status?: string;
    tier?: string;
    limit?: number;
    offset?: number;
  } = {}): Promise<DashboardFeedResponse> {
    const q = new URLSearchParams();
    if (params.status) q.set('status', params.status);
    if (params.tier) q.set('tier', params.tier);
    if (params.limit !== undefined) q.set('limit', String(params.limit));
    if (params.offset !== undefined) q.set('offset', String(params.offset));

    const queryStr = q.toString() ? `?${q.toString()}` : '';
    return request<DashboardFeedResponse>(`/opportunities/dashboard-feed${queryStr}`);
  },

  // Final Human Approval (Phase 6 §2 Gate)
  async finalApprove(
    id: number,
    resumeId?: number | null,
    actor: string = 'human_user'
  ): Promise<any> {
    return request(`/opportunities/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ resume_id: resumeId, actor }),
    });
  },

  // Dismiss Opportunity (Phase 6)
  async dismiss(
    id: number,
    reason: string = 'Declined by candidate during review',
    actor: string = 'human_user'
  ): Promise<any> {
    return request(`/opportunities/${id}/dismiss`, {
      method: 'POST',
      body: JSON.stringify({ reason, actor }),
    });
  },

  // ── Scam Review Queue (Phase 5) ─────────────────────────────────────────
  async getPendingScamReviews(limit: number = 50, offset: number = 0): Promise<DashboardOpportunityItem[]> {
    // Note: GET /opportunities/dashboard-feed?status=scam_review_pending provides the full rich model
    const feed = await this.getDashboardFeed({
      status: 'scam_review_pending',
      limit,
      offset,
    });
    return feed.items;
  },

  // Clear listing through scam gate -> proceeds to Stage 3 Relevance
  async clearScamReview(id: number, actor: string = 'human_reviewer'): Promise<any> {
    return request(`/opportunities/${id}/scam-review/approve`, {
      method: 'POST',
      body: JSON.stringify({ actor }),
    });
  },

  // Confirm listing as scam -> transitions to scam_risk_rejected & records content hash
  async rejectScamReview(
    id: number,
    reason: string = 'Confirmed scam during human review',
    actor: string = 'human_reviewer'
  ): Promise<any> {
    return request(`/opportunities/${id}/scam-review/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason, actor }),
    });
  },

  // Get Opportunity details
  async getOpportunity(id: number): Promise<any> {
    return request(`/opportunities/${id}`);
  },

  // ── Recruiter Messages (Phase 7) ─────────────────────────────────────────
  async getMessages(params: {
    classification?: string;
    application_id?: number;
    linked?: boolean;
    limit?: number;
    offset?: number;
  } = {}): Promise<MessageListResponse> {
    const q = new URLSearchParams();
    if (params.classification) q.set('classification', params.classification);
    if (params.application_id !== undefined) q.set('application_id', String(params.application_id));
    if (params.linked !== undefined) q.set('linked', String(params.linked));
    if (params.limit !== undefined) q.set('limit', String(params.limit));
    if (params.offset !== undefined) q.set('offset', String(params.offset));

    const queryStr = q.toString() ? `?${q.toString()}` : '';
    return request<MessageListResponse>(`/messages${queryStr}`);
  },

  async getMessageStats(): Promise<MessageStats> {
    return request<MessageStats>('/messages/stats');
  },

  async getIntegrationHealth(
    integrationName?: string,
    limit: number = 20,
  ): Promise<IntegrationHealthListResponse> {
    const q = new URLSearchParams();
    if (integrationName) q.set('integration_name', integrationName);
    q.set('limit', String(limit));
    return request<IntegrationHealthListResponse>(`/integration-health?${q.toString()}`);
  },

  // ── Recruiter Response Loop (Phase 10) ──────────────────────────────────
  async draftReply(messageId: number): Promise<RecruiterMessage> {
    return request<RecruiterMessage>(`/messages/${messageId}/draft-reply`, {
      method: 'POST',
    });
  },

  async approveReply(
    messageId: number,
    editedReply?: string,
  ): Promise<ApproveReplyResponse> {
    return request<ApproveReplyResponse>(`/messages/${messageId}/approve-reply`, {
      method: 'POST',
      body: JSON.stringify({ edited_reply: editedReply }),
    });
  },

  async acknowledgeMessage(
    messageId: number,
  ): Promise<AcknowledgeMessageResponse> {
    return request<AcknowledgeMessageResponse>(`/messages/${messageId}/acknowledge`, {
      method: 'POST',
    });
  },


  // ── Notifications (Phase 8) ─────────────────────────────────────────────
  async getNotificationSettings(): Promise<NotificationSettings> {
    return request<NotificationSettings>('/notifications/settings');
  },

  async updateNotificationSettings(params: {
    whatsapp_enabled?: boolean;
    enabled_events?: Record<string, boolean>;
  }): Promise<NotificationSettings> {
    return request<NotificationSettings>('/notifications/settings', {
      method: 'PATCH',
      body: JSON.stringify(params),
    });
  },

  async getNotificationLogs(params: {
    channel?: string;
    status?: string;
    event_type?: string;
    limit?: number;
    offset?: number;
  } = {}): Promise<NotificationLogListResponse> {
    const q = new URLSearchParams();
    if (params.channel) q.set('channel', params.channel);
    if (params.status) q.set('status', params.status);
    if (params.event_type) q.set('event_type', params.event_type);
    if (params.limit !== undefined) q.set('limit', String(params.limit));
    if (params.offset !== undefined) q.set('offset', String(params.offset));

    const queryStr = q.toString() ? `?${q.toString()}` : '';
    return request<NotificationLogListResponse>(`/notifications/logs${queryStr}`);
  },

  async sendTestNotification(eventType: string = 'test_event'): Promise<TestNotificationResponse> {
    return request<TestNotificationResponse>('/notifications/test', {
      method: 'POST',
      body: JSON.stringify({ event_type: eventType }),
    });
  },
};
