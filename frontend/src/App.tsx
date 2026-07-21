import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import antdTheme from './theme/antdTheme'
import AppShell from './components/layout/AppShell'
import ChatPage from './pages/ChatPage'
import TemplateCenter from './pages/TemplateCenter'
import HistoryPage from './pages/HistoryPage'

export default function App() {
  return (
    <ConfigProvider theme={antdTheme} locale={zhCN}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<ChatPage />} />
            <Route path="/templates" element={<TemplateCenter />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  )
}
