import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, X } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { api, unwrap, type Schemas } from '../../api/client';
import { ErrorAlert, Field, Loading } from '../../lib/ui';

type Standard = Schemas['StandardClassOut'];

/**
 * Set up classes from the usual list (Creche to SSS 3). A new school starts with every class
 * ticked and unticks the ones it doesn't run; a school that already has classes sees only the
 * ones it's missing, unticked. Its own extra classes can be added too. Standard classes take
 * their usual place in the promotion order.
 */
export function ClassSetup({ firstTime, onDone }: { firstTime: boolean; onDone?: () => void }) {
  const queryClient = useQueryClient();
  const standard = useQuery({
    queryKey: ['class-levels', 'standard'],
    queryFn: async () => unwrap(await api.GET('/api/v1/class-levels/standard')),
  });
  const [ticked, setTicked] = useState<Set<string> | null>(null);
  const [arms, setArms] = useState('A');
  const [extras, setExtras] = useState<string[]>([]);
  const [extra, setExtra] = useState('');

  const offered = (standard.data ?? []).filter((c) => !c.exists);
  const chosen = ticked ?? new Set(firstTime ? offered.map((c) => c.name) : []);
  const groups = offered.reduce<Record<string, Standard[]>>((acc, c) => ({ ...acc, [c.group]: [...(acc[c.group] ?? []), c] }), {});
  const armNames = arms
    .split(',')
    .map((a) => a.trim())
    .filter(Boolean);
  const count = chosen.size + extras.length;

  const create = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/class-levels/batch', {
          body: { classes: [...offered.filter((c) => chosen.has(c.name)).map((c) => c.name), ...extras].map((name) => ({ name, arms: armNames })) },
        }),
      ),
    onSuccess: (data) => {
      queryClient.setQueryData(['class-levels'], data);
      void queryClient.invalidateQueries({ queryKey: ['class-levels', 'standard'] });
      void queryClient.invalidateQueries({ queryKey: ['onboarding'] });
      onDone?.();
    },
  });

  function toggle(names: string[], on: boolean) {
    const next = new Set(chosen);
    for (const name of names) {
      if (on) next.add(name);
      else next.delete(name);
    }
    setTicked(next);
  }

  function addExtra() {
    const name = extra.trim();
    const taken = [...offered.map((c) => c.name), ...extras, ...(standard.data ?? []).filter((c) => c.exists).map((c) => c.name)];
    if (name && !taken.some((t) => t.toLowerCase() === name.toLowerCase())) setExtras([...extras, name]);
    setExtra('');
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  if (standard.isPending) return <Loading />;
  if (standard.isError) return <ErrorAlert error={standard.error} />;

  return (
    <form onSubmit={submit} aria-label="Set up classes">
      <ErrorAlert error={create.error} />
      {offered.length === 0 ? (
        <p className="muted">You already have every class on the usual list. Add your own below.</p>
      ) : (
        <div className="class-groups">
          {Object.entries(groups).map(([group, classes]) => {
            const all = classes.every((c) => chosen.has(c.name));
            return (
              <fieldset key={group} className="class-group">
                <legend className="row between">
                  <span>{group}</span>
                  <button type="button" className="link small" onClick={() => toggle(classes.map((c) => c.name), !all)}>
                    {all ? 'None' : 'All'}
                  </button>
                </legend>
                {classes.map((c) => (
                  <label key={c.name} className="checkbox">
                    <input type="checkbox" checked={chosen.has(c.name)} onChange={(e) => toggle([c.name], e.target.checked)} />
                    <span>{c.name}</span>
                  </label>
                ))}
              </fieldset>
            );
          })}
        </div>
      )}

      <div className="grid-2 tight">
        <Field label="Your own classes" hint="Anything not on the list, e.g. Year 7 or A-level. They go after the others; you can move them.">
          <div className="row">
            <input
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  addExtra();
                }
              }}
              maxLength={50}
              placeholder="Class name"
            />
            <button type="button" className="secondary" onClick={addExtra} disabled={!extra.trim()}>
              <Plus size={16} /> Add
            </button>
          </div>
        </Field>
        <Field label="Arms in each class" hint="Separate with commas, e.g. A, B or Gold, Diamond. You can change each class later.">
          <input value={arms} onChange={(e) => setArms(e.target.value)} required />
        </Field>
      </div>
      {extras.length > 0 && (
        <div className="row wrap section">
          {extras.map((name) => (
            <span key={name} className="chip">
              {name}
              <button type="button" className="link small" aria-label={`Remove ${name}`} onClick={() => setExtras(extras.filter((x) => x !== name))}>
                <X size={14} />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="row end">
        <button type="submit" disabled={count === 0 || armNames.length === 0 || create.isPending}>
          {count === 1 ? 'Create 1 class' : `Create ${count} classes`}
        </button>
      </div>
    </form>
  );
}
