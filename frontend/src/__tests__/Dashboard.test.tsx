import React from 'react';
import { render, screen, waitFor, waitForElementToBeRemoved, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Dashboard } from '../pages/Dashboard';
import { users, tokens, auth, privateAIKeys, regions } from '../api/client';
import { AuthProvider } from '../contexts/AuthContext';

// Increase test timeout
jest.setTimeout(30000);

// Mock window.confirm
const mockConfirm = jest.fn(() => true);
Object.defineProperty(window, 'confirm', { value: mockConfirm });

jest.mock('../api/client', () => ({
  users: {
    list: jest.fn(),
    create: jest.fn(),
    delete: jest.fn()
  },
  tokens: {
    list: jest.fn(),
    create: jest.fn(),
    delete: jest.fn()
  },
  auth: {
    me: jest.fn()
  },
  privateAIKeys: {
    list: jest.fn(),
    create: jest.fn(),
    delete: jest.fn()
  },
  regions: {
    list: jest.fn()
  }
}));

const mockUser = {
  id: 1,
  email: 'admin@example.com',
  is_active: true,
  is_admin: true
};

const mockPrivateAIKeys = [
  {
    id: '1',
    database_name: 'key1',
    host: 'host1.example.com',
    username: 'user1',
    region: 'us-east-1',
    password: 'password1',
    litellm_token: 'token1'
  },
  {
    id: '2',
    database_name: 'key2',
    host: 'host2.example.com',
    username: 'user2',
    region: 'us-west-1',
    password: 'password2',
    litellm_token: 'token2'
  }
];

const mockRegions = [
  { id: 1, name: 'us-east-1', display_name: 'US East 1', is_active: true },
  { id: 2, name: 'us-west-1', display_name: 'US West 1', is_active: true }
];

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
    },
    mutations: {
      retry: false,
    },
  },
});

const renderDashboard = () => {
  (auth.me as jest.Mock).mockResolvedValue(mockUser);
  (privateAIKeys.list as jest.Mock).mockResolvedValue(mockPrivateAIKeys);
  (regions.list as jest.Mock).mockResolvedValue(mockRegions);

  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Dashboard />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
};

describe('Dashboard Component', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    queryClient.clear();
  });

  it('renders dashboard with private AI keys and regions', async () => {
    (auth.me as jest.Mock).mockResolvedValue(mockUser);
    (privateAIKeys.list as jest.Mock).mockResolvedValue(mockPrivateAIKeys);
    (regions.list as jest.Mock).mockResolvedValue(mockRegions);

    await act(async () => {
      renderDashboard();
    });

    // Wait for loading state to disappear
    await waitForElementToBeRemoved(() => screen.queryByText('Loading...'));

    // Check for private AI key details
    expect(screen.getByText('key1')).toBeInTheDocument();

    const hostElement = screen.getByText((content, element) => {
      return element?.textContent === 'Host: host1.example.com';
    });
    expect(hostElement).toBeInTheDocument();

    const usernameElement = screen.getByText((content, element) => {
      return element?.textContent === 'Username: user1';
    });
    expect(usernameElement).toBeInTheDocument();
  });

  it('handles private AI key creation', async () => {
    (privateAIKeys.create as jest.Mock).mockResolvedValue({ id: 3, database_name: 'new-key' });
    (auth.me as jest.Mock).mockResolvedValue(mockUser);
    (privateAIKeys.list as jest.Mock).mockResolvedValue(mockPrivateAIKeys);
    (regions.list as jest.Mock).mockResolvedValue(mockRegions);

    await act(async () => {
      renderDashboard();
    });

    // Wait for loading state to disappear
    await waitForElementToBeRemoved(() => screen.queryByText('Loading...'));

    // Click create button to show the form
    await act(async () => {
      await userEvent.click(screen.getByText(/create new private ai key/i));
    });

    // Wait for region select to be available and select a region
    const regionSelect = await screen.findByRole('combobox');
    await act(async () => {
      await userEvent.selectOptions(regionSelect, '1');
    });

    // Click create button in the form
    await act(async () => {
      const createButton = screen.getByRole('button', { name: /create private ai key/i });
      await userEvent.click(createButton);
    });

    // Wait for the API call
    await waitFor(() => {
      expect(privateAIKeys.create).toHaveBeenCalledWith({ region_id: 1 });
    });
  });

  it('handles private AI key deletion', async () => {
    // Mock window.confirm to return true
    const confirmSpy = jest.spyOn(window, 'confirm').mockImplementation(() => true);
    (auth.me as jest.Mock).mockResolvedValue(mockUser);
    (privateAIKeys.list as jest.Mock).mockResolvedValue(mockPrivateAIKeys);
    (regions.list as jest.Mock).mockResolvedValue(mockRegions);
    (privateAIKeys.delete as jest.Mock).mockResolvedValue({});

    await act(async () => {
      renderDashboard();
    });

    // Wait for content to load
    await waitFor(() => {
      expect(screen.queryByText(/loading/i)).not.toBeInTheDocument();
      expect(screen.getAllByRole('button', { name: /delete/i })).toHaveLength(2);
    });

    // Get delete buttons
    const deleteButtons = screen.getAllByRole('button', { name: /delete/i });

    // Click the first delete button
    await act(async () => {
      await userEvent.click(deleteButtons[0]);
    });

    // Wait for the delete mutation to be called
    await waitFor(() => {
      expect(privateAIKeys.delete).toHaveBeenCalledWith('1');
    });

    // Clean up mock
    confirmSpy.mockRestore();
  });
});