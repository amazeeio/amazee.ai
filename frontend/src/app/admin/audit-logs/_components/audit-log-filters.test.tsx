import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AuditLogFilters } from './audit-log-filters'
import { AuditLogFilters as IFilters } from '@/types/audit-log'

describe('AuditLogFilters', () => {
  const defaultFilters: IFilters = {
    event_type: [],
    resource_type: [],
    status_code: [],
    user_email: ''
  }

  const defaultMetadata = {
    eventTypes: [
      { value: 'CREATE', label: 'Create' },
      { value: 'DELETE', label: 'Delete' }
    ],
    resourceTypes: [
      { value: 'user', label: 'User' },
      { value: 'team', label: 'Team' }
    ],
    statusCodes: [
      { value: '200', label: '200 - OK' },
      { value: '500', label: '500 - Internal Server Error' }
    ]
  }

  const mockOnFilterChange = vi.fn()

  beforeEach(() => {
    mockOnFilterChange.mockClear()
  })

  it('renders all filter labels', () => {
    render(
      <AuditLogFilters
        filters={defaultFilters}
        onFilterChange={mockOnFilterChange}
        metadata={defaultMetadata}
      />
    )

    expect(screen.getByText('Event Type')).toBeInTheDocument()
    expect(screen.getByText('Resource Type')).toBeInTheDocument()
    expect(screen.getByText('Status Code')).toBeInTheDocument()
    expect(screen.getByText('User Email')).toBeInTheDocument()
  })

  it('renders filter card with Filters title', () => {
    render(
      <AuditLogFilters
        filters={defaultFilters}
        onFilterChange={mockOnFilterChange}
        metadata={defaultMetadata}
      />
    )

    expect(screen.getByText('Filters')).toBeInTheDocument()
  })

  it('renders user email input with placeholder', () => {
    render(
      <AuditLogFilters
        filters={defaultFilters}
        onFilterChange={mockOnFilterChange}
        metadata={defaultMetadata}
      />
    )

    const emailInput = screen.getByPlaceholderText('Search by email')
    expect(emailInput).toBeInTheDocument()
  })

  it('renders user email input with current value', () => {
    const filtersWithEmail = {
      ...defaultFilters,
      user_email: 'test@example.com'
    }

    render(
      <AuditLogFilters
        filters={filtersWithEmail}
        onFilterChange={mockOnFilterChange}
        metadata={defaultMetadata}
      />
    )

    const emailInput = screen.getByDisplayValue('test@example.com')
    expect(emailInput).toBeInTheDocument()
  })

  it('calls onFilterChange when user email changes', () => {
    render(
      <AuditLogFilters
        filters={defaultFilters}
        onFilterChange={mockOnFilterChange}
        metadata={defaultMetadata}
      />
    )

    const emailInput = screen.getByPlaceholderText('Search by email')
    fireEvent.change(emailInput, { target: { value: 'new@example.com' } })

    expect(mockOnFilterChange).toHaveBeenCalledWith('user_email', 'new@example.com')
  })
})
