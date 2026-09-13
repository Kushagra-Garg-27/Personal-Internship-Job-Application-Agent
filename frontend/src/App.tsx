import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppLayout } from './components/layout/AppLayout';
import { FeedPage } from './pages/FeedPage';
import { ScamQueuePage } from './pages/ScamQueuePage';
import { ResponseCenterPage } from './pages/ResponseCenterPage';
import { NotificationSettingsPage } from './pages/NotificationSettingsPage';
import { ProfilePage } from './pages/ProfilePage';
import { SubmissionQueuePage } from './pages/SubmissionQueuePage';

export const App: React.FC = () => {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppLayout />}>
          <Route index element={<FeedPage />} />
          <Route path="scam-review" element={<ScamQueuePage />} />
          <Route path="responses" element={<ResponseCenterPage />} />
          <Route path="submission-queue" element={<SubmissionQueuePage />} />
          <Route path="notifications" element={<NotificationSettingsPage />} />
          <Route path="profile" element={<ProfilePage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
};

export default App;
