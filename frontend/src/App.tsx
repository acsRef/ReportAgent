import { Routes, Route, Navigate } from 'react-router-dom';
import Header from './components/layout/Header';
import ChatPage from './pages/ChatPage';
import DashboardPage from './pages/DashboardPage';

export default function App() {
  return (
    <>
      <Header />
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}