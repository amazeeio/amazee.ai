import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

const NEW_DASHBOARD_URL = process.env.NEXT_PUBLIC_NEW_DASHBOARD_URL;

export default function Home() {
  return (
    <div className="container relative h-screen flex-col items-center justify-center grid lg:max-w-none lg:grid-cols-2 lg:px-0">
      {/* Left panel */}
      <div className="relative hidden h-full flex-col bg-muted p-10 text-white lg:flex dark:border-r">
        <div className="absolute inset-0 bg-zinc-900" />
        <div className="relative z-20 flex items-center text-lg font-medium">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="mr-2 h-6 w-6"
          >
            <path d="M15 6v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3" />
          </svg>
          amazee.ai
        </div>
        <div className="relative z-20 mt-auto">
          <blockquote className="space-y-2">
            <p className="text-lg">
              &ldquo;Our new dashboard brings a faster, more powerful experience
              — built from the ground up to serve you better.&rdquo;
            </p>
          </blockquote>
        </div>
      </div>

      {/* Right panel */}
      <div className="lg:p-8">
        <div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-[420px]">
          <div className="flex flex-col space-y-2 text-center">
            <h1 className="text-2xl font-semibold tracking-tight">
              We&apos;ve moved! 🎉
            </h1>
            <p className="text-sm text-muted-foreground">
              The amazee.ai dashboard has a new home. Please use the link below
              to access the new dashboard and manage your API keys and teams.
            </p>
          </div>

          {NEW_DASHBOARD_URL ? (
            <Button asChild size="lg" className="w-full">
              <a href={NEW_DASHBOARD_URL} rel="noopener noreferrer">
                Go to the new dashboard
                <ArrowRight className="ml-2 h-4 w-4" />
              </a>
            </Button>
          ) : (
            <p className="text-sm text-center text-destructive">
              New dashboard URL is not configured.
            </p>
          )}

          <p className="text-xs text-center text-muted-foreground">
            If you have any issues, please contact{" "}
            <a
              href="mailto:support@amazee.io"
              className="underline underline-offset-4 hover:text-primary"
            >
              support@amazee.io
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
