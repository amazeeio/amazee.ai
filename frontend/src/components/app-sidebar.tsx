"use client"

import * as React from "react"
import {
  AudioWaveform,
  BookOpen,
  Bot,
  Command,
  Frame,
  GalleryVerticalEnd,
  Map,
  PieChart,
  Settings2,
  SquareTerminal,
} from "lucide-react"

import { NavMain } from "@/components/nav-main"
import { NavProjects } from "@/components/nav-projects"
import { NavUser } from "@/components/nav-user"
import { TeamSwitcher } from "@/components/team-switcher"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
} from "@/components/ui/sidebar"

// This is sample data.
const data = {
  user: {
    name: "shadcn",
    email: "m@example.com",
    avatar: "/avatars/shadcn.jpg",
  },
  teams: [
    {
      name: "Acme Inc",
      logo: GalleryVerticalEnd,
      plan: "Enterprise",
    },
    {
      name: "Acme Corp.",
      logo: AudioWaveform,
      plan: "Startup",
    },
    {
      name: "Evil Corp.",
      logo: Command,
      plan: "Free",
    },
  ],
  navMain: [
    {
      name: "Playground",
      href: "#",
      icon: SquareTerminal,
      subItems: [
        {
          name: "History",
          href: "#",
        },
        {
          name: "Starred",
          href: "#",
        },
        {
          name: "Settings",
          href: "#",
        },
      ],
    },
    {
      name: "Models",
      href: "#",
      icon: Bot,
      subItems: [
        {
          name: "Genesis",
          href: "#",
        },
        {
          name: "Explorer",
          href: "#",
        },
        {
          name: "Quantum",
          href: "#",
        },
      ],
    },
    {
      name: "Documentation",
      href: "#",
      icon: BookOpen,
      subItems: [
        {
          name: "Introduction",
          href: "#",
        },
        {
          name: "Get Started",
          href: "#",
        },
        {
          name: "Tutorials",
          href: "#",
        },
        {
          name: "Changelog",
          href: "#",
        },
      ],
    },
    {
      name: "Settings",
      href: "#",
      icon: Settings2,
      subItems: [
        {
          name: "General",
          href: "#",
        },
        {
          name: "Team",
          href: "#",
        },
        {
          name: "Billing",
          href: "#",
        },
        {
          name: "Limits",
          href: "#",
        },
      ],
    },
  ],
  projects: [
    {
      name: "Design Engineering",
      url: "#",
      icon: Frame,
    },
    {
      name: "Sales & Marketing",
      url: "#",
      icon: PieChart,
    },
    {
      name: "Travel",
      url: "#",
      icon: Map,
    },
  ],
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <TeamSwitcher teams={data.teams} />
      </SidebarHeader>
      <SidebarContent>
        <NavMain navigation={data.navMain} pathname={window.location.pathname} />
        <NavProjects projects={data.projects} />
      </SidebarContent>
      <SidebarFooter>
        <NavUser />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
