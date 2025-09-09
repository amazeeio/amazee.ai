'use client';

import { ReactNode } from 'react';
import { usePathname } from 'next/navigation';

export default function AdminLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isSalesDashboard = pathname === '/admin/sales-dashboard';

  return (
    <div className="space-y-6">
      <div className={isSalesDashboard ? "w-full min-w-0" : "mx-auto max-w-7xl px-4 sm:px-6 lg:px-8"}>
        {children}
      </div>
    </div>
  );
}