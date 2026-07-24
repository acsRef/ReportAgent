import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import antdTheme from './theme/antdTheme'
import AuthGuard from './components/AuthGuard'
import LoginPage from './pages/LoginPage'
import WorkbenchPage from './pages/WorkbenchPage'
import TemplateLibraryPage from './pages/TemplateLibraryPage'
import SecureReportPage from './pages/SecureReportPage'
// Legacy pages kept available during Phase 8 cleanup; new routes shadow them.
import ChatPage from './pages/ChatPage'
import TemplateCenter from './pages/TemplateCenter'
import HistoryPage from './pages/HistoryPage'

export default function App() {
  return (
    <ConfigProvider theme={antdTheme} locale={zhCN}>
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
          {/* Legacy routes — kept for Phase 8 evaluation. Phase 6 redirects
              /history to root. The old ChatPage + TemplateCenter remain
              wired behind these paths so manual QA can still run them. */}
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
    </ConfigProvider>
  )
}
