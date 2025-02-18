import React from 'react';
import { render, screen, waitFor, waitForElementToBeRemoved, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Admin } from '../pages/Admin';
import { users, regions, privateAIKeys, auth } from '../api/client';
import { AuthProvider } from '../contexts/AuthContext';

// Mock the APIs
jest.mock('../api/client', () => ({
  users: {
    list: jest.fn(),
    update: jest.fn(),
  },
  regions: {
    list: jest.fn(),
    create: jest.fn(),
    delete: jest.fn(),
  },
  privateAIKeys: {
    list: jest.fn(),
    delete: jest.fn(),
  },
  auth: {
    me: jest.fn(),
  },
}));

// Mock useAuth hook and AuthProvider
jest.mock('../contexts/AuthContext', () => {
  const mockUser = { id: 1, email: 'admin@example.com', is_active: true, is_admin: true };
  return {
    useAuth: () => ({
      user: mockUser,
      isLoading: false,
    }),
    AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  };
});

// Mock window.confirm
const mockConfirm = jest.fn(() => true);
Object.defineProperty(window, 'confirm', { value: mockConfirm });

const mockUser = { email: 'admin@example.com', is_admin: true };

const mockUsers = [
  { id: 1, email: 'user@example.com', is_active: true, is_admin: false },
  { id: 2, email: 'admin@example.com', is_active: true, is_admin: true }
];

const mockPrivateAIKeys = [
  {
    database_name: 'db1',
    host: 'localhost',
    username: 'user1',
    password: 'pass1',
    litellm_token: 'token1',
    region: 'us-east-1',
    owner_id: 1
  }
];

const mockRegions = [
  { id: 1, name: 'us-east-1', postgres_host: 'localhost', postgres_port: 5432, is_active: true }
];

const mockNewRegion = {
  id: 2,
  name: 'test-region',
  postgres_host: 'localhost',
  postgres_port: 5432,
  postgres_db: 'test',
  postgres_user: 'test',
  postgres_password: 'test',
  litellm_api_url: 'http://localhost:8800',
  litellm_api_key: 'test-key',
};

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
    },
  },
});

const renderAdmin = async () => {
  (auth.me as jest.Mock).mockResolvedValue(mockUser);
  (privateAIKeys.list as jest.Mock).mockResolvedValue(mockPrivateAIKeys);
  (regions.list as jest.Mock).mockResolvedValue(mockRegions);
  (users.list as jest.Mock).mockResolvedValue(mockUsers);

  let result;
  await act(async () => {
    result = render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <Admin />
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    );
  });
  return result;
};

// Increase timeout for all tests in this suite
jest.setTimeout(60000);

