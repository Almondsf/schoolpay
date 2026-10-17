import { useQuery } from '@tanstack/react-query';

import { api, unwrap, type Schemas } from '../api/client';

export type Term = Schemas['TermOut'];
export type AcademicSession = Schemas['AcademicSessionOut'];
export type ClassLevel = Schemas['ClassLevelOut'];

export const useSessions = () =>
  useQuery({ queryKey: ['academic-sessions'], queryFn: async () => unwrap(await api.GET('/api/v1/academic-sessions')) });

export const useCurrentTerm = () =>
  useQuery({ queryKey: ['current-term'], queryFn: async () => unwrap(await api.GET('/api/v1/terms/current')) });

export const useClassLevels = () =>
  useQuery({ queryKey: ['class-levels'], queryFn: async () => unwrap(await api.GET('/api/v1/class-levels')) });

/** "JSS 1 A" style labels for every active arm, for class pickers. */
export function armOptions(levels: ClassLevel[] | undefined) {
  return (levels ?? [])
    .filter((level) => level.is_active)
    .flatMap((level) =>
      level.arms.filter((arm) => arm.is_active).map((arm) => ({ id: arm.id, label: `${level.name} ${arm.name}`, levelId: level.id })),
    );
}

export function termLabel(sessions: AcademicSession[] | undefined, termId: string): string {
  for (const session of sessions ?? []) {
    const term = session.terms.find((t) => t.id === termId);
    if (term) return `${session.name} · ${term.label}`;
  }
  return '';
}
