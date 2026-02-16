import { describe, it, expect } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useAuditLogMetadata } from './use-audit-log-metadata'

describe('useAuditLogMetadata', () => {
  it('returns empty options when metadata is undefined', () => {
    const { result } = renderHook(() => useAuditLogMetadata(undefined))
    expect(result.current.eventTypes).toEqual([])
    expect(result.current.resourceTypes).toEqual([])
    expect(result.current.statusCodes).toEqual([])
  })

  it('returns empty options when metadata is null', () => {
    const { result } = renderHook(() => useAuditLogMetadata(null as any))
    expect(result.current.eventTypes).toEqual([])
    expect(result.current.resourceTypes).toEqual([])
    expect(result.current.statusCodes).toEqual([])
  })

  it('transforms event_types to options with capitalized labels', () => {
    const metadata = {
      event_types: ['CREATE', 'DELETE', 'UPDATE'],
      resource_types: [],
      status_codes: []
    }
    const { result } = renderHook(() => useAuditLogMetadata(metadata))
    
    expect(result.current.eventTypes).toEqual([
      { value: 'CREATE', label: 'Create' },
      { value: 'DELETE', label: 'Delete' },
      { value: 'UPDATE', label: 'Update' }
    ])
  })

  it('transforms resource_types to options with capitalized labels', () => {
    const metadata = {
      event_types: [],
      resource_types: ['user', 'team', 'key'],
      status_codes: []
    }
    const { result } = renderHook(() => useAuditLogMetadata(metadata))
    
    expect(result.current.resourceTypes).toEqual([
      { value: 'user', label: 'User' },
      { value: 'team', label: 'Team' },
      { value: 'key', label: 'Key' }
    ])
  })

  it('transforms status_codes to options with descriptions', () => {
    const metadata = {
      event_types: [],
      resource_types: [],
      status_codes: ['200', '401', '500']
    }
    const { result } = renderHook(() => useAuditLogMetadata(metadata))
    
    expect(result.current.statusCodes).toEqual([
      { value: '200', label: '200 - OK' },
      { value: '401', label: '401 - Unauthorized' },
      { value: '500', label: '500 - Internal Server Error' }
    ])
  })

  it('handles empty arrays in metadata', () => {
    const metadata = {
      event_types: [],
      resource_types: [],
      status_codes: []
    }
    const { result } = renderHook(() => useAuditLogMetadata(metadata))
    
    expect(result.current.eventTypes).toEqual([])
    expect(result.current.resourceTypes).toEqual([])
    expect(result.current.statusCodes).toEqual([])
  })

  it('handles metadata with null values', () => {
    const metadata = {
      event_types: null,
      resource_types: null,
      status_codes: null
    }
    const { result } = renderHook(() => useAuditLogMetadata(metadata as any))
    
    expect(result.current.eventTypes).toEqual([])
    expect(result.current.resourceTypes).toEqual([])
    expect(result.current.statusCodes).toEqual([])
  })
})
