"use client"

import Link from 'next/link';
import { ChevronDown } from 'lucide-react';
import { useState } from 'react';

interface NavItem {
  name: string;
  href: string;
  subItems?: NavItem[];
}

interface NavMainProps {
  navigation: NavItem[];
  pathname: string;
}

export function NavMain({ navigation, pathname }: NavMainProps) {
  const [expandedItems, setExpandedItems] = useState<string[]>(['/admin']);

  const toggleExpanded = (href: string) => {
    setExpandedItems((prev) =>
      prev.includes(href)
        ? prev.filter((item) => item !== href)
        : [...prev, href]
    );
  };

  const renderNavItem = (item: NavItem, level = 0) => {
    const isActive = pathname === item.href;
    const hasSubItems = item.subItems && item.subItems.length > 0;
    const isExpanded = expandedItems.includes(item.href);
    const isSubItemActive = hasSubItems && item.subItems?.some(
      (subItem) => pathname === subItem.href
    );

    return (
      <div key={item.href}>
        <Link
          href={hasSubItems ? '#' : item.href}
          onClick={hasSubItems ? () => toggleExpanded(item.href) : undefined}
          className={`flex items-center justify-between rounded-lg px-3 py-2 transition-all ${
            level > 0 ? 'ml-4' : ''
          } ${
            isActive || isSubItemActive
              ? 'bg-accent text-accent-foreground'
              : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
          }`}
        >
          <span>{item.name}</span>
          {hasSubItems && (
            <ChevronDown
              className={`h-4 w-4 transition-transform ${
                isExpanded ? 'rotate-180' : ''
              }`}
            />
          )}
        </Link>
        {hasSubItems && isExpanded && item.subItems && (
          <div className="mt-1">
            {item.subItems.map((subItem) => renderNavItem(subItem, level + 1))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="flex-1 overflow-auto">
      <nav className="grid items-start px-4 text-sm font-medium gap-1">
        {navigation.map((item) => renderNavItem(item))}
      </nav>
    </div>
  );
}
