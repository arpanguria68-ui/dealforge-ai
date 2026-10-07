/**
 * ChatSidebar — Collapsible conversation history sidebar
 *
 * Inspired by Perplexity Clone's Sidebar.tsx, adapted for DealForge:
 * - Shows conversation list with deal context
 * - New Analysis button
 * - Delete conversations
 * - Collapsible on desktop, overlay on mobile
 */

import {
    Plus, MessageSquare, Trash2, ChevronLeft, ChevronRight,
    Briefcase, Clock, Search
} from 'lucide-react';
import { useDealForgeStore } from '@/lib/dealforge-store';
import { useState } from 'react';
import { useIsMobile } from '@/hooks/use-mobile';
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';

interface ChatSidebarProps {
    collapsed: boolean;
    onToggle: () => void;
}

export function ChatSidebar({ collapsed, onToggle }: ChatSidebarProps) {
    const isMobile = useIsMobile();
    const isCollapsed = collapsed || isMobile;
    const {
        conversations,
        activeConversationId,
        createConversation,
        setActiveConversation,
        deleteConversation,
    } = useDealForgeStore();

    const [searchQuery, setSearchQuery] = useState('');
    const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

    const filtered = searchQuery
        ? conversations.filter(c =>
            c.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
            c.dealId?.toLowerCase().includes(searchQuery.toLowerCase())
        )
        : conversations;

    const handleNewChat = () => {
        createConversation();
    };

    const handleDelete = (id: string, e: React.MouseEvent) => {
        e.stopPropagation();
        setPendingDeleteId(id);
    };

    const confirmDelete = () => {
        if (pendingDeleteId) deleteConversation(pendingDeleteId);
        setPendingDeleteId(null);
    };

    const formatTime = (ts: number) => {
        const d = new Date(ts);
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    };

    return (
        <aside
            className={`flex flex-col bg-slate-50 dark:bg-slate-900/50 border-r transition-all duration-300 ease-in-out flex-shrink-0 ${isCollapsed ? 'w-14 sm:w-16' : 'w-64'
                }`}
        >
            {/* Header */}
            <div className="p-3 border-b flex items-center justify-between flex-shrink-0">
                {!isCollapsed && (
                    <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                        Conversations
                    </span>
                )}
                <button
                    onClick={onToggle}
                    title={isMobile ? 'Conversation history' : isCollapsed ? 'Expand' : 'Collapse'}
                    className={`p-1.5 rounded-md hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors ${isMobile ? 'hidden' : ''}`}
                >
                    {isCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
                </button>
            </div>

            {/* New Chat Button */}
            <div className="p-2 flex-shrink-0">
                <button
                    onClick={handleNewChat}
                    className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg bg-primary/10 hover:bg-primary/20 text-primary text-sm font-medium transition-colors ${isCollapsed ? 'justify-center px-2' : ''
                        }`}
                    title="New Analysis"
                >
                    <Plus className="h-4 w-4 flex-shrink-0" />
                    {!isCollapsed && <span>New Analysis</span>}
                </button>
            </div>

            {/* Search */}
            {!isCollapsed && (
                <div className="px-2 pb-2 flex-shrink-0">
                    <div className="relative">
                        <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={e => setSearchQuery(e.target.value)}
                            placeholder="Search..."
                            className="w-full pl-8 pr-3 py-1.5 text-xs border rounded-md bg-white dark:bg-slate-800"
                        />
                    </div>
                </div>
            )}

            {/* Conversation List */}
            <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
                {filtered.length === 0 && !isCollapsed && (
                    <div className="text-center py-6 text-muted-foreground text-xs">
                        <MessageSquare className="h-6 w-6 mx-auto mb-2 opacity-30" />
                        <p>No conversations yet</p>
                    </div>
                )}

                {filtered.map(conv => {
                    const isActive = conv.id === activeConversationId;
                    const agentCount = new Set(conv.messages.filter(m => m.agentName).map(m => m.agentName)).size;

                    return (
                        <div
                            key={conv.id}
                            className={`w-full rounded-lg transition-all duration-150 group ${isCollapsed ? 'p-2 justify-center flex' : 'px-1 py-1'
                                } ${isActive
                                    ? 'bg-primary/10 border border-primary/20 shadow-sm'
                                    : 'hover:bg-slate-100 dark:hover:bg-slate-800'
                                }`}
                        >
                            {isCollapsed ? (
                                <button
                                    type="button"
                                    onClick={() => setActiveConversation(conv.id)}
                                    title={conv.title}
                                    aria-label={`Open ${conv.title}`}
                                    className="flex w-full justify-center rounded-md p-1"
                                >
                                    <MessageSquare className={`h-4 w-4 ${isActive ? 'text-primary' : 'text-slate-400'}`} />
                                </button>
                            ) : (
                                <div className="flex items-start justify-between w-full">
                                    <button
                                        type="button"
                                        onClick={() => setActiveConversation(conv.id)}
                                        className="flex-1 min-w-0 px-2 py-1 text-left"
                                        aria-current={isActive ? 'page' : undefined}
                                    >
                                        <p className={`text-xs font-medium truncate ${isActive ? 'text-primary' : 'text-slate-700 dark:text-slate-200'}`}>
                                            {conv.title}
                                        </p>
                                        <div className="flex items-center gap-2 mt-1">
                                            {conv.dealId && (
                                                <span className="inline-flex items-center gap-0.5 text-[10px] text-slate-400">
                                                    <Briefcase className="h-2.5 w-2.5" />
                                                    {conv.dealId.slice(0, 8)}
                                                </span>
                                            )}
                                            <span className="text-[10px] text-slate-400 flex items-center gap-0.5">
                                                <Clock className="h-2.5 w-2.5" />
                                                {formatTime(conv.updatedAt)}
                                            </span>
                                            {agentCount > 0 && (
                                                <span className="text-[10px] px-1 rounded bg-slate-200 dark:bg-slate-700 text-slate-500">
                                                    {agentCount} agents
                                                </span>
                                            )}
                                        </div>
                                    </button>
                                    <button
                                        type="button"
                                        onClick={e => handleDelete(conv.id, e)}
                                        className="mt-1 p-1 rounded opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-red-100 hover:text-red-500 transition-all"
                                        title="Delete"
                                        aria-label={`Delete ${conv.title}`}
                                    >
                                        <Trash2 className="h-3 w-3" />
                                    </button>
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>

            {/* Footer */}
            {!isCollapsed && (
                <div className="p-2 border-t text-[10px] text-muted-foreground text-center">
                    {conversations.length} conversation{conversations.length !== 1 ? 's' : ''}
                </div>
            )}

            {/* Delete confirmation */}
            <AlertDialog open={pendingDeleteId !== null} onOpenChange={open => { if (!open) setPendingDeleteId(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete conversation?</AlertDialogTitle>
                        <AlertDialogDescription>
                            This removes the conversation and its messages from this device and the backend. This action cannot be undone.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={confirmDelete} className="bg-red-600 hover:bg-red-700 focus:ring-red-600">
                            Delete
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </aside>
    );
}
