import { expect, it, describe, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ModelsPage from "./page";

// Mock sidebar layout
vi.mock("@/components/sidebar-layout", () => ({
  SidebarLayout: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

// Mock useToast
vi.mock("@/hooks/use-toast", () => ({
  useToast: () => ({
    toast: vi.fn(),
  }),
}));

const createTestQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

describe("ModelsPage Integration", () => {
  it("renders the model catalog list", async () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ModelsPage />
      </QueryClientProvider>,
    );

    // Wait for the models to load
    await waitFor(() => {
      expect(screen.getByText("Llama 3 70B")).toBeInTheDocument();
    });

    expect(screen.getByText("GPT-4o Mini")).toBeInTheDocument();
    expect(screen.getByText("meta-llama/llama-3-70b-instruct")).toBeInTheDocument();
    expect(screen.getByText("openai/gpt-4o-mini")).toBeInTheDocument();
  });

  it("filters models by search term", async () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ModelsPage />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Llama 3 70B")).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText("Search by ID or Display Name...");
    fireEvent.change(searchInput, { target: { value: "gpt" } });

    // Llama should be hidden, GPT should still be visible
    expect(screen.queryByText("Llama 3 70B")).not.toBeInTheDocument();
    expect(screen.getByText("GPT-4o Mini")).toBeInTheDocument();
  });

  it("filters models by provider button", async () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ModelsPage />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Llama 3 70B")).toBeInTheDocument();
    });

    // Select the "openai" provider button
    const openaiButton = screen.getByRole("button", { name: "openai" });
    fireEvent.click(openaiButton);

    expect(screen.queryByText("Llama 3 70B")).not.toBeInTheDocument();
    expect(screen.getByText("GPT-4o Mini")).toBeInTheDocument();
  });

  it("switches to regions matrix tab", async () => {
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ModelsPage />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Llama 3 70B")).toBeInTheDocument();
    });

    const matrixTab = screen.getByRole("tab", { name: /regions matrix/i });
    await user.click(matrixTab);

    // Verify headers for the matrix table are rendered asynchronously
    await waitFor(() => {
      expect(screen.getByText("Model (Inventory)")).toBeInTheDocument();
      expect(screen.getByText("us-east-1")).toBeInTheDocument();
      expect(screen.getByText("us-west-2")).toBeInTheDocument();
    });
  });
});
