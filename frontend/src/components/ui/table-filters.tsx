import React from 'react'
import { Search, X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

export interface FilterField {
  key: string
  label: string
  type: 'text' | 'select' | 'search'
  placeholder?: string
  options?: { value: string; label: string }[]
  value: string
  onChange: (value: string) => void
}

interface TableFiltersProps {
  filters: FilterField[]
  onClearFilters: () => void
  hasActiveFilters: boolean
  totalItems: number
  filteredItems: number
  className?: string
}

export function TableFilters({
  filters,
  onClearFilters,
  hasActiveFilters,
  totalItems,
  filteredItems,
  className
}: TableFiltersProps) {
  return (
    <div className={cn("space-y-4 p-4 bg-gray-50 rounded-md border", className)}>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {filters.map((filter) => (
          <div key={filter.key} className="space-y-2">
            <label className="text-sm font-medium">{filter.label}</label>
            {filter.type === 'search' ? (
              <div className="relative">
                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder={filter.placeholder}
                  value={filter.value}
                  onChange={(e) => filter.onChange(e.target.value)}
                  className="pl-10"
                />
              </div>
            ) : filter.type === 'select' ? (
              <Select value={filter.value} onValueChange={filter.onChange}>
                <SelectTrigger>
                  <SelectValue placeholder={filter.placeholder} />
                </SelectTrigger>
                <SelectContent>
                  {filter.options?.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Input
                placeholder={filter.placeholder}
                value={filter.value}
                onChange={(e) => filter.onChange(e.target.value)}
              />
            )}
          </div>
        ))}
      </div>
      <div className="flex justify-between items-center">
        <span className="text-sm text-gray-600">
          Showing {filteredItems} of {totalItems} items
        </span>
        {hasActiveFilters && (
          <Button
            variant="outline"
            size="sm"
            onClick={onClearFilters}
            className="flex items-center gap-2"
          >
            <X className="h-4 w-4" />
            Clear filters
          </Button>
        )}
      </div>
    </div>
  )
}
