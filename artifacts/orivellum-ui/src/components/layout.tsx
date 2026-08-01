import { Link, useLocation } from "wouter";
import { 
  Sidebar, 
  SidebarContent, 
  SidebarHeader, 
  SidebarMenu, 
  SidebarMenuButton, 
  SidebarMenuItem, 
  SidebarProvider,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
} from "@/components/ui/sidebar";
import { 
  Library, 
  MessageSquare, 
  BookOpen, 
  FolderOpen, 
  Target, 
  Settings, 
  HardDrive, 
  Activity, 
  Mic 
} from "lucide-react";

export function AppLayout({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();

  const navigation = [
    { name: "Dashboard", href: "/", icon: Activity },
    { name: "Conversations", href: "/chat", icon: MessageSquare },
    { name: "Works", href: "/works", icon: BookOpen },
    { name: "Library", href: "/library", icon: Library },
    { name: "Projects", href: "/projects", icon: Target },
    { name: "Studio", href: "/studio", icon: Mic },
    { name: "Files", href: "/files", icon: FolderOpen },
  ];

  const systemNavigation = [
    { name: "Backups", href: "/backups", icon: HardDrive },
    { name: "System", href: "/system", icon: Settings },
  ];

  return (
    <SidebarProvider>
      <div className="flex min-h-screen w-full">
        <Sidebar className="border-r border-border/50 bg-sidebar">
          <SidebarHeader className="p-4 flex flex-row items-center gap-2">
            <div className="bg-primary text-primary-foreground w-8 h-8 rounded-sm flex items-center justify-center font-serif font-bold text-lg">
              O
            </div>
            <div className="font-serif font-bold text-xl tracking-tight">Orivellum</div>
          </SidebarHeader>
          
          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel className="font-mono text-xs uppercase tracking-wider text-muted-foreground">Workspace</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {navigation.map((item) => (
                    <SidebarMenuItem key={item.name}>
                      <SidebarMenuButton asChild isActive={location === item.href || (item.href !== '/' && location.startsWith(item.href))}>
                        <Link href={item.href} className="flex items-center gap-3">
                          <item.icon className="w-4 h-4" />
                          <span className="font-medium">{item.name}</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>

            <SidebarGroup className="mt-auto pb-4">
              <SidebarGroupLabel className="font-mono text-xs uppercase tracking-wider text-muted-foreground">Infrastructure</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {systemNavigation.map((item) => (
                    <SidebarMenuItem key={item.name}>
                      <SidebarMenuButton asChild isActive={location.startsWith(item.href)}>
                        <Link href={item.href} className="flex items-center gap-3">
                          <item.icon className="w-4 h-4" />
                          <span className="font-medium">{item.name}</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          </SidebarContent>
        </Sidebar>

        <main className="flex-1 overflow-auto bg-background selection:bg-primary/20">
          <div className="h-full w-full max-w-[1400px] mx-auto p-8">
            {children}
          </div>
        </main>
      </div>
    </SidebarProvider>
  );
}
