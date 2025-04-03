'use client';

import { useState, FormEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/hooks/use-toast';
import { Loader2 } from 'lucide-react';
import { post } from '@/utils/api';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

interface OpenMeterResponse {
  data: Record<string, unknown>;
  status: number;
}

export default function OpenMeterPage() {
  const { toast } = useToast();
  const [endpoint, setEndpoint] = useState('');
  const [requestBody, setRequestBody] = useState('');
  const [method, setMethod] = useState('GET');
  const [response, setResponse] = useState<OpenMeterResponse | null>(null);

  const openMeterMutation = useMutation({
    mutationFn: async () => {
      const data = requestBody ? JSON.parse(requestBody) : {};
      const response = await post('/api/metering/passthrough', {
        endpoint,
        method,
        data,
      });
      return response.json();
    },
    onSuccess: (data) => {
      setResponse(data);
      toast({
        title: 'Success',
        description: 'OpenMeter request completed successfully',
      });
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    openMeterMutation.mutate();
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">OpenMeter Passthrough</h1>
        <p className="text-muted-foreground">
          Make passthrough requests to the OpenMeter API. See the{' '}
          <a
            href="https://openmeter.io/docs/api/cloud"
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            OpenMeter API documentation
          </a>{' '}
          for available endpoints and methods.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>OpenMeter Request</CardTitle>
          <CardDescription>
            Enter the OpenMeter endpoint and request body (optional)
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="method">Method</Label>
              <Select value={method} onValueChange={setMethod}>
                <SelectTrigger>
                  <SelectValue placeholder="Select method" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="GET">GET</SelectItem>
                  <SelectItem value="POST">POST</SelectItem>
                  <SelectItem value="PUT">PUT</SelectItem>
                  <SelectItem value="DELETE">DELETE</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="endpoint">Endpoint</Label>
              <Input
                id="endpoint"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                placeholder="e.g. /api/v1/meters"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="requestBody">Request Body (JSON)</Label>
              <Textarea
                id="requestBody"
                value={requestBody}
                onChange={(e) => setRequestBody(e.target.value)}
                placeholder="Enter JSON request body (optional)"
                className="min-h-[200px] font-mono"
              />
            </div>
            <Button type="submit" disabled={openMeterMutation.isPending}>
              {openMeterMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Send Request
            </Button>
          </form>
        </CardContent>
      </Card>

      {response && (
        <Card>
          <CardHeader>
            <CardTitle>Response</CardTitle>
            <CardDescription>OpenMeter API Response</CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="bg-muted p-4 rounded-md overflow-auto">
              {JSON.stringify(response, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}