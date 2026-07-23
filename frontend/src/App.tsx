import { BrowserRouter, Routes, Route, Navigate, Outlet } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import antdTheme from './theme/antdTheme'
import AppShell from './components/layout/AppShell'
import ChatPage from './pages/ChatPage'
import TemplateCenter from './pages/TemplateCenter'
import HistoryPage from './pages/HistoryPage'
import LoginPage from './pages/LoginPage'
import StandaloneReportPage from './pages/StandaloneReportPage'
import { useAuthStore } from './stores/authStore'

function AuthGuard() {
  const { isAuthenticated } = useAuthStore()
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />
  }
  return <Outlet />
}

export default function App() {
  return (
    <ConfigProvider theme={antdTheme} locale={zhCN}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/report/:sessionId" element={<StandaloneReportPage />} />
          <Route element={<AuthGuard />}>
            <Route element={<AppShell />}>
              <Route path="/" element={<ChatPage />} />
              <Route path="/templates" element={<TemplateCenter />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  )
}
