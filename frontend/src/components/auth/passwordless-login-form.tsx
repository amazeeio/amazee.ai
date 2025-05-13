'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import * as z from 'zod';

import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useToast } from '@/hooks/use-toast';
import { useAuth } from '@/hooks/use-auth';
import { get } from '@/utils/api';
import { getCachedConfig } from '@/utils/config';

const emailFormSchema = z.object({
  email: z.string().email('Invalid email address'),
});

const verificationFormSchema = z.object({
  verificationCode: z.string().min(1, 'Verification code is required'),
});

interface PasswordlessLoginFormProps {
  onSwitchToPassword: () => void;
}

export function PasswordlessLoginForm({ onSwitchToPassword }: PasswordlessLoginFormProps) {
  const router = useRouter();
  const { toast } = useToast();
  const { setUser } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [emailSent, setEmailSent] = useState(false);
  const [email, setEmail] = useState('');
  const [codeChars, setCodeChars] = useState(Array(8).fill(''));
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);

  const emailForm = useForm<z.infer<typeof emailFormSchema>>({
    resolver: zodResolver(emailFormSchema),
    defaultValues: {
      email: '',
    },
  });

  const verificationForm = useForm<z.infer<typeof verificationFormSchema>>({
    resolver: zodResolver(verificationFormSchema),
    defaultValues: {
      verificationCode: '',
    },
  });

  // Reset verification form when email is sent
  useEffect(() => {
    if (emailSent) {
      verificationForm.reset();
    }
  }, [emailSent, verificationForm]);

  // Handler for code input change
  const handleCodeChange = (index: number, value: string) => {
    if (!/^[a-zA-Z0-9]?$/.test(value)) return; // Only allow single alphanumeric char
    const newChars = [...codeChars];
    newChars[index] = value;
    setCodeChars(newChars);
    if (value && index < 7) {
      inputRefs.current[index + 1]?.focus();
    }
  };

  // Handler for backspace
  const handleCodeKeyDown = (index: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace' && !codeChars[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  };

  async function onEmailSubmit(data: z.infer<typeof emailFormSchema>) {
    try {
      setIsLoading(true);
      setError(null);

      const { NEXT_PUBLIC_API_URL: apiUrl } = getCachedConfig();
      const response = await fetch(`${apiUrl}/auth/validate-email`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email: data.email }),
      });

      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.detail || 'Failed to send verification code');
      }

      setEmail(data.email);
      setEmailSent(true);
      verificationForm.reset({ verificationCode: '' });
      toast({
        title: 'Verification code sent',
        description: 'Please check your email for the verification code',
      });
    } catch (error) {
      setError(error instanceof Error ? error.message : 'An error occurred while sending the verification code');
    } finally {
      setIsLoading(false);
    }
  }

  async function onVerificationSubmit({ email, verificationCode }: { email: string, verificationCode: string }) {
    try {
      setIsLoading(true);
      setError(null);

      const { NEXT_PUBLIC_API_URL: apiUrl } = getCachedConfig();
      const response = await fetch(`${apiUrl}/auth/sign-in`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          username: email,
          verification_code: verificationCode,
        }),
        credentials: 'include',
      });

      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.detail || 'Failed to sign in');
      }

      if (result.access_token) {
        // Fetch user profile
        try {
          const profileResponse = await get('/auth/me');
          const profileData = await profileResponse.json();
          setUser(profileData);

          toast({
            title: 'Success',
            description: 'Successfully signed in',
          });

          router.refresh();
          router.push('/private-ai-keys');
        } catch (profileError) {
          console.error('Failed to fetch user profile:', profileError);
          setError('Successfully signed in but failed to fetch user profile. Please refresh the page.');
        }
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : 'An error occurred during sign in');
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="grid gap-6">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!emailSent ? (
        <Form {...emailForm}>
          <form onSubmit={emailForm.handleSubmit(onEmailSubmit)} className="space-y-4">
            <FormField
              control={emailForm.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Email</FormLabel>
                  <FormControl>
                    <Input
                      type="email"
                      placeholder="name@example.com"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <Button
              type="submit"
              className="w-full"
              disabled={isLoading}
            >
              {isLoading ? 'Sending code...' : 'Send verification code'}
            </Button>
          </form>
        </Form>
      ) : (
        <Form {...verificationForm}>
          <form onSubmit={e => { e.preventDefault(); onVerificationSubmit({ email, verificationCode: codeChars.join('') }); }} className="space-y-4" autoComplete="off">
            <FormLabel>Verification Code</FormLabel>
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'center' }}>
              {codeChars.map((char, idx) => (
                <Input
                  key={idx}
                  type="text"
                  inputMode="text"
                  maxLength={1}
                  value={char}
                  onChange={e => handleCodeChange(idx, e.target.value)}
                  onKeyDown={e => handleCodeKeyDown(idx, e)}
                  ref={el => { inputRefs.current[idx] = el; }}
                  style={{ width: '2.5rem', textAlign: 'center', fontSize: '1.5rem' }}
                  autoComplete="off"
                  aria-label={`Verification code character ${idx + 1}`}
                />
              ))}
            </div>
            <FormMessage />
            <Button
              type="submit"
              className="w-full"
              disabled={isLoading || codeChars.some(c => !c)}
            >
              {isLoading ? 'Signing in...' : 'Sign in'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="w-full"
              onClick={() => {
                setEmailSent(false);
                setError(null);
                setCodeChars(Array(8).fill(''));
              }}
            >
              Use a different email
            </Button>
          </form>
        </Form>
      )}

      <div className="relative">
        <div className="absolute inset-0 flex items-center">
          <span className="w-full border-t" />
        </div>
        <div className="relative flex justify-center text-xs uppercase">
          <span className="bg-background px-2 text-muted-foreground">
            Or
          </span>
        </div>
      </div>

      <div className="text-center text-sm">
        <Button
          variant="link"
          className="text-sm"
          onClick={onSwitchToPassword}
        >
          Sign in with password
        </Button>
      </div>
    </div>
  );
}