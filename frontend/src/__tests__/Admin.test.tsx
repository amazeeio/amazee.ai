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
  privateAIKeys: {
    list: jest.fn(),
    delete: jest.fn(),
  },
  regions: {
    list: jest.fn(),
    create: jest.fn(),
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
    // Mock the API calls to resolve immediately
    (users.list as jest.Mock).mockResolvedValueOnce(mockUsers);
    (privateAIKeys.list as jest.Mock).mockResolvedValueOnce(mockPrivateAIKeys);
    (regions.list as jest.Mock).mockResolvedValueOnce(mockRegions);
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

    // Verify loading state is shown
    expect(screen.getByText('Loading...')).toBeInTheDocument();

    // Wait for loading to finish
    await waitForElementToBeRemoved(() => screen.queryByText('Loading...'));

    // Verify content is loaded
    expect(screen.getByText('Admin Dashboard')).toBeInTheDocument();
  });

  it('renders admin dashboard with all sections', async () => {
    await renderAdmin();
    await waitForElementToBeRemoved(() => screen.queryByText('Loading...'));
    expect(screen.getByText('Admin Dashboard')).toBeInTheDocument();
    expect(screen.getByText('Regions')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add region/i })).toBeInTheDocument();
  });

  it('toggles admin status for a user', async () => {
    (users.list as jest.Mock).mockResolvedValueOnce(mockUsers);
    (users.update as jest.Mock).mockResolvedValueOnce({ ...mockUsers[0], is_admin: true });

    await act(async () => {
      await renderAdmin();
    });

    // Wait for loading to complete
    await waitForElementToBeRemoved(() => screen.queryByText('Loading...'));

    // Find the specific user row by its email in the first column
    const userRow = screen.getAllByRole('cell', { name: 'user@example.com' })[0];
    expect(userRow).toBeInTheDocument();

    // Find and click the toggle button for the specific user
    const toggleButton = screen.getByRole('switch', { name: `Toggle admin status for user@example.com` });
    await act(async () => {
      await userEvent.click(toggleButton);
    });

    expect(users.update).toHaveBeenCalledWith(1, { is_admin: true });
  });

  it('creates a new region', async () => {
    (auth.me as jest.Mock).mockResolvedValueOnce(mockUser);
    (regions.list as jest.Mock).mockResolvedValueOnce([]);
    (regions.create as jest.Mock).mockResolvedValueOnce({});

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Admin />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Wait for loading to complete
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
    });

    // Click add region button
    const addButton = screen.getByRole('button', { name: /add region/i });
    await act(async () => {
      await userEvent.click(addButton);
    });

    // Fill in the form
    await act(async () => {
      await userEvent.type(screen.getByLabelText('Region name'), 'test-region');
      await userEvent.type(screen.getByLabelText('Postgres host'), 'localhost');
      await userEvent.clear(screen.getByLabelText('Postgres port'));
      await userEvent.type(screen.getByLabelText('Postgres port'), '5432');
      await userEvent.type(screen.getByLabelText('Postgres admin user'), 'admin');
      await userEvent.type(screen.getByLabelText('Postgres admin password'), 'password');
      await userEvent.type(screen.getByLabelText('LiteLLM API URL'), 'http://localhost:8000');
      await userEvent.type(screen.getByLabelText('LiteLLM API key'), 'test-key');
    });

    // Submit the form
    const createButton = screen.getByRole('button', { name: /create region/i });
    await act(async () => {
      await userEvent.click(createButton);
    });

    // Wait for the mutation to be called
    await waitFor(() => {
      expect(regions.create).toHaveBeenCalledWith({
        name: 'test-region',
        postgres_host: 'localhost',
        postgres_port: 5432,
        postgres_admin_user: 'admin',
        postgres_admin_password: 'password',
        litellm_api_url: 'http://localhost:8000',
        litellm_api_key: 'test-key',
      });
    });
  });

  it('deletes a region after confirmation', async () => {
    (regions.delete as jest.Mock).mockResolvedValueOnce({});
    window.confirm = jest.fn(() => true);
    await renderAdmin();

    // Wait for content to load
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: /delete region us-east-1/i })).toBeInTheDocument();

    // Find and click delete button
    const deleteButton = screen.getByRole('button', { name: /delete region us-east-1/i });
    await userEvent.click(deleteButton);

    // Confirm deletion
    expect(window.confirm).toHaveBeenCalledWith('Are you sure you want to delete this region?');

    // Verify API call
    await waitFor(() => {
      expect(regions.delete).toHaveBeenCalledWith(1);
    });
  });

  it('deletes a private AI key after confirmation', async () => {
    // Mock window.confirm to return true
    window.confirm = jest.fn(() => true);

    // Mock the delete function
    (privateAIKeys.delete as jest.Mock).mockResolvedValueOnce({ data: { message: 'Key deleted successfully' } });

    // Render the component using the helper function
    await act(async () => {
      await renderAdmin();
    });

    // Wait for loading to complete
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
    });

    // Find and click delete button for the private AI key
    const deleteButton = screen.getByRole('button', { name: /delete private ai key db1/i });
    await act(async () => {
      await userEvent.click(deleteButton);
    });

    // Confirm deletion
    expect(window.confirm).toHaveBeenCalledWith('Are you sure you want to delete this private AI key?');
    await waitFor(() => {
      expect(privateAIKeys.delete).toHaveBeenCalledWith('db1');
    });
  });

  it('shows error message when delete operation fails', async () => {
    const errorMessage = 'Failed to delete region';
    (auth.me as jest.Mock).mockResolvedValueOnce(mockUser);
    (regions.list as jest.Mock).mockResolvedValueOnce(mockRegions);
    (regions.delete as jest.Mock).mockRejectedValueOnce({ response: { data: { detail: errorMessage } } });

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Admin />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Wait for loading to complete
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
    });

    // Mock window.confirm to return true
    window.confirm = jest.fn(() => true);

    // Click delete button
    const deleteButton = screen.getByRole('button', { name: /delete region us-east-1/i });
    await act(async () => {
      await userEvent.click(deleteButton);
    });

    // Wait for error message
    await waitFor(() => {
      expect(screen.getByTestId('error-message')).toHaveTextContent(errorMessage);
    });
  });

  it('should show error when trying to delete a region with active private AI keys', async () => {
    const errorMessage = 'Cannot delete region with active private AI keys';
    (auth.me as jest.Mock).mockResolvedValueOnce(mockUser);
    (regions.list as jest.Mock).mockResolvedValueOnce(mockRegions);
    (regions.delete as jest.Mock).mockRejectedValueOnce({ response: { data: { detail: errorMessage } } });

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Admin />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Wait for loading to complete
    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
    });

    // Mock window.confirm to return true
    window.confirm = jest.fn(() => true);

    // Click delete button
    const deleteButton = screen.getByRole('button', { name: /delete region us-east-1/i });
    await act(async () => {
      await userEvent.click(deleteButton);
    });

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
    await userEvent.click(addButton);

    // Now fill in the form fields
    await userEvent.type(screen.getByLabelText('Region name'), 'new-region');
    await userEvent.type(screen.getByLabelText('Postgres host'), 'localhost');

    // Set postgres port
    const portInput = screen.getByLabelText('Postgres port');
    await userEvent.clear(portInput);
    await userEvent.type(portInput, '5432', { delay: 0 });

    await userEvent.type(screen.getByLabelText('Postgres admin user'), 'admin');
    await userEvent.type(screen.getByLabelText('Postgres admin password'), 'password');
    await userEvent.type(screen.getByLabelText('LiteLLM API URL'), 'http://localhost:8000');
    await userEvent.type(screen.getByLabelText('LiteLLM API key'), 'test-key');

    // Submit the form
    const createButton = await screen.findByRole('button', { name: /create region/i });
    await userEvent.click(createButton);

    // Wait for the mutation to complete
    await waitFor(() => {
      expect(regions.create).toHaveBeenCalledWith(expect.objectContaining({
        name: 'new-region',
        postgres_host: 'localhost',
        postgres_port: 5432,
        postgres_admin_user: 'admin',
        postgres_admin_password: 'password',
        litellm_api_url: 'http://localhost:8000',
        litellm_api_key: 'test-key'
      }));
    });
  });
});