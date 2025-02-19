import React, { useEffect, useState, useCallback } from 'react';
import { format, parseISO } from 'date-fns';
import { formatInTimeZone, toZonedTime } from 'date-fns-tz';
import { AuditLog, AuditLogFilters, getAuditLogs, users, User } from '../api/client';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { Header } from '../components/Header';
import { useNavigate } from 'react-router-dom';
import { auth } from '../api/client';
import debounce from 'lodash/debounce';

const ITEMS_PER_PAGE = 20;
const userTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

const AuditLogs: React.FC = () => {
    const navigate = useNavigate();
    const [logs, setLogs] = useState<AuditLog[]>([]);
    const [loading, setLoading] = useState(true);
    const [filters, setFilters] = useState<AuditLogFilters>({
        skip: 0,
        limit: ITEMS_PER_PAGE,
    });
    const [uniqueEventTypes, setUniqueEventTypes] = useState<string[]>([]);
    const [uniqueResourceTypes, setUniqueResourceTypes] = useState<string[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [emailSearchTerm, setEmailSearchTerm] = useState('');
    const [emailSearchResults, setEmailSearchResults] = useState<User[]>([]);
    const [isEmailDropdownOpen, setIsEmailDropdownOpen] = useState(false);
    const [emailSearchLoading, setEmailSearchLoading] = useState(false);

    // Debounced search function
    const debouncedSearchUsers = useCallback(
        debounce(async (searchTerm: string) => {
            if (!searchTerm) {
                setEmailSearchResults([]);
                setEmailSearchLoading(false);
                return;
            }
            try {
                const results = await users.search(searchTerm);
                setEmailSearchResults(results);
            } catch (error) {
                console.error('Error searching users:', error);
                setEmailSearchResults([]);
            }
            setEmailSearchLoading(false);
        }, 300),
        []
    );

    // Handle email search input change
    const handleEmailSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const value = e.target.value;
        setEmailSearchTerm(value);
        if (!value) {
            // Clear the email filter when input is empty
            handleFilterChange('user_email', null);
            setEmailSearchResults([]);
            setEmailSearchLoading(false);
            setIsEmailDropdownOpen(false);
            return;
        }
        setEmailSearchLoading(true);
        setIsEmailDropdownOpen(true);
        debouncedSearchUsers(value);
    };

    // Handle email selection
    const handleEmailSelect = (email: string) => {
        setEmailSearchTerm(email);
        setIsEmailDropdownOpen(false);
        handleFilterChange('user_email', email);
    };

    // Add a clear button for email filter
    const clearEmailFilter = () => {
        setEmailSearchTerm('');
        handleFilterChange('user_email', null);
        setEmailSearchResults([]);
        setIsEmailDropdownOpen(false);
    };

    // Close dropdown when clicking outside
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            const target = event.target as HTMLElement;
            if (!target.closest('#email-search-container')) {
                setIsEmailDropdownOpen(false);
            }
        };

        document.addEventListener('click', handleClickOutside);
        return () => document.removeEventListener('click', handleClickOutside);
    }, []);

    useEffect(() => {
        // Check if user is admin
        const checkAdmin = async () => {
            try {
                const user = await auth.me();
                if (!user.is_admin) {
                    navigate('/');
                    return;
                }
            } catch (error) {
                console.error('Error checking admin status:', error);
                navigate('/');
                return;
            }
        };
        checkAdmin();
    }, [navigate]);

    const fetchLogs = async () => {
        try {
            setLoading(true);
            setError(null);
            console.log('Fetching audit logs with filters:', filters);
            const data = await getAuditLogs(filters);
            console.log('Received audit logs:', data);

            if (!Array.isArray(data)) {
                throw new Error('Invalid response format from server');
            }

            setLogs(data);

            // Update unique values for filters
            const eventTypes = [...new Set(data.map(log => log.event_type))];
            const resourceTypes = [...new Set(data.map(log => log.resource_type))];
            console.log('Unique event types:', eventTypes);
            console.log('Unique resource types:', resourceTypes);
            setUniqueEventTypes(eventTypes);
            setUniqueResourceTypes(resourceTypes);
        } catch (error) {
            console.error('Error fetching audit logs:', error);
            if (error instanceof Error) {
                if (error.message.includes('403')) {
                    setError('You do not have permission to view audit logs');
                    navigate('/');
                } else {
                    setError(error.message);
                }
            } else {
                setError('Failed to fetch audit logs');
            }
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchLogs();
    }, [filters]);

    const handleFilterChange = (key: keyof AuditLogFilters, value: string | number | null) => {
        setFilters(prev => ({
            ...prev,
            [key]: value === '' ? undefined : value,
            skip: 0, // Reset pagination when filters change
        }));
    };

    const handleNextPage = () => {
        setFilters(prev => ({
            ...prev,
            skip: (prev.skip || 0) + ITEMS_PER_PAGE,
        }));
    };

    const handlePrevPage = () => {
        setFilters(prev => ({
            ...prev,
            skip: Math.max((prev.skip || 0) - ITEMS_PER_PAGE, 0),
        }));
    };

    const getStatusBadge = (status: number) => {
        const color = status < 300 ? 'bg-green-100 text-green-800' :
                     status < 400 ? 'bg-blue-100 text-blue-800' :
                     status < 500 ? 'bg-orange-100 text-orange-800' :
                     'bg-red-100 text-red-800';
        return (
            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${color}`}>
                {status}
            </span>
        );
    };

    if (loading) {
        return (
            <>
                <Header />
                <LoadingSpinner fullScreen />
            </>
        );
    }

    return (
        <div className="min-h-screen bg-gray-100">
            <Header />
            <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
                <div className="px-4 py-6 sm:px-0">
                    {error && (
                        <div className="mb-4 bg-red-50 border border-red-200 text-red-800 rounded-md p-4">
                            <div className="flex">
                                <div className="ml-3">
                                    <p className="text-sm">{error}</p>
                                </div>
                                <div className="ml-auto pl-3">
                                    <button
                                        onClick={() => setError(null)}
                                        className="inline-flex rounded-md p-1.5 text-red-500 hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500"
                                    >
                                        <span className="sr-only">Dismiss</span>
                                        <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                            <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                                        </svg>
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}

                    <div className="bg-white shadow overflow-hidden sm:rounded-lg">
                        <div className="px-4 py-5 sm:p-6">
                            <h3 className="text-lg font-medium text-gray-900 mb-4">Audit Logs</h3>

                            {/* Filters */}
                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
                                <div>
                                    <label htmlFor="event-type" className="block text-sm font-medium text-gray-700 mb-1">
                                        Event Type
                                    </label>
                                    <select
                                        id="event-type"
                                        value={filters.event_type || ''}
                                        onChange={(e) => handleFilterChange('event_type', e.target.value)}
                                        className="block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm rounded-md"
                                    >
                                        <option value="">All Events</option>
                                        {uniqueEventTypes.map(type => (
                                            <option key={type} value={type}>{type}</option>
                                        ))}
                                    </select>
                                </div>

                                <div>
                                    <label htmlFor="resource-type" className="block text-sm font-medium text-gray-700 mb-1">
                                        Resource Type
                                    </label>
                                    <select
                                        id="resource-type"
                                        value={filters.resource_type || ''}
                                        onChange={(e) => handleFilterChange('resource_type', e.target.value)}
                                        className="block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm rounded-md"
                                    >
                                        <option value="">All Resources</option>
                                        {uniqueResourceTypes.map(type => (
                                            <option key={type} value={type}>{type}</option>
                                        ))}
                                    </select>
                                </div>

                                {/* Email Search with Dropdown */}
                                <div id="email-search-container" className="relative">
                                    <label htmlFor="user-email" className="block text-sm font-medium text-gray-700 mb-1">
                                        User Email
                                    </label>
                                    <div className="relative">
                                        <input
                                            id="user-email"
                                            type="text"
                                            placeholder="Search by email"
                                            value={emailSearchTerm}
                                            onChange={handleEmailSearchChange}
                                            onFocus={() => setIsEmailDropdownOpen(true)}
                                            className="block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm rounded-md"
                                        />
                                        {emailSearchTerm && (
                                            <button
                                                onClick={clearEmailFilter}
                                                className="absolute right-3 top-2 text-gray-400 hover:text-gray-600"
                                                title="Clear email filter"
                                            >
                                                <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                                    <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                                                </svg>
                                            </button>
                                        )}
                                        {emailSearchLoading && !emailSearchTerm && (
                                            <div className="absolute right-3 top-2">
                                                <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-indigo-500"></div>
                                            </div>
                                        )}
                                    </div>
                                    {isEmailDropdownOpen && emailSearchResults.length > 0 && (
                                        <div className="absolute z-10 mt-1 w-full bg-white shadow-lg max-h-60 rounded-md py-1 text-base overflow-auto focus:outline-none sm:text-sm">
                                            {emailSearchResults.map((user) => (
                                                <div
                                                    key={user.id}
                                                    onClick={() => handleEmailSelect(user.email)}
                                                    className="cursor-pointer select-none relative py-2 pl-3 pr-9 hover:bg-indigo-50"
                                                >
                                                    <span className="block truncate">{user.email}</span>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                <div>
                                    <label htmlFor="from-date" className="block text-sm font-medium text-gray-700 mb-1">
                                        From Date
                                    </label>
                                    <input
                                        id="from-date"
                                        type="datetime-local"
                                        value={filters.from_date || ''}
                                        onChange={(e) => handleFilterChange('from_date', e.target.value)}
                                        className="block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm rounded-md"
                                    />
                                </div>

                                <div>
                                    <label htmlFor="to-date" className="block text-sm font-medium text-gray-700 mb-1">
                                        To Date
                                    </label>
                                    <input
                                        id="to-date"
                                        type="datetime-local"
                                        value={filters.to_date || ''}
                                        onChange={(e) => handleFilterChange('to_date', e.target.value)}
                                        className="block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm rounded-md"
                                    />
                                </div>
                            </div>

                            {/* Table */}
                            <div className="mt-4 flex flex-col">
                                <div className="-my-2 overflow-x-auto sm:-mx-6 lg:-mx-8">
                                    <div className="py-2 align-middle inline-block min-w-full sm:px-6 lg:px-8">
                                        <div className="shadow overflow-hidden border-b border-gray-200 sm:rounded-lg">
                                            <table className="min-w-full divide-y divide-gray-200">
                                                <thead className="bg-gray-50">
                                                    <tr>
                                                        <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                            Timestamp
                                                        </th>
                                                        <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                            Event
                                                        </th>
                                                        <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                            Resource
                                                        </th>
                                                        <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                            Action
                                                        </th>
                                                        <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                            Status
                                                        </th>
                                                        <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                            User
                                                        </th>
                                                        <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                            IP Address
                                                        </th>
                                                        <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                            Details
                                                        </th>
                                                    </tr>
                                                </thead>
                                                <tbody className="bg-white divide-y divide-gray-200">
                                                    {logs.map((log) => (
                                                        <tr key={log.id} className="hover:bg-gray-50">
                                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                                                <span title={`UTC: ${format(parseISO(log.timestamp), "yyyy-MM-dd HH:mm:ss 'UTC'")}`}>
                                                                    {formatInTimeZone(
                                                                        new Date(log.timestamp + 'Z'),
                                                                        userTimeZone,
                                                                        'yyyy-MM-dd HH:mm:ss (z)'
                                                                    )}
                                                                </span>
                                                            </td>
                                                            <td className="px-6 py-4 whitespace-nowrap">
                                                                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                                                                    {log.event_type}
                                                                </span>
                                                            </td>
                                                            <td className="px-6 py-4 whitespace-nowrap">
                                                                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-800">
                                                                    {log.resource_type}
                                                                </span>
                                                            </td>
                                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                                                {log.action}
                                                            </td>
                                                            <td className="px-6 py-4 whitespace-nowrap">
                                                                {getStatusBadge(log.details.status_code)}
                                                            </td>
                                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                                                {log.user_email || 'Anonymous'}
                                                            </td>
                                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                                                {log.ip_address || '-'}
                                                            </td>
                                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                                                                <button
                                                                    type="button"
                                                                    className="text-indigo-600 hover:text-indigo-900"
                                                                    title={JSON.stringify(log.details, null, 2)}
                                                                >
                                                                    <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                                                        <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                                                                    </svg>
                                                                </button>
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Pagination */}
                            <div className="flex items-center justify-between mt-6">
                                <div className="flex-1 flex justify-between sm:hidden">
                                    <button
                                        onClick={handlePrevPage}
                                        disabled={!filters.skip || filters.skip === 0}
                                        className="relative inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                                    >
                                        Previous
                                    </button>
                                    <button
                                        onClick={handleNextPage}
                                        disabled={logs.length < ITEMS_PER_PAGE}
                                        className="ml-3 relative inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                                    >
                                        Next
                                    </button>
                                </div>
                                <div className="hidden sm:flex-1 sm:flex sm:items-center sm:justify-between">
                                    <div>
                                        <p className="text-sm text-gray-700">
                                            Page <span className="font-medium">{Math.floor((filters.skip || 0) / ITEMS_PER_PAGE) + 1}</span>
                                        </p>
                                    </div>
                                    <div>
                                        <nav className="relative z-0 inline-flex rounded-md shadow-sm -space-x-px" aria-label="Pagination">
                                            <button
                                                onClick={handlePrevPage}
                                                disabled={!filters.skip || filters.skip === 0}
                                                className="relative inline-flex items-center px-4 py-2 rounded-l-md border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                                            >
                                                Previous
                                            </button>
                                            <button
                                                onClick={handleNextPage}
                                                disabled={logs.length < ITEMS_PER_PAGE}
                                                className="relative inline-flex items-center px-4 py-2 rounded-r-md border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                                            >
                                                Next
                                            </button>
                                        </nav>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

export { AuditLogs };
export default AuditLogs;