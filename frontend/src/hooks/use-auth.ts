'use client';

import { create } from 'zustand';

export interface User {
  id: number;
  email: string;
  is_active: boolean;
  is_admin: boolean;
  team_id: number | null;
  role: string | null;
}

interface AuthState {
  user: User | null;
  setUser: (user: User | null) => void;
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  setUser: (user) => set({ user }),
}));

// Helper function to check if a user is a team admin
export const isTeamAdmin = (user: User | null): boolean => {
  if (!user) return false;
  return !user.is_admin && user.team_id !== null && user.role === 'admin';
};