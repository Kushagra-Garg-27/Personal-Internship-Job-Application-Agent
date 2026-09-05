import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppLayout } from './components/layout/AppLayout';
import { FeedPage } from './pages/FeedPage';
import { ScamQueuePage } from './pages/ScamQueuePage';
import { ResponseCenterPlaceholder } from './pages/ResponseCenterPlaceholder';
import { NotificationSettingsPage } from './pages/NotificationSettingsPage';

export const App: React.FC = () => {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppLayout />}>
          <Route index element={<FeedPage />} />
          <Route path="scam-review" element={<ScamQueuePage />} />
          <Route path="responses" element={<ResponseCenterPlaceholder />} />
          <Route path="notifications" element={<NotificationSettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
};

export default App;
