import { useState, useRef, useEffect } from "react";
import { useLocation } from "wouter";
import { 
  useListConversations, 
  useGetConversation, 
  useCreateConversation,
  useSendMessage,
  getListConversationsQueryKey,
  getGetConversationQueryKey
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { MessageSquare, Plus, Send, MoreVertical, Search, Bot, User } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";

export default function Chat() {
  const [location, setLocation] = useLocation();
  const searchParams = new URLSearchParams(window.location.search);
  const activeId = searchParams.get('id');
  
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [newMessage, setNewMessage] = useState("");
  
  const { data: convsResp, isLoading: loadingList } = useListConversations();
  const { data: activeConv, isLoading: loadingActive } = useGetConversation(activeId!, { query: { enabled: !!activeId, queryKey: getGetConversationQueryKey(activeId!) } });
  
  const createConv = useCreateConversation();
  const sendMessage = useSendMessage();
  
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeConv?.messages]);

  const handleCreate = () => {
    createConv.mutate({ data: { title: "New Conversation" } }, {
      onSuccess: (newC) => {
        queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
        if (newC?.conversation?.id) {
          setLocation(`/chat?id=${newC.conversation.id}`);
        }
      }
    });
  };

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newMessage.trim() || !activeId) return;
    
    // Optimistic UI could be added here
    sendMessage.mutate({ convId: activeId, data: { text: newMessage } }, {
      onSuccess: () => {
        setNewMessage("");
        queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(activeId) });
        queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
      }
    });
  };

  const filteredConvs = convsResp?.conversations?.filter(c => 
    c.title?.toLowerCase().includes(search.toLowerCase()) ||
    c.last_message?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="h-[calc(100vh-6rem)] flex gap-6 animate-in fade-in duration-500">
      {/* Sidebar */}
      <Card className="w-80 flex flex-col shrink-0 rounded-xl overflow-hidden border-border/50">
        <div className="p-4 border-b border-border/50 bg-muted/10 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-serif text-lg font-medium">Conversations</h2>
            <Button size="icon" variant="ghost" onClick={handleCreate} disabled={createConv.isPending}>
              <Plus className="w-4 h-4" />
            </Button>
          </div>
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input 
              placeholder="Search..." 
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-8 h-8 text-xs bg-background"
            />
          </div>
        </div>
        
        <ScrollArea className="flex-1">
          <div className="p-2 space-y-1">
            {loadingList ? (
              [1, 2, 3, 4].map(i => <Skeleton key={i} className="h-16 w-full rounded-md" />)
            ) : filteredConvs?.map(conv => (
              <div 
                key={conv.id}
                onClick={() => setLocation(`/chat?id=${conv.id}`)}
                className={`p-3 rounded-md cursor-pointer transition-colors ${activeId === conv.id ? 'bg-primary/10 text-primary' : 'hover:bg-muted/50'}`}
              >
                <div className="font-medium text-sm truncate">{conv.title || 'Untitled'}</div>
                <div className="text-xs text-muted-foreground truncate mt-1">
                  {conv.last_message || 'No messages'}
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
      </Card>

      {/* Main Chat Area */}
      <Card className="flex-1 flex flex-col rounded-xl overflow-hidden border-border/50">
        {activeId ? (
          <>
            {/* Header */}
            <div className="p-4 border-b border-border/50 bg-muted/10 flex justify-between items-center shrink-0">
              <div>
                <h2 className="font-serif text-lg font-medium">{activeConv?.conversation?.title || 'Loading...'}</h2>
                <div className="text-xs font-mono text-muted-foreground">
                  {activeConv?.conversation?.message_count || 0} messages
                </div>
              </div>
              <Button size="icon" variant="ghost"><MoreVertical className="w-4 h-4" /></Button>
            </div>

            {/* Messages */}
            <ScrollArea className="flex-1 p-6">
              <div className="max-w-3xl mx-auto space-y-8">
                {loadingActive ? (
                  <div className="space-y-8">
                    <Skeleton className="h-20 w-3/4 ml-auto" />
                    <Skeleton className="h-32 w-3/4" />
                  </div>
                ) : activeConv?.messages?.map((msg, i) => (
                  <div key={msg.id || i} className={`flex gap-4 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
                    <div className={`w-8 h-8 shrink-0 rounded-sm flex items-center justify-center ${msg.role === 'user' ? 'bg-secondary text-secondary-foreground' : 'bg-primary text-primary-foreground'}`}>
                      {msg.role === 'user' ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
                    </div>
                    <div className={`flex flex-col gap-1 ${msg.role === 'user' ? 'items-end' : 'items-start'} max-w-[80%]`}>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-medium uppercase tracking-wider text-muted-foreground">
                          {msg.role}
                        </span>
                        <span className="text-[10px] text-muted-foreground/50 font-mono">
                          {msg.created_at ? format(new Date(msg.created_at), 'HH:mm') : ''}
                        </span>
                      </div>
                      <div className={`p-4 rounded-lg text-sm leading-relaxed whitespace-pre-wrap ${
                        msg.role === 'user' 
                          ? 'bg-secondary/50 border border-secondary text-foreground' 
                          : 'bg-muted/30 border border-border/50'
                      }`}>
                        {msg.text}
                      </div>
                    </div>
                  </div>
                ))}
                <div ref={messagesEndRef} />
              </div>
            </ScrollArea>

            {/* Input */}
            <div className="p-4 bg-muted/10 border-t border-border/50 shrink-0">
              <form onSubmit={handleSend} className="max-w-3xl mx-auto relative">
                <Textarea 
                  value={newMessage}
                  onChange={(e) => setNewMessage(e.target.value)}
                  placeholder="Type a message..."
                  className="pr-12 resize-none py-3"
                  rows={2}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleSend(e);
                    }
                  }}
                />
                <Button 
                  type="submit" 
                  size="icon" 
                  disabled={!newMessage.trim() || sendMessage.isPending}
                  className="absolute right-2 top-2 h-8 w-8"
                >
                  <Send className="w-4 h-4" />
                </Button>
              </form>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-center p-8 text-muted-foreground">
            <MessageSquare className="w-12 h-12 mb-4 opacity-20" />
            <h3 className="font-serif text-xl font-medium text-foreground">No Conversation Selected</h3>
            <p className="mt-2 max-w-sm">Select a conversation from the sidebar or start a new one to begin chatting.</p>
            <Button onClick={handleCreate} disabled={createConv.isPending} className="mt-6">
              Start New Conversation
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
