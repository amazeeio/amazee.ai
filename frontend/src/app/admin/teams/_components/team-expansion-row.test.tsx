import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { TeamExpansionRow } from "./team-expansion-row";

const refreshPoolKeys = vi.fn();

vi.mock("@/hooks/use-teams", () => ({
  useTeams: () => ({
    removeUserFromTeam: vi.fn(),
    isRemovingUser: false,
    refreshPoolKeys,
    isRefreshingPoolKeys: false,
    restoreTeam: vi.fn(),
    isRestoring: false,
    deleteTeam: vi.fn(),
    isDeleting: false,
    resetLimits: vi.fn(),
    isResettingLimits: false,
  }),
}));

let budgetType: "pool" | "periodic" = "pool";

vi.mock("@/utils/api", () => ({
  get: vi.fn(async (url: string) => {
    const body = url.startsWith("/teams/")
      ? {
          id: "1",
          name: "Team One",
          admin_email: "admin@example.com",
          phone: "",
          billing_address: "",
          is_active: true,
          is_always_free: false,
          created_at: "2026-01-01T00:00:00Z",
          budget_type: budgetType,
          users: [],
        }
      : url === "/users"
        ? []
        : [];
    return { json: async () => body };
  }),
}));

function renderRow() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <table>
        <tbody>
          <TeamExpansionRow
            teamId="1"
            isExpanded
            includeDeleted={false}
            regions={[]}
            onEdit={vi.fn()}
            onAddUser={vi.fn()}
            onCreateUser={vi.fn()}
          />
        </tbody>
      </table>
    </QueryClientProvider>,
  );
}

describe("TeamExpansionRow Refresh Pool Keys button", () => {
  it("shows the button for a pool team", async () => {
    budgetType = "pool";
    renderRow();
    await waitFor(() => {
      expect(screen.getByText("Team One")).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: /Refresh Pool Keys/ }),
    ).toBeInTheDocument();
  });

  it("hides the button for a periodic team", async () => {
    budgetType = "periodic";
    renderRow();
    await waitFor(() => {
      expect(screen.getByText("Team One")).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /Refresh Pool Keys/ }),
    ).not.toBeInTheDocument();
  });
});
