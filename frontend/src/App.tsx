import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import AuthGuard from './components/AuthGuard'
import { ToastProvider } from './components/atelier/Toast'
import LoginPage from './pages/LoginPage'
import WorkbenchPage from './pages/WorkbenchPage'
import TemplateLibraryPage from './pages/TemplateLibraryPage'
import SecureReportPage from './pages/SecureReportPage'
// Legacy pages kept available during Phase 8 cleanup; new routes shadow them.
import ChatPage from './legacy/pages/ChatPage'
import TemplateCenter from './legacy/pages/TemplateCenter'
import HistoryPage from './legacy/pages/HistoryPage'
import ObservabilityPage from './pages/ObservabilityPage'

export default function App() {
  return (
    <ToastProvider>
      <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />

            <Route
              path="/report/:sessionId/:version"
              element={
                <AuthGuard>
                  <SecureReportPage />
                </AuthGuard>
              }
            />

            <Route
              path="/"
              element={
                <AuthGuard>
                  <WorkbenchPage />
                </AuthGuard>
              }
            />
            <Route
              path="/templates"
              element={
                <AuthGuard>
                  <TemplateLibraryPage />
                </AuthGuard>
              }
            />
            <Route
              path="/observability"
              element={
                <AuthGuard>
                  <ObservabilityPage />
                </AuthGuard>
              }
            />
            {/* Legacy routes — kept for Phase 8 evaluation. */}
            <Route
              path="/history"
              element={
                <AuthGuard>
                  <HistoryPage />
                </AuthGuard>
              }
            />
            <Route
              path="/legacy/chat"
              element={
                <AuthGuard>
                  <ChatPage />
                </AuthGuard>
              }
            />
            <Route
              path="/legacy/templates"
              element={
                <AuthGuard>
                  <TemplateCenter />
                </AuthGuard>
              }
            />

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
      </BrowserRouter>
    </ToastProvider>
  )
}
