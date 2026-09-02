import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Sidebar } from './components/layout/Sidebar'
import { Header } from './components/layout/Header'

// 路由级代码分割：首屏只加载工作台，其余页面按需拉取
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const ConfigPage = lazy(() => import('./pages/ConfigPage'))
const ResumePage = lazy(() => import('./pages/ResumePage'))
const StatsPage = lazy(() => import('./pages/StatsPage'))
const ConfirmQueuePage = lazy(() => import('./pages/ConfirmQueuePage'))

function JobsPage() {
  return <DashboardPage view="jobs" />
}

function MonitorPage() {
  return <DashboardPage view="monitor" />
}

function PageFallback() {
  return (
    <div className="flex h-full items-center justify-center gap-3 text-sm text-muted" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      页面加载中…
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      {/* 浅灰画布上的悬浮 App Shell：大圆角 + 柔和投影 */}
      <div className="h-dvh bg-canvas p-0 text-foreground sm:p-4 lg:p-6">
        <div className="app-shell mx-auto flex h-full max-w-[1560px] overflow-hidden rounded-none bg-shell shadow-shell sm:rounded-shell">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
            <Header />
            <main className="min-w-0 flex-1 overflow-y-auto px-5 py-6 md:px-7 xl:px-9">
              <Suspense fallback={<PageFallback />}>
                <Routes>
                  <Route path="/" element={<DashboardPage />} />
                  <Route path="/jobs" element={<JobsPage />} />
                  <Route path="/resume" element={<ResumePage />} />
                  <Route path="/stats" element={<StatsPage />} />
                  <Route path="/confirm" element={<ConfirmQueuePage />} />
                  <Route path="/monitor" element={<MonitorPage />} />
                  <Route path="/config" element={<ConfigPage />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Suspense>
            </main>
          </div>
        </div>
      </div>
    </BrowserRouter>
  )
}
