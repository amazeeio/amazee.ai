'use client';

import { useEffect, useState } from 'react';
import { useAuth } from '@/hooks/use-auth';
import { get, post } from '@/utils/api';
import Script from 'next/script';

declare module 'react' {
  interface HTMLAttributes<T> extends AriaAttributes, DOMAttributes<T> {
    'pricing-table-id'?: string;
    'publishable-key'?: string;
    'customer-session-client-secret'?: string;
  }
}

declare module 'react/jsx-runtime' {
  interface Element {
    'stripe-pricing-table': any;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'stripe-pricing-table': HTMLElement;
  }
}

export default function PricingPage() {
  const { user } = useAuth();
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchSessionToken = async () => {
      try {
        if (!user?.team_id) return;
        const response = await get(`/billing/teams/${user.team_id}/pricing-table-session`);
        const data = await response.json();
        setClientSecret(data.client_secret);
      } catch (err) {
        setError('Failed to load pricing table. Please try again later.');
        console.error('Error fetching pricing table session:', err);
      }
    };

    fetchSessionToken();
  }, [user?.team_id]);

  const handleManageSubscription = async () => {
    try {
      const response = await post(`/billing/teams/${user?.team_id}/portal`, {});
      if (response.redirected) {
        window.location.href = response.url;
      }
    } catch (error) {
      console.error('Error accessing portal:', error);
    }
  };

  if (error) {
    return <div className="text-red-500">{error}</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-semibold">Subscription Plans</h2>
        <button
          onClick={handleManageSubscription}
          className="text-blue-600 hover:text-blue-800"
        >
          Manage Subscription
        </button>
      </div>
      <Script src="https://js.stripe.com/v3/pricing-table.js" strategy="afterInteractive" />
      {clientSecret && (
        // @ts-ignore
        <stripe-pricing-table
          pricing-table-id="prctbl_1RRqUhPszKsC9PNiI6av2bXK"
          publishable-key="pk_test_51RRqG1PszKsC9PNicexnqtXn94fTB1MQXbGxApaEojDe81ZtouhTXDzN8Jgg44DBiHvMjGA5aQSvTZ1Q4N4uLl9i00rhEbJpHm"
          customer-session-client-secret={clientSecret}
        />
      )}
    </div>
  );
}