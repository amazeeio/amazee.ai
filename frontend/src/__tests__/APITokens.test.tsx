import React from 'react';
import { render, screen, waitFor, waitForElementToBeRemoved, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { APITokens } from '../pages/APITokens';
import { tokens } from '../api/client';
import { AuthProvider } from '../contexts/AuthContext';

// Mock the tokens API
jest.mock('../api/client', () => ({
  tokens: {
    list: jest.fn(),
    create: jest.fn(),
    delete: jest.fn(),
  },
  auth: {
    me: jest.fn().mockResolvedValue({
      id: 1,
      email: 'test@example.com',
      is_active: true,
      is_admin: true,
    }),
  },
}));

// Mock window.confirm
const mockConfirm = jest.fn(() => true);
Object.defineProperty(window, 'confirm', { value: mockConfirm });

const mockTokens = [
  {
    id: 1,
    name: 'Test Token 1',
    created_at: '2024-02-15T00:00:00Z',
    last_used_at: '2024-02-16T00:00:00Z',
  },
  {
    id: 2,
    name: 'Test Token 2',
    created_at: '2024-02-14T00:00:00Z',
    last_used_at: null,
  },
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

const renderAPITokens = async () => {
  (tokens.list as jest.Mock).mockResolvedValue(mockTokens);

  let result;
  await act(async () => {
    result = render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <APITokens />
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    );
  });
  return result;
};

describe('APITokens Component', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    queryClient.clear();
  });

  it('renders loading state initially', async () => {
    (tokens.list as jest.Mock).mockImplementation(() => new Promise(resolve => setTimeout(() => resolve(mockTokens), 100)));

    await act(async () => {
      renderAPITokens();
    });

    // Verify loading spinner is shown
    const loadingSpinner = document.querySelector('.animate-spin');
    expect(loadingSpinner).toBeInTheDocument();

    // Wait for loading to finish
    await waitForElementToBeRemoved(() => screen.queryByTestId('loading-spinner'));
  });

  it('renders list of tokens', async () => {
    await renderAPITokens();

    // Wait for loading spinner to disappear
    await waitForElementToBeRemoved(() => document.querySelector('.animate-spin'));

    expect(screen.getByText('Test Token 1')).toBeInTheDocument();
    expect(screen.getByText('Test Token 2')).toBeInTheDocument();
    expect(screen.getByText(/created: 2\/15\/2024/i)).toBeInTheDocument();
    expect(screen.getByText(/last used: 2\/16\/2024/i)).toBeInTheDocument();
  });

  it('creates a new token', async () => {
    const newToken = {
      id: 3,
      name: 'New Token',
      token: 'secret-token-value',
      created_at: '2024-02-17T00:00:00Z',
    };
    (tokens.create as jest.Mock).mockResolvedValueOnce(newToken);

    await renderAPITokens();

    // Wait for content to load
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
      expect(screen.getByPlaceholderText('Token name')).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText('Token name');
    const createButton = screen.getByRole('button', { name: /create token/i });

    await act(async () => {
      await userEvent.type(input, 'New Token');
      await userEvent.click(createButton);
    });

    await waitFor(() => {
      expect(tokens.create).toHaveBeenCalledWith('New Token');
    });

    await waitFor(() => {
      expect(screen.getByText('New Token Created')).toBeInTheDocument();
      expect(screen.getByText('secret-token-value')).toBeInTheDocument();
    });
  });

  it('deletes a token after confirmation', async () => {
    (tokens.delete as jest.Mock).mockResolvedValueOnce({});
    window.confirm = jest.fn(() => true);

    await renderAPITokens();

    // Wait for content to load
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
      expect(screen.getAllByRole('button', { name: /delete/i })).toHaveLength(2);
    });

    const deleteButtons = screen.getAllByRole('button', { name: /delete/i });
    await act(async () => {
      await userEvent.click(deleteButtons[0]);
    });

    expect(window.confirm).toHaveBeenCalled();
    expect(tokens.delete).toHaveBeenCalledWith(1);
  });

  it('shows empty state when no tokens exist', async () => {
    (tokens.list as jest.Mock).mockResolvedValueOnce([]);
    await renderAPITokens();

    // Wait for content to load
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
      expect(screen.getByText('No API tokens found. Create one to get started.')).toBeInTheDocument();
    });
  });

  it('disables create button when input is empty', async () => {
    await renderAPITokens();

    // Wait for content to load
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /create token/i })).toBeInTheDocument();
    });

    const createButton = screen.getByRole('button', { name: /create token/i });
    expect(createButton).toBeDisabled();

    const input = screen.getByPlaceholderText('Token name');
    await act(async () => {
      await userEvent.type(input, 'New Token');
    });
    expect(createButton).not.toBeDisabled();

    await act(async () => {
      await userEvent.clear(input);
    });
    expect(createButton).toBeDisabled();
  });
});