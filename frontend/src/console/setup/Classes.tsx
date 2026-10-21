import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { api, unwrap } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { Empty, ErrorAlert, Field, Loading, Modal, PageHeader } from '../../lib/ui';
import { useClassLevels, type ClassLevel } from '../hooks';
import { ClassSetup } from './ClassSetup';

export function Classes() {
  const { can } = useAuth();
  const manage = can('school.manage');
  const levels = useClassLevels();
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [fromList, setFromList] = useState(false);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['class-levels'] });

  const reorder = useMutation({
    mutationFn: async (ids: string[]) => unwrap(await api.PUT('/api/v1/class-levels/order', { body: { class_level_ids: ids } })),
    onSuccess: (data) => queryClient.setQueryData(['class-levels'], data),
  });
  const updateLevel = useMutation({
    mutationFn: async ({ id, body }: { id: string; body: { name?: string; is_active?: boolean } }) =>
      unwrap(await api.PATCH('/api/v1/class-levels/{level_id}', { params: { path: { level_id: id } }, body })),
    onSuccess: refresh,
  });
  const addArm = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }) =>
      unwrap(await api.POST('/api/v1/class-levels/{level_id}/arms', { params: { path: { level_id: id } }, body: { name } })),
    onSuccess: refresh,
  });
  const updateArm = useMutation({
    mutationFn: async ({ id, body }: { id: string; body: { name?: string; is_active?: boolean } }) =>
      unwrap(await api.PATCH('/api/v1/arms/{arm_id}', { params: { path: { arm_id: id } }, body })),
    onSuccess: refresh,
  });

  function move(list: ClassLevel[], index: number, delta: number) {
    const ids = list.map((l) => l.id);
    const [moved] = ids.splice(index, 1);
    ids.splice(index + delta, 0, moved!);
    reorder.mutate(ids);
  }

  function rename(level: ClassLevel) {
    const name = window.prompt('New name for this class', level.name)?.trim();
    if (name && name !== level.name) updateLevel.mutate({ id: level.id, body: { name } });
  }

  function newArm(level: ClassLevel) {
    const name = window.prompt(`Name of the new arm in ${level.name} (e.g. B, Gold, Science)`)?.trim();
    if (name) addArm.mutate({ id: level.id, name });
  }

  return (
    <>
      <PageHeader
        title="Classes"
        subtitle="In promotion order: at the end of a session students move to the next class down this list, and the last class graduates."
        actions={
          manage &&
          (levels.data?.length ?? 0) > 0 && (
            <>
              <button className="secondary" onClick={() => setFromList(true)}>
                Add from the usual list
              </button>
              <button onClick={() => setAdding(true)}>Add class</button>
            </>
          )
        }
      />
      <ErrorAlert error={reorder.error ?? updateLevel.error ?? addArm.error ?? updateArm.error} />
      {levels.isPending ? (
        <Loading />
      ) : levels.isError ? (
        <ErrorAlert error={levels.error} />
      ) : levels.data.length === 0 ? (
        manage ? (
          <section className="card">
            <h2>Set up your classes</h2>
            <p className="muted">
              We've ticked the usual classes, from Creche to SSS 3. Untick any your school doesn't run, add your own, and create them all in one go.
            </p>
            <ClassSetup firstTime />
          </section>
        ) : (
          <Empty>No classes yet. The school admin or proprietor sets them up here.</Empty>
        )
      ) : (
        <div className="card table-card">
          <table>
            <thead>
              <tr>
                <th>Class</th>
                <th>Arms</th>
                {manage && <th aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {levels.data.map((level, index, list) => (
                <tr key={level.id} className={level.is_active ? '' : 'retired'}>
                  <td>
                    <strong>{level.name}</strong> {!level.is_active && <span className="badge">Retired</span>}
                  </td>
                  <td>
                    <div className="row wrap">
                      {level.arms.map((arm) => (
                        <span key={arm.id} className={`chip ${arm.is_active ? '' : 'retired'}`}>
                          {arm.name}
                          {manage && (
                            <button
                              type="button"
                              className="link small"
                              title={arm.is_active ? 'Retire this arm' : 'Bring this arm back'}
                              onClick={() => updateArm.mutate({ id: arm.id, body: { is_active: !arm.is_active } })}
                            >
                              {arm.is_active ? '×' : '↺'}
                            </button>
                          )}
                        </span>
                      ))}
                      {manage && (
                        <button type="button" className="link small" onClick={() => newArm(level)}>
                          + arm
                        </button>
                      )}
                    </div>
                  </td>
                  {manage && (
                    <td className="actions nowrap">
                      <button className="secondary small" aria-label={`Move ${level.name} up`} disabled={index === 0} onClick={() => move(list, index, -1)}>
                        ↑
                      </button>{' '}
                      <button
                        className="secondary small"
                        aria-label={`Move ${level.name} down`}
                        disabled={index === list.length - 1}
                        onClick={() => move(list, index, 1)}
                      >
                        ↓
                      </button>{' '}
                      <button className="secondary small" onClick={() => rename(level)}>
                        Rename
                      </button>{' '}
                      <button className="secondary small" onClick={() => updateLevel.mutate({ id: level.id, body: { is_active: !level.is_active } })}>
                        {level.is_active ? 'Retire' : 'Restore'}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && <AddClass onClose={() => setAdding(false)} />}
      {fromList && (
        <Modal title="Add from the usual list" onClose={() => setFromList(false)}>
          <p className="muted">The usual classes you don't have yet. Each goes into its place in the promotion order.</p>
          <ClassSetup firstTime={false} onDone={() => setFromList(false)} />
        </Modal>
      )}
    </>
  );
}

function AddClass({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [arms, setArms] = useState('A');
  const create = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/class-levels', {
          body: { name, arms: arms.split(',').map((a) => a.trim()).filter(Boolean) },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['class-levels'] });
      onClose();
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <Modal title="Add class" onClose={onClose}>
      <form onSubmit={submit}>
        <p className="muted">New classes go at the end of the list; use the arrows to move them.</p>
        <ErrorAlert error={create.error} />
        <Field label="Class name" hint="For example JSS 1 or Primary 4">
          <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={50} />
        </Field>
        <Field label="Arms" hint="Separate with commas, e.g. A, B or Gold, Diamond">
          <input value={arms} onChange={(e) => setArms(e.target.value)} required />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={create.isPending}>
            Add class
          </button>
        </div>
      </form>
    </Modal>
  );
}
