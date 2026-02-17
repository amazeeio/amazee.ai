import { describe, it, expect, vi, beforeEach } from "vitest";
import { User } from "@/types/user";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { UserFilter } from "./user-filter";

const createTestQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

const mockUsers: User[] = [
  {
    id: 1,
    email: "test@example.com",
    role: "user",
    team_id: 1,
    is_active: true,
    created_at: "2024-01-01T00:00:00Z",
  },
  {
    id: 2,
    email: "admin@example.com",
    role: "admin",
    team_id: 1,
    is_active: true,
    created_at: "2024-01-01T00:00:00Z",
  },
  {
    id: 3,
    email: "john@example.com",
    role: "user",
    team_id: 1,
    is_active: true,
    created_at: "2024-01-01T00:00:00Z",
  },
];

vi.mock("@/hooks/use-toast", () => ({
  useToast: () => ({
    toast: vi.fn(),
  }),
}));

describe("UserFilter", () => {
  const mockOnUserSelect = vi.fn();

  beforeEach(() => {
    mockOnUserSelect.mockClear();
  });

  const renderWithQuery = (component: React.ReactElement) => {
    const queryClient = createTestQueryClient();
    return render(
      <QueryClientProvider client={queryClient}>
        {component}
      </QueryClientProvider>,
    );
  };

  it("renders with placeholder when no user selected", () => {
    renderWithQuery(
      <UserFilter selectedUser={null} onUserSelect={mockOnUserSelect} />,
    );

    expect(screen.getByText("Filter by owner...")).toBeInTheDocument();
  });

  it("renders selected user email", () => {
    const selectedUser: User = mockUsers[0];

    renderWithQuery(
      <UserFilter
        selectedUser={selectedUser}
        onUserSelect={mockOnUserSelect}
      />,
    );

    expect(screen.getByText("test@example.com")).toBeInTheDocument();
  });

  it("opens popover when clicked", async () => {
    renderWithQuery(
      <UserFilter selectedUser={null} onUserSelect={mockOnUserSelect} />,
    );

    const button = screen.getByText("Filter by owner...");
    fireEvent.click(button);

    await waitFor(() => {
      expect(
        screen.getByPlaceholderText("Search users..."),
      ).toBeInTheDocument();
    });
  });

  it("shows clear filter button when user is selected", () => {
    const selectedUser: User = mockUsers[0];

    renderWithQuery(
      <UserFilter
        selectedUser={selectedUser}
        onUserSelect={mockOnUserSelect}
      />,
    );

    expect(screen.getByText("Clear filter")).toBeInTheDocument();
  });

  it("calls onUserSelect with null when clear filter is clicked", () => {
    const selectedUser: User = mockUsers[0];

    renderWithQuery(
      <UserFilter
        selectedUser={selectedUser}
        onUserSelect={mockOnUserSelect}
      />,
    );

    const clearButton = screen.getByText("Clear filter");
    fireEvent.click(clearButton);

    expect(mockOnUserSelect).toHaveBeenCalledWith(null);
  });
});