describe('Admin Component', () => {
  jest.setTimeout(60000); // Increase timeout to 60 seconds

  beforeEach(() => {
    // Clear all mocks
    jest.clearAllMocks();
    queryClient.clear();

    // Mock auth.me
    (auth.me as jest.Mock).mockResolvedValue(mockUser);

    // Mock React Query responses
    (regions.list as jest.Mock).mockResolvedValue(mockRegions);
    (users.list as jest.Mock).mockResolvedValue(mockUsers);
    (privateAIKeys.list as jest.Mock).mockResolvedValue(mockPrivateAIKeys);
  });

  it('renders loading state initially', async () => {
    // Mock the API calls to resolve after a delay to ensure loading state is visible
    (users.list as jest.Mock).mockImplementation(() => new Promise(resolve => setTimeout(() => resolve(mockUsers), 100)));
    (privateAIKeys.list as jest.Mock).mockImplementation(() => new Promise(resolve => setTimeout(() => resolve(mockPrivateAIKeys), 100)));
    (regions.list as jest.Mock).mockImplementation(() => new Promise(resolve => setTimeout(() => resolve(mockRegions), 100)));
    (auth.me as jest.Mock).mockResolvedValueOnce(mockUser);

    await act(async () => {
      render(
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <AuthProvider>
              <Admin />
            </AuthProvider>
          </BrowserRouter>
        </QueryClientProvider>
      );
    });

    // Verify loading spinner is shown
    const loadingSpinner = document.querySelector('.animate-spin');
    expect(loadingSpinner).toBeInTheDocument();

    // Wait for loading to finish
    await waitForElementToBeRemoved(() => document.querySelector('.animate-spin'));

    // Verify content is loaded
    expect(screen.getByText('Admin Dashboard')).toBeInTheDocument();
  });

  it('renders admin dashboard with all sections', async () => {
    await renderAdmin();

    // Wait for loading spinner to disappear
    await waitForElementToBeRemoved(() => document.querySelector('.animate-spin'));

    expect(screen.getByText('Admin Dashboard')).toBeInTheDocument();
    expect(screen.getByText('Regions')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add region/i })).toBeInTheDocument();
  });

  it('toggles admin status for a user', async () => {
    (users.update as jest.Mock).mockResolvedValueOnce({ ...mockUsers[1], is_admin: true });
    (auth.me as jest.Mock).mockResolvedValueOnce(mockUser);

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Admin />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Wait for loading spinner to disappear
    await waitForElementToBeRemoved(() => screen.queryByTestId('loading-spinner'));

    // Wait for the users table to be rendered
    await waitFor(() => {
      expect(screen.getAllByRole('cell', { name: 'user@example.com' })[0]).toBeInTheDocument();
    });

    // Find and click the toggle button for the specific user
    const toggleButton = screen.getByRole('switch', { name: /toggle admin status for user@example.com/i });
    await userEvent.click(toggleButton);

    await waitFor(() => {
      expect(users.update).toHaveBeenCalledWith(1, { is_admin: true });
    });
  });

  it('creates a new region', async () => {
    // Mock successful region creation
    (regions.create as jest.Mock).mockResolvedValueOnce(mockNewRegion);
    (regions.list as jest.Mock).mockResolvedValue(mockRegions);
    (auth.me as jest.Mock).mockResolvedValueOnce(mockUser);

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Admin />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Wait for loading spinner to disappear
    await waitForElementToBeRemoved(() => screen.queryByTestId('loading-spinner'));

    // Click add region button
    const addButton = screen.getByRole('button', { name: /add region/i });
    await userEvent.click(addButton);

    // Fill in the form
    const nameInput = screen.getByLabelText(/name/i);
    const hostInput = screen.getByLabelText(/postgres host/i);
    const portInput = screen.getByLabelText(/postgres port/i);
    const dbInput = screen.getByLabelText(/postgres database/i);
    const userInput = screen.getByLabelText(/postgres admin user/i);
    const passwordInput = screen.getByLabelText(/postgres admin password/i);
    const apiUrlInput = screen.getByLabelText(/litellm api url/i);
    const apiKeyInput = screen.getByLabelText(/litellm api key/i);

    await userEvent.type(nameInput, 'test-region');
    await userEvent.type(hostInput, 'localhost');
    await userEvent.type(portInput, '5432');
    await userEvent.type(dbInput, 'test');
    await userEvent.type(userInput, 'test');
    await userEvent.type(passwordInput, 'test');
    await userEvent.type(apiUrlInput, 'http://localhost:8800');
    await userEvent.type(apiKeyInput, 'test-key');

    // Submit the form
    const submitButton = screen.getByRole('button', { name: /create/i });
    await userEvent.click(submitButton);

    await waitFor(() => {
      expect(regions.create).toHaveBeenCalledWith(expect.objectContaining({
        name: 'test-region',
        postgres_host: 'localhost',
        postgres_port: 5432,
        postgres_db: 'test',
        postgres_admin_user: 'test',
        postgres_admin_password: 'test',
        litellm_api_url: 'http://localhost:8800',
        litellm_api_key: 'test-key',
      }));
    });
  });

  it('deletes a region after confirmation', async () => {
    (regions.delete as jest.Mock).mockResolvedValueOnce({});
    mockConfirm.mockReturnValueOnce(true);

    await renderAdmin();

    // Wait for content to load
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
    });

    // Find and click the delete button
    const deleteButton = await screen.findByRole('button', { name: /delete region/i });
    await act(async () => {
      await userEvent.click(deleteButton);
    });

    expect(mockConfirm).toHaveBeenCalled();
    expect(regions.delete).toHaveBeenCalledWith(1);
  });

  it('deletes a private AI key after confirmation', async () => {
    (privateAIKeys.delete as jest.Mock).mockResolvedValueOnce({});
    mockConfirm.mockReturnValueOnce(true);

    await renderAdmin();

    // Wait for content to load
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
    });

    // Find and click the delete button
    const deleteButton = await screen.findByRole('button', { name: /delete private ai key/i });
    await act(async () => {
      await userEvent.click(deleteButton);
    });

    expect(mockConfirm).toHaveBeenCalled();
    expect(privateAIKeys.delete).toHaveBeenCalledWith('db1');
  });

  it('shows error message when delete operation fails', async () => {
    const errorMessage = 'Failed to delete region';
    (regions.delete as jest.Mock).mockRejectedValueOnce(new Error(errorMessage));
    mockConfirm.mockReturnValueOnce(true);

    await renderAdmin();

    // Wait for content to load
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
    });

    // Find and click the delete button
    const deleteButton = await screen.findByRole('button', { name: /delete region/i });
    await act(async () => {
      await userEvent.click(deleteButton);
    });

    expect(mockConfirm).toHaveBeenCalled();
    expect(regions.delete).toHaveBeenCalledWith(1);

    // Wait for error message
    await waitFor(() => {
      expect(screen.getByTestId('error-message')).toHaveTextContent(errorMessage);
    });
  });

  it('should show error when trying to delete a region with active private AI keys', async () => {
    const errorMessage = 'Cannot delete region with active private AI keys';
    (regions.delete as jest.Mock).mockRejectedValueOnce(new Error(errorMessage));
    mockConfirm.mockReturnValueOnce(true);

    await renderAdmin();

    // Wait for content to load
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
    });

    // Find and click the delete button
    const deleteButton = await screen.findByRole('button', { name: /delete region/i });
    await act(async () => {
      await userEvent.click(deleteButton);
    });

    expect(mockConfirm).toHaveBeenCalled();
    expect(regions.delete).toHaveBeenCalledWith(1);

    // Wait for error message
    await waitFor(() => {
      expect(screen.getByTestId('error-message')).toHaveTextContent(errorMessage);
    });
  });

  it('should create a new region successfully', async () => {
    jest.setTimeout(30000); // Increase timeout to 30 seconds
    (regions.create as jest.Mock).mockResolvedValueOnce({ data: { message: 'Region created successfully' } });

    await renderAdmin();

    // First click the "Add Region" button to show the form
    const addButton = await screen.findByRole('button', { name: /add region/i });
    await act(async () => {
      await userEvent.click(addButton);
    });

    // Now fill in the form fields
    await act(async () => {
      await userEvent.type(screen.getByLabelText('Region name'), 'new-region');
      await userEvent.type(screen.getByLabelText('Postgres host'), 'localhost');

      // Set postgres port
      const portInput = screen.getByLabelText('Postgres port');
      await userEvent.clear(portInput);
      await userEvent.type(portInput, '5432', { delay: 0 });

      await userEvent.type(screen.getByLabelText('Postgres admin user'), 'admin');
      await userEvent.type(screen.getByLabelText('Postgres admin password'), 'password');
      await userEvent.type(screen.getByLabelText('LiteLLM API URL'), 'http://localhost:8800');
      await userEvent.type(screen.getByLabelText('LiteLLM API key'), 'test-key');
    });

    // Submit the form
    const createButton = await screen.findByRole('button', { name: /create region/i });
    await act(async () => {
      await userEvent.click(createButton);
    });

    // Wait for the mutation to complete
    await waitFor(() => {
      expect(regions.create).toHaveBeenCalledWith(expect.objectContaining({
        name: 'new-region',
        postgres_host: 'localhost',
        postgres_port: 5432,
        postgres_admin_user: 'admin',
        postgres_admin_password: 'password',
        litellm_api_url: 'http://localhost:8800',
        litellm_api_key: 'test-key'
      }));
    });
  });

  it('handles region deletion', async () => {
    (regions.delete as jest.Mock).mockRejectedValueOnce(new Error('Failed to delete region'));
    (auth.me as jest.Mock).mockResolvedValueOnce(mockUser);
    (regions.list as jest.Mock).mockResolvedValue(mockRegions);
    (users.list as jest.Mock).mockResolvedValue(mockUsers);
    (privateAIKeys.list as jest.Mock).mockResolvedValue([]);

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Admin />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Wait for loading spinner to disappear
    await waitForElementToBeRemoved(() => screen.queryByTestId('loading-spinner'));

    // Click delete button
    const deleteButton = screen.getByRole('button', { name: /delete region us-east-1/i });
    window.confirm = jest.fn(() => true);
    await userEvent.click(deleteButton);

    // Verify error message is shown
    await waitFor(() => {
      expect(screen.getByText(/failed to delete region/i)).toBeInTheDocument();
    });
  });

  it('prevents deletion of region with active keys', async () => {
    (regions.delete as jest.Mock).mockRejectedValueOnce(new Error('Cannot delete region with active private AI keys'));
    (auth.me as jest.Mock).mockResolvedValueOnce(mockUser);
    (regions.list as jest.Mock).mockResolvedValue(mockRegions);
    (users.list as jest.Mock).mockResolvedValue(mockUsers);
    (privateAIKeys.list as jest.Mock).mockResolvedValue([]);

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Admin />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Wait for loading spinner to disappear
    await waitForElementToBeRemoved(() => screen.queryByTestId('loading-spinner'));

    // Click delete button
    const deleteButton = screen.getByRole('button', { name: /delete region us-east-1/i });
    window.confirm = jest.fn(() => true);
    await userEvent.click(deleteButton);

    // Verify error message is shown
    await waitFor(() => {
      expect(screen.getByText(/cannot delete region with active private ai keys/i)).toBeInTheDocument();
    });
  });
});