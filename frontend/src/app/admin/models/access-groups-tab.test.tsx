import { expect, it, describe, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import AccessGroupsTab from "./access-groups-tab";

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

const regions = [
  { id: 1, name: "us-east-1" },
  { id: 2, name: "us-west-2" },
];

describe("AccessGroupsTab", () => {
  it("renders groups with deployment, default badges and team counts", async () => {
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AccessGroupsTab regions={regions} />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Default ZDR Models")).toBeInTheDocument();
    });

    expect(screen.getByText("default-zdr")).toBeInTheDocument();
    expect(screen.getByText("Extended Data Retention Models")).toBeInTheDocument();
    // default-zdr is the default in region 1 -> star badge with region name
    const usEast = screen.getAllByText("us-east-1");
    expect(usEast.length).toBeGreaterThanOrEqual(2); // deployed badge + default badge
    // team_count of the extended group
    expect(screen.getByText("3")).toBeInTheDocument();
  });
});
