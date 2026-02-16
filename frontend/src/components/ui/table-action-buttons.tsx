"use client"

import * as React from "react"
import { Button } from "@/components/ui/button"
import { DeleteConfirmationDialog } from "@/components/ui/delete-confirmation-dialog"
import { Pencil } from "lucide-react"

interface TableActionButtonsProps {
  onEdit?: () => void
  onDelete?: () => void
  deleteTitle?: string
  deleteDescription?: string
  deleteConfirmText?: string
  isLoading?: boolean
  isDeleting?: boolean
  disabled?: boolean
  showEdit?: boolean
  showDelete?: boolean
  editText?: string
  deleteText?: string
  className?: string
}

export function TableActionButtons({
  onEdit,
  onDelete,
  deleteTitle = "Are you sure?",
  deleteDescription = "This action cannot be undone.",
  deleteConfirmText = "Delete",
  isLoading = false,
  isDeleting = false,
  disabled = false,
  showEdit = true,
  showDelete = true,
  editText = "Edit",
  deleteText = "Delete",
  className = "space-x-2 flex flex-row gap-2",
}: TableActionButtonsProps) {
  return (
    <div className={className}>
      {showEdit && onEdit && (
        <Button
          variant="outline"
          size="sm"
          onClick={onEdit}
          disabled={disabled || isLoading}
        >
          <Pencil className="h-4 w-4 mr-1" />
          {editText}
        </Button>
      )}
      {showDelete && onDelete && (
        <DeleteConfirmationDialog
          title={deleteTitle}
          description={deleteDescription}
          triggerText={deleteText}
          confirmText={deleteConfirmText}
          onConfirm={onDelete}
          isLoading={isDeleting}
          disabled={disabled}
        />
      )}
    </div>
  )
}
