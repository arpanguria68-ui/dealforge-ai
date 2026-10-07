import { lazy, Suspense, useState } from 'react';
import { ChatSidebar } from '@/components/ChatSidebar';
import {
  Zap, LayoutDashboard, Settings, Activity, Briefcase, MessageSquare, Database,
  CheckCircle2, XCircle, Loader2
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Toaster } from '@/components/ui/sonner';
import { API_BASE } from '@/lib/api-base';
import { useDealForgeStore } from '@/lib/dealforge-store';

type Page = 'chat' | 'dashboard' | 'tasks' | 'knowledge' | 'settings';

const Dashboard = lazy(() => import('./sections/Dashboard').then(module => ({ default: module.Dashboard })));
const SettingsPage = lazy(() => import('./sections/SettingsPage').then(module => ({ default: module.SettingsPage })));
const ChatWindow = lazy(() => import('./sections/ChatWindow').then(module => ({ default: module.ChatWindow })));
const TaskBoardPage = lazy(() => import('./sections/TaskBoardPage').then(module => ({ default: module.TaskBoardPage })));
const RAGDashboard = lazy(() => import('./sections/RAGDashboard').then(module => ({ default: module.RAGDashboard })));

function App() {
  const [page, setPage] = useState<Page>('chat');
  const [visitedPages, setVisitedPages] = useState<Set<Page>>(() => new Set(['chat']));
  const [sysStatus, setSysStatus] = useState<null | 'checking' | 'ok' | 'error'>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const createConversation = useDealForgeStore(state => state.createConversation);

  function navigateTo(nextPage: Page) {
    setVisitedPages(previous => new Set(previous).add(nextPage));
    setPage(nextPage);
  }

  async function checkSystemStatus() {
    setSysStatus('checking');
    try {
      const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
      setSysStatus(res.ok ? 'ok' : 'error');
    } catch {
      setSysStatus('error');
    }
    setTimeout(() => setSysStatus(null), 5000);
  }

  const NAV_ITEMS: { id: Page; label: string; icon: typeof Zap }[] = [
    { id: 'chat', label: 'Chat', icon: MessageSquare },
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'tasks', label: 'Tasks', icon: Briefcase },
    { id: 'knowledge', label: 'Knowledge', icon: Database },
    { id: 'settings', label: 'Settings', icon: Settings },
  ];

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden font-sans antialiased text-slate-900 dark:text-slate-50 bg-slate-50 dark:bg-slate-950">
      <header className="relative z-10 flex min-h-14 shrink-0 items-center gap-3 border-b bg-background px-4 sm:px-5">
        <div className="flex shrink-0 items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Zap className="h-4 w-4" />
          </div>
          <h1 className="hidden text-base font-semibold tracking-tight sm:block">DealForge</h1>
        </div>

        {/* Navigation */}
        <nav className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto sm:ml-2">
          {NAV_ITEMS.map(item => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                id={`nav-${item.id}`}
                onClick={() => navigateTo(item.id)}
                className={`flex shrink-0 items-center gap-2 rounded-md px-2 py-2 text-sm font-medium transition-colors md:px-3 ${page === item.id
                  ? 'bg-primary/10 text-primary shadow-sm'
                  : 'text-muted-foreground hover:text-foreground hover:bg-slate-100 dark:hover:bg-slate-800'
                  }`}
              >
                <Icon className="h-4 w-4" />
                <span className="hidden md:inline">{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="flex shrink-0 items-center gap-2">
          <div className="relative">
            <Button
              variant="outline"
              size="sm"
              onClick={checkSystemStatus}
              id="system-status-btn"
            >
              {sysStatus === 'checking' ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : sysStatus === 'ok' ? (
                <CheckCircle2 className="mr-2 h-4 w-4 text-green-500" />
              ) : sysStatus === 'error' ? (
                <XCircle className="mr-2 h-4 w-4 text-red-500" />
              ) : (
                <Activity className="mr-2 h-4 w-4" />
              )}
              <span className="hidden sm:inline">{sysStatus === 'ok' ? 'Online' : sysStatus === 'error' ? 'Offline' : 'Backend'}</span>
            </Button>
          </div>
          <Button size="sm" onClick={() => { createConversation(); navigateTo('chat'); }} id="new-deal-btn">
            <Briefcase className="h-4 w-4 sm:mr-2" />
            <span className="hidden sm:inline">New analysis</span>
          </Button>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {visitedPages.has('chat') && (
          <div className={page === 'chat' ? 'flex min-h-0 flex-1 overflow-hidden' : 'hidden'}>
            <ChatSidebar collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed(!sidebarCollapsed)} />
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
              <Suspense fallback={<div className="flex h-full items-center justify-center text-sm text-muted-foreground">Loading chat…</div>}>
                <ChatWindow />
              </Suspense>
            </div>
          </div>
        )}
        {visitedPages.has('dashboard') && (
          <div className={page === 'dashboard' ? 'min-h-0 flex-1 overflow-auto p-4 sm:p-6' : 'hidden'}>
            <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading dashboard…</div>}>
              <Dashboard onNavigate={navigateTo} />
            </Suspense>
          </div>
        )}
        {visitedPages.has('tasks') && (
          <div className={page === 'tasks' ? 'min-h-0 flex-1 overflow-auto p-4 sm:p-6' : 'hidden'}>
            <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading tasks…</div>}>
              <TaskBoardPage />
            </Suspense>
          </div>
        )}
        {visitedPages.has('knowledge') && (
          <div className={page === 'knowledge' ? 'min-h-0 flex-1 overflow-auto p-4 sm:p-6' : 'hidden'}>
            <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading knowledge…</div>}>
              <RAGDashboard />
            </Suspense>
          </div>
        )}
        {visitedPages.has('settings') && (
          <div className={page === 'settings' ? 'min-h-0 flex-1 overflow-auto p-4 sm:p-6' : 'hidden'}>
            <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading settings…</div>}>
              <SettingsPage />
            </Suspense>
          </div>
        )}
      </main>
      <Toaster richColors position="bottom-right" />
    </div>
  );
}

export default App;
