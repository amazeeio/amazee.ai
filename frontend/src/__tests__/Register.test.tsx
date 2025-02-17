import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import { Register } from '../pages/Register';
import { auth } from '../api/client';
import { act } from 'react-dom/test-utils';

// Mock the auth API
jest.mock('../api/client', () => ({
  auth: {
    register: jest.fn(),
  },
}));

// Mock useNavigate
const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}));

const renderRegister = () => {
  return render(
    <BrowserRouter>
      <Register />
    </BrowserRouter>
  );
};

describe('Register Component', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    window.history.pushState({}, '', '/');
  });

  it('renders registration form', () => {
    renderRegister();

    expect(screen.getByText('Create a new account')).toBeInTheDocument();
    expect(screen.getByLabelText(/email address/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create account/i })).toBeInTheDocument();
    expect(screen.getByText(/sign in to your account/i)).toBeInTheDocument();
  });

  it('submits registration form successfully', async () => {
    (auth.register as jest.Mock).mockResolvedValueOnce({});
    renderRegister();

    const emailInput = screen.getByLabelText(/email address/i);
    const passwordInput = screen.getByLabelText(/password/i);
    const submitButton = screen.getByRole('button', { name: /create account/i });

    await act(async () => {
      await userEvent.type(emailInput, 'test@example.com');
      await userEvent.type(passwordInput, 'password123');
      await userEvent.click(submitButton);
    });

    await waitFor(() => {
      expect(auth.register).toHaveBeenCalledWith({
        email: 'test@example.com',
        password: 'password123'
      });
    });

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/login');
    });
  });

  it('shows loading state while submitting', async () => {
    (auth.register as jest.Mock).mockImplementation(() => new Promise(resolve => setTimeout(resolve, 100)));
    renderRegister();

    const emailInput = screen.getByLabelText(/email address/i);
    const passwordInput = screen.getByLabelText(/password/i);
    const submitButton = screen.getByRole('button', { name: /create account/i });

    await act(async () => {
      await userEvent.type(emailInput, 'test@example.com');
      await userEvent.type(passwordInput, 'password123');
      await userEvent.click(submitButton);
    });

    expect(screen.getByText('Creating account...')).toBeInTheDocument();
  });

  it('displays error message on registration failure', async () => {
    const errorMessage = 'Email already exists';
    (auth.register as jest.Mock).mockRejectedValueOnce({
      response: {
        data: {
          detail: errorMessage
        }
      }
    });

    renderRegister();

    const emailInput = screen.getByLabelText(/email address/i);
    const passwordInput = screen.getByLabelText(/password/i);
    const submitButton = screen.getByRole('button', { name: /create account/i });

    await act(async () => {
      await userEvent.type(emailInput, 'test@example.com');
      await userEvent.type(passwordInput, 'password123');
      await userEvent.click(submitButton);
    });

    await waitFor(() => {
      expect(screen.getByText(errorMessage)).toBeInTheDocument();
    });
  });

  it('requires email and password fields', async () => {
    renderRegister();

    const submitButton = screen.getByRole('button', { name: /create account/i });
    const emailInput = screen.getByLabelText(/email address/i);
    const passwordInput = screen.getByLabelText(/password/i);

    // Try to submit without filling in the fields
    await act(async () => {
      await userEvent.click(submitButton);
    });

    // Check that the inputs show validation messages
    expect(emailInput).toBeInvalid();
    expect(passwordInput).toBeInvalid();
  });

  it('navigates to login page when clicking sign in link', async () => {
    renderRegister();

    const signInLink = screen.getByText(/sign in to your account/i);
    await act(async () => {
      await userEvent.click(signInLink);
    });

    expect(window.location.pathname).toBe('/login');
  });
});