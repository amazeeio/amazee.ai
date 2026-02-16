import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import RegionsPage from './page';
import { expect, it, describe, vi } from 'vitest';

// Mock the sidebar layout and other components that might interfere
vi.mock('@/components/sidebar-layout', () => ({
  SidebarLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// Mock useToast
vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({
    toast: vi.fn(),
  }),
}));

const createTestQueryClient = () => new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
    },
  },
});

describe('RegionsPage Integration', () => {
  it('renders the regions list', async () => {
    const queryClient = createTestQueryClient();
    
    render(
      <QueryClientProvider client={queryClient}>
        <RegionsPage />
      </QueryClientProvider>
    );

    // Wait for the data to load
    await waitFor(() => {
      expect(screen.getByText('us-east-1')).toBeInTheDocument();
    });

    expect(screen.getByText('US East 1')).toBeInTheDocument();
    expect(screen.getByText('Test region description')).toBeInTheDocument();
    expect(screen.getByText('Shared')).toBeInTheDocument();
    
    expect(screen.getByText('us-west-2')).toBeInTheDocument();
    expect(screen.getByText('Dedicated')).toBeInTheDocument();
  });

  it('shows Manage Teams button only for dedicated regions', async () => {
    const queryClient = createTestQueryClient();
    
    render(
      <QueryClientProvider client={queryClient}>
        <RegionsPage />
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('us-east-1')).toBeInTheDocument();
    });

    // us-east-1 is shared, should show N/A in teams column
    const eastRow = screen.getByText('us-east-1').closest('tr');
    expect(eastRow).toContainHTML('N/A');

    // us-west-2 is dedicated, should have Manage Teams button
    const westRow = screen.getByText('us-west-2').closest('tr');
    expect(westRow).toContainHTML('Manage Teams');
  });
});
