'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import Script from 'next/script';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2, AlertCircle } from 'lucide-react';
import { getWithToken } from '@/utils/api';
import { getConfig } from '@/utils/config';

declare module 'react' {
  interface HTMLAttributes<T> extends AriaAttributes, DOMAttributes<T> {
    'pricing-table-id'?: string;
    'publishable-key'?: string;
    'customer-session-client-secret'?: string;
  }
}

declare module 'react/jsx-runtime' {
  interface Element {
    'stripe-pricing-table': HTMLElement;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'stripe-pricing-table': HTMLElement;
  }
}

interface User {
  id: number;
  email: string;
  is_active: boolean;
  is_admin: boolean;
  team_id: number | null;
  role: string | null;
}

interface PricingTable {
  pricing_table_id: string;
  updated_at: string;
}

interface PricingTableSession {
  client_secret: string;
}

export default function PricingTokenPage() {
  const searchParams = useSearchParams();
  const token = searchParams?.get('token');
  
  const [user, setUser] = useState<User | null>(null);
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isValidatingToken, setIsValidatingToken] = useState(true);

  // Fetch config using React Query
  const { data: config, isLoading: isLoadingConfig } = useQuery({
    queryKey: ['config'],
    queryFn: getConfig,
  });

  // Fetch pricing table ID
  const { data: pricingTable, error: pricingTableError } = useQuery<PricingTable>({
    queryKey: ['pricing-table', token],
    queryFn: async () => {
      if (!token) throw new Error('No token provided');
      const response = await getWithToken('/pricing-tables', token);
      return response.json();
    },
    enabled: !!token && !!user,
  });

  // Validate JWT token and get user data
  useEffect(() => {
    const validateToken = async () => {
      if (!token) {
        setError('No access token provided in URL. Please include ?token=your_jwt_token');
        setIsValidatingToken(false);
        return;
      }

      try {
        setIsValidatingToken(true);
        setError(null);
        
        // Validate the JWT token using the validate-jwt endpoint
        const response = await getWithToken('/auth/validate-jwt', token);
        
        if (!response.ok) {
          throw new Error('Invalid or expired token');
        }

        // The validate-jwt endpoint returns a new token, but we need user data
        // So we'll call the /auth/me endpoint to get user info
        const userResponse = await getWithToken('/auth/me', token);
        const userData = await userResponse.json();
        
        setUser(userData);
      } catch (err) {
        console.error('Error validating token:', err);
        setError(err instanceof Error ? err.message : 'Failed to validate token');
      } finally {
        setIsValidatingToken(false);
      }
    };

    validateToken();
  }, [token]);

  // Fetch pricing table session token
  useEffect(() => {
    const fetchSessionToken = async () => {
      if (!user?.team_id || !token) return;
      
      try {
        const response = await getWithToken(
          `/billing/teams/${user.team_id}/pricing-table-session`,
          token
        );
        const data: PricingTableSession = await response.json();
        setClientSecret(data.client_secret);
      } catch (err) {
        console.error('Error fetching pricing table session:', err);
        setError('Failed to load pricing table. Please try again later.');
      }
    };

    fetchSessionToken();
  }, [user?.team_id, token]);

  // Loading state
  if (isValidatingToken || isLoadingConfig) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <Card className="w-full max-w-md">
          <CardContent className="pt-6">
            <div className="flex items-center justify-center space-x-2">
              <Loader2 className="h-6 w-6 animate-spin" />
              <span>Validating access token...</span>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  // Error state
  if (error || pricingTableError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <Card className="w-full max-w-md">
          <CardContent className="pt-6">
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>
                {error || 'Failed to load pricing table. Please try again later.'}
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      </div>
    );
  }

  // Configuration missing state
  if (!config?.STRIPE_PUBLISHABLE_KEY) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <Card className="w-full max-w-md">
          <CardContent className="pt-6">
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>
                Stripe configuration is missing. Please contact support.
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      </div>
    );
  }

  // Success state - show pricing table
  return (
    <div className="min-h-screen bg-gray-50 py-8">
      <div className="max-w-4xl mx-auto px-4">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">
            Subscription Plans
          </h1>
          <p className="text-gray-600">
            Welcome, {user?.email}. Choose a plan that works for your team.
          </p>
        </div>

        <Script src="https://js.stripe.com/v3/pricing-table.js" strategy="afterInteractive" />
        
        {clientSecret && pricingTable ? (
          <div className="bg-white rounded-lg shadow-sm p-2">
            {/* @ts-expect-error - Stripe pricing table is a custom element */}
            <stripe-pricing-table
              pricing-table-id={pricingTable.pricing_table_id}
              publishable-key={config.STRIPE_PUBLISHABLE_KEY}
              customer-session-client-secret={clientSecret}
            />
          </div>
        ) : (
          <Card>
            <CardContent className="pt-6">
              <div className="flex items-center justify-center space-x-2">
                <Loader2 className="h-6 w-6 animate-spin" />
                <span>Loading pricing information...</span>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}